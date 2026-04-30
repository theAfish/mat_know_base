"""Lightweight HTTP server for direct file uploads from the browser component.

Bypasses Streamlit's setComponentValue payload-size limit by accepting raw
binary file data via POST.  Runs as a daemon thread alongside Streamlit.
"""

import errno
import json
import logging
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

logger = logging.getLogger(__name__)

_UPLOAD_TEMP = Path("data/uploads/_temp")
_UPLOAD_PORT = 8502
_MAX_UPLOAD_BYTES = 512 * 1024 * 1024  # 512 MiB per file
_CHUNK_SIZE = 64 * 1024  # 64 KiB read chunks


def _validate_upload_id(upload_id: str) -> str:
    """Validate and return a canonical UUID string.

    Parses *upload_id* through :class:`uuid.UUID` so the return value is
    always a stdlib-generated canonical string, breaking taint chains for
    static-analysis tools.  Raises ``ValueError`` for non-UUID values.
    """
    try:
        return str(uuid.UUID(upload_id))
    except ValueError:
        raise ValueError(f"Invalid upload_id: {upload_id!r}")


def _safe_upload_path(relative_path: str, base: Path) -> Path:
    """Return a resolved Path guaranteed to sit inside *base*.

    Raises ``ValueError`` for absolute paths or any path whose resolved
    location escapes *base*.
    """
    p = Path(relative_path)
    if p.is_absolute():
        raise ValueError(f"Absolute path rejected: {relative_path!r}")
    resolved = (base / p).resolve()
    base_resolved = base.resolve()
    # relative_to raises ValueError if resolved is outside base_resolved
    try:
        resolved.relative_to(base_resolved)
    except ValueError:
        raise ValueError(f"Path traversal rejected: {relative_path!r}")
    return resolved


class _UploadHandler(BaseHTTPRequestHandler):
    """Handles POST requests from the drop-zone component."""

    # ── CORS preflight ──────────────────────────────────────────────

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    # ── Upload endpoints ────────────────────────────────────────────

    def do_POST(self):
        if self.path == "/upload/init":
            upload_id = str(uuid.uuid4())
            (_UPLOAD_TEMP / upload_id).mkdir(parents=True, exist_ok=True)
            self._send_json(200, {"upload_id": upload_id})
            return

        if self.path == "/upload/file":
            upload_id = self.headers.get("X-Upload-Id", "")
            relative_path = self.headers.get("X-Relative-Path", "")
            if not upload_id or not relative_path:
                self._send_json(400, {"error": "missing X-Upload-Id or X-Relative-Path"})
                return

            try:
                safe_id = _validate_upload_id(upload_id)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return

            cl_header = self.headers.get("Content-Length")
            if cl_header is None:
                self._send_json(411, {"error": "Content-Length required"})
                return
            try:
                content_length = int(cl_header)
            except ValueError:
                self._send_json(400, {"error": "invalid Content-Length"})
                return
            if content_length < 0:
                self._send_json(400, {"error": "invalid Content-Length"})
                return
            if content_length > _MAX_UPLOAD_BYTES:
                self._send_json(413, {"error": "file too large"})
                return

            session_base = _UPLOAD_TEMP / safe_id
            try:
                dest = _safe_upload_path(relative_path, session_base)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return

            dest.parent.mkdir(parents=True, exist_ok=True)
            remaining = content_length
            with dest.open("wb") as fh:
                while remaining > 0:
                    chunk = self.rfile.read(min(_CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    fh.write(chunk)
                    remaining -= len(chunk)
            if remaining != 0:
                dest.unlink(missing_ok=True)
                self._send_json(400, {"error": "incomplete upload: received fewer bytes than Content-Length specified"})
                return
            self._send_json(200, {"ok": True})
            return

        if self.path == "/upload/complete":
            upload_id = self.headers.get("X-Upload-Id", "")
            if not upload_id:
                self._send_json(400, {"error": "missing X-Upload-Id"})
                return
            try:
                safe_id = _validate_upload_id(upload_id)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            (_UPLOAD_TEMP / safe_id / ".complete").write_text("done")
            self._send_json(200, {"ok": True})
            return

        self._send_json(404, {"error": "not found"})

    # ── Helpers ────────────────────────────────────────────────────

    def _cors_headers(self):
        """Must be called AFTER send_response(), BEFORE end_headers()."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-Upload-Id, X-Relative-Path, X-File-Name",
        )

    def _send_json(self, status: int, body: dict):
        """Write a complete JSON response: status → CORS → content-type → body."""
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, format, *args):
        logger.debug(format, *args)


_server_started = False


def ensure_upload_server(port: int = _UPLOAD_PORT) -> None:
    """Start the upload server once (idempotent).  Safe to call on every rerun."""
    global _server_started
    if _server_started:
        return
    _UPLOAD_TEMP.mkdir(parents=True, exist_ok=True)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), _UploadHandler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:  # already bound in the parent Streamlit process
            _server_started = True
            return
        raise
    Thread(target=server.serve_forever, daemon=True).start()
    _server_started = True
    logger.info("Upload server listening on http://127.0.0.1:%d", port)


def get_upload_url(port: int = _UPLOAD_PORT) -> str:
    return f"http://127.0.0.1:{port}"


def session_dir(upload_id: str) -> Path:
    return _UPLOAD_TEMP / upload_id