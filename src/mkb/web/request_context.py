"""Request correlation shared by web handlers and background job creation."""

from __future__ import annotations

import contextvars
import logging
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if 0 < len(supplied) <= 128 and supplied.isascii() else str(uuid.uuid4())
        token = request_id_var.set(request_id)
        request.state.request_id = request_id
        started = time.monotonic()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        logging.getLogger("mkb.request").info(
            "request_id=%s method=%s path=%s status=%s duration_ms=%d",
            request_id, request.method, request.url.path, response.status_code,
            int((time.monotonic() - started) * 1000),
        )
        return response
