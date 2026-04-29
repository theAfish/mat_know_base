"""Lightweight HTTP server for direct file uploads from the browser component.

Bypasses Streamlit's setComponentValue payload-size limit by accepting raw
binary file data via POST.  Runs as a daemon thread alongside Streamlit.
"""

import json
import logging
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

logger = logging.getLogger(__name__)

_UPLOAD_TEMP = Path("data/uploads/_temp")
_UPLOAD_PORT = 8502


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

            content_length = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(content_length)

            dest = _UPLOAD_TEMP / upload_id / relative_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            self._send_json(200, {"ok": True})
            return

        if self.path == "/upload/complete":
            upload_id = self.headers.get("X-Upload-Id", "")
            if not upload_id:
                self._send_json(400, {"error": "missing X-Upload-Id"})
                return
            (_UPLOAD_TEMP / upload_id / ".complete").write_text("done")
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
        if exc.errno == 98:  # EADDRINUSE — already bound in the parent Streamlit process
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