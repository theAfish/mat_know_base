"""MinerU cloud API backend for PDF processing.

Implements the "Precision Extract Batch" flow documented at
https://mineru.net/apiManage/docs:

    1. POST /api/v4/file-urls/batch  → batch_id + pre-signed file_urls
    2. PUT each file_urls[i]         → uploads raw PDF bytes (no auth, no CT)
    3. GET /api/v4/extract-results/batch/{batch_id} → poll until done
    4. GET full_zip_url              → download zip with full.md + images/

The result is normalised into a :class:`ProcessingResult` matching the local
backend so callers (coordinator, link_manual_processed_data, etc.) do not need
to care which backend produced the output.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from mkb.processors.base import ProcessingResult, TextualProcessor

logger = logging.getLogger(__name__)


class MinerUApiError(RuntimeError):
    pass


def _http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500] if e.fp else ""
        raise MinerUApiError(f"HTTP {e.code} on {method} {url}: {detail}") from e
    except urllib.error.URLError as e:
        raise MinerUApiError(f"Network error on {method} {url}: {e}") from e
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise MinerUApiError(f"Non-JSON response from {url}: {raw[:200]!r}") from e


def _http_put_bytes(url: str, data: bytes, timeout: int = 300) -> None:
    """Upload raw bytes to a MinerU pre-signed URL.

    Uses ``requests`` so we get a clean PUT without urllib's default
    ``Accept-Encoding``/``User-Agent``/``Connection`` headers, which can
    cause the OSS signature check to fail (403 Forbidden).

    Per the MinerU docs, the request must NOT set ``Content-Type``.
    """
    try:
        # `requests` sends Content-Length but not Content-Type when `data` is bytes.
        resp = requests.put(url, data=data, timeout=timeout)
    except requests.RequestException as e:
        raise MinerUApiError(f"Upload network error: {e}") from e
    if resp.status_code >= 400:
        body = (resp.text or "")[:300]
        raise MinerUApiError(
            f"Upload failed ({resp.status_code}) for {urlparse(url).path}: {body}"
        )


def _http_get_bytes(url: str, timeout: int = 300) -> bytes:
    try:
        resp = requests.get(url, timeout=timeout, stream=False)
    except requests.RequestException as e:
        raise MinerUApiError(f"Download network error: {e}") from e
    if resp.status_code >= 400:
        raise MinerUApiError(f"Download failed ({resp.status_code}) for {url}")
    return resp.content


class MinerUApiPDFProcessor(TextualProcessor):
    """PDF → Markdown via the MinerU cloud API."""

    supported_mime_types = ["application/pdf"]

    def __init__(
        self,
        *,
        api_base: str,
        token: str,
        model_version: str = "vlm",
        language: str = "en",
        enable_ocr: bool = False,
        enable_formula: bool = True,
        enable_table: bool = True,
        timeout: int = 600,
    ) -> None:
        if not token:
            raise ValueError("MinerU API token is required")
        self.api_base = api_base.rstrip("/")
        self.token = token
        self.model_version = model_version
        self.language = language
        self.enable_ocr = enable_ocr
        self.enable_formula = enable_formula
        self.enable_table = enable_table
        self.timeout = timeout

    def can_process(self, mime_type: str, filename: str) -> bool:
        return mime_type in self.supported_mime_types or filename.lower().endswith(".pdf")

    # ─────────────────────────────────────────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _request_upload_url(self, filename: str) -> tuple[str, str]:
        payload = {
            "enable_formula": self.enable_formula,
            "enable_table": self.enable_table,
            "language": self.language,
            "model_version": self.model_version,
            "is_ocr": self.enable_ocr,
            "files": [{"name": filename, "is_ocr": self.enable_ocr, "data_id": hashlib.sha256(filename.encode()).hexdigest()}],
        }
        resp = _http_json(
            f"{self.api_base}/file-urls/batch",
            method="POST",
            headers=self._auth_headers(),
            body=payload,
            timeout=60,
        )
        if resp.get("code") != 0:
            raise MinerUApiError(f"MinerU API error: {resp}")
        data = resp.get("data") or {}
        batch_id = data.get("batch_id")
        file_urls = data.get("file_urls") or []
        if not batch_id or not file_urls:
            raise MinerUApiError(f"Unexpected MinerU API response: {resp}")
        return batch_id, file_urls[0]

    def _poll_until_done(self, batch_id: str) -> str:
        deadline = time.monotonic() + self.timeout
        delay = 3.0
        url = f"{self.api_base}/extract-results/batch/{batch_id}"
        while True:
            resp = _http_json(url, headers=self._auth_headers(), timeout=60)
            if resp.get("code") != 0:
                raise MinerUApiError(f"MinerU poll error: {resp}")
            results = (resp.get("data") or {}).get("extract_result") or []
            if not results:
                raise MinerUApiError(f"MinerU poll returned no result entries: {resp}")
            entry = results[0]
            state = entry.get("state")
            if state == "done":
                zip_url = entry.get("full_zip_url")
                if not zip_url:
                    raise MinerUApiError(f"MinerU done but no zip: {entry}")
                return zip_url
            if state == "failed":
                msg = entry.get("err_msg") or entry.get("data_id") or "unknown"
                raise MinerUApiError(f"MinerU extraction failed: {msg}")
            if time.monotonic() >= deadline:
                raise MinerUApiError(
                    f"MinerU extraction timed out after {self.timeout}s "
                    f"(last state: {state})"
                )
            time.sleep(delay)
            delay = min(delay * 1.5, 15.0)

    # ─────────────────────────────────────────────────────────────────────────

    def process(self, data: bytes, filename: str) -> ProcessingResult:
        try:
            return self._process(data, filename)
        except Exception as e:  # noqa: BLE001
            logger.warning("MinerU API processing failed for %s: %s", filename, e)
            return ProcessingResult(
                processing_type=self.get_processing_type(),
                output_format="md",
                content=b"",
                error=f"MinerU API processing failed: {e}",
            )

    def _process(self, data: bytes, filename: str) -> ProcessingResult:
        stem = Path(filename).stem

        # 1+2. Get upload URL and PUT bytes
        batch_id, upload_url = self._request_upload_url(filename)
        _http_put_bytes(upload_url, data, timeout=max(60, self.timeout))

        # 3. Poll until done
        zip_url = self._poll_until_done(batch_id)

        # 4. Download zip and unpack
        zip_bytes = _http_get_bytes(zip_url, timeout=max(60, self.timeout))
        markdown_content: str | None = None
        artifacts: dict[str, bytes] = {}
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            md_name: str | None = None
            for name in zf.namelist():
                # MinerU zip typically contains `full.md` (and images/, *.json)
                if name.endswith("/") or name.startswith("__MACOSX/"):
                    continue
                if md_name is None and name.lower().endswith(".md"):
                    md_name = name
            if md_name is None:
                raise MinerUApiError("MinerU result zip contained no .md file")
            markdown_content = zf.read(md_name).decode("utf-8", errors="replace")
            for name in zf.namelist():
                if name.endswith("/") or name.startswith("__MACOSX/") or name == md_name:
                    continue
                # Keep images and structural JSON as artifacts (relative paths)
                artifacts[name] = zf.read(name)

        image_count = len([k for k in artifacts if k.lower().startswith("images/")])
        return ProcessingResult(
            processing_type=self.get_processing_type(),
            output_format="md",
            content=markdown_content.encode("utf-8"),
            artifacts=artifacts,
            primary_relpath=f"{stem}.md",
            conversion_metadata={
                "method": f"mineru-api-{self.model_version}",
                "batch_id": batch_id,
                "image_files": image_count,
                "has_figures": image_count > 0,
                "language": self.language,
            },
        )
