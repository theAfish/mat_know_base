"""Central authentication, authorization, and lightweight abuse controls."""

from __future__ import annotations

import hmac
import hashlib
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from mkb.config import Settings


class Permission(str, Enum):
    READ = "read"
    MUTATE = "mutate"
    DESTRUCTIVE = "destructive"
    SETTINGS = "settings"
    JOB_START = "job-start"
    CODE_UPLOAD = "code-upload"


ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "reader": frozenset({Permission.READ}),
    "editor": frozenset({Permission.READ, Permission.MUTATE, Permission.JOB_START}),
    "admin": frozenset(Permission),
}

PUBLIC_PATHS = frozenset({
    "/health",
    "/health/live",
    "/health/ready",
    "/api/health",
    "/api/ready",
})
CODE_UPLOAD_PATHS = frozenset({
    "/api/skills/upload",
    "/api/post-processor-scripts/upload",
})
DESTRUCTIVE_POST_PATHS = frozenset({"/api/graph/clear", "/api/jobs/cancel-all"})
JOB_ACTION_NAMES = frozenset({
    "process",
    "extract",
    "project",
    "kg-extract",
    "workflow-extract",
    "workflow-reextract",
    "workflow-recanonicalize",
})
EXACT_JOB_PATHS = frozenset({
    "/api/assistant/chat",
    "/api/feedback/review",
    "/api/graph/review",
    "/api/projections/review",
    "/api/upload/ingest",
    "/api/workflow-schema/curate",
    "/api/workflow-maintenance-batch/recanonicalize",
})


@dataclass(frozen=True)
class Principal:
    actor: str
    role: str
    permissions: frozenset[Permission]


def required_permission(method: str, path: str) -> Permission | None:
    if path in PUBLIC_PATHS or method == "OPTIONS":
        return None
    if path.startswith("/api/settings"):
        return Permission.SETTINGS
    if path in CODE_UPLOAD_PATHS:
        return Permission.CODE_UPLOAD
    if method == "DELETE" or path in DESTRUCTIVE_POST_PATHS:
        return Permission.DESTRUCTIVE
    if method in {"GET", "HEAD"}:
        return Permission.READ
    if method == "POST" and is_job_start_path(path):
        return Permission.JOB_START
    return Permission.MUTATE


def is_job_start_path(path: str) -> bool:
    if path in EXACT_JOB_PATHS:
        return True
    if path.startswith("/api/workflow-maintenance/") and path.endswith("/run"):
        return True
    return path.startswith("/api/projects/") and path.rsplit("/", 1)[-1] in JOB_ACTION_NAMES


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, category: str, identity: str, limit: int, now: float | None = None) -> int:
        current = time.monotonic() if now is None else now
        cutoff = current - 60.0
        key = (category, identity)
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= max(1, limit):
                return max(1, int(60 - (current - events[0])))
            events.append(current)
        return 0

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


rate_limiter = SlidingWindowLimiter()


def rate_limit_category(path: str) -> str | None:
    if (
        path.startswith("/api/upload")
        or path.endswith("/processed-upload")
        or path in CODE_UPLOAD_PATHS
    ):
        return "upload"
    if path.startswith("/api/assistant"):
        return "assistant"
    if is_job_start_path(path):
        return "job-start"
    return None


class ApiSecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, settings: Settings):
        super().__init__(app)
        self.settings = settings

    def _authenticate(self, request: Request) -> Principal | None:
        if not self.settings.authentication_enabled:
            return Principal("local-user", "admin", ROLE_PERMISSIONS["admin"])
        scheme, _, supplied = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not supplied:
            return None
        for configured, role in self.settings.auth_tokens.items():
            if hmac.compare_digest(supplied, configured):
                token_id = hashlib.sha256(configured.encode("utf-8")).hexdigest()[:12]
                return Principal(f"token:{token_id}", role, ROLE_PERMISSIONS[role])
        return None

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        permission = required_permission(request.method, request.url.path)
        if permission is None:
            return await call_next(request)

        principal = self._authenticate(request)
        if principal is None:
            client_host = request.client.host if request.client else "unknown"
            retry_after = rate_limiter.check(
                "auth",
                client_host,
                self.settings.rate_limit_auth_failures_per_minute,
            )
            if retry_after:
                return JSONResponse(
                    {"detail": {"code": "rate_limit_exceeded", "category": "auth"}},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
            return JSONResponse(
                {"detail": {"code": "authentication_required", "message": "Valid bearer token required"}},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        if permission not in principal.permissions:
            return JSONResponse(
                {"detail": {"code": "permission_denied", "required": permission.value}},
                status_code=403,
            )
        request.state.principal = principal

        category = rate_limit_category(request.url.path)
        if category:
            limit = {
                "upload": self.settings.rate_limit_upload_per_minute,
                "assistant": self.settings.rate_limit_assistant_per_minute,
                "job-start": self.settings.rate_limit_job_start_per_minute,
            }[category]
            client_host = request.client.host if request.client else "unknown"
            identity = principal.actor if self.settings.authentication_enabled else client_host
            retry_after = rate_limiter.check(category, identity, limit)
            if retry_after:
                return JSONResponse(
                    {"detail": {"code": "rate_limit_exceeded", "category": category}},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
        return await call_next(request)
