"""Authentication, authorization, and endpoint-budget contracts."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from mkb.config import Settings
from mkb.web.security import (
    ApiSecurityMiddleware,
    Permission,
    is_job_start_path,
    rate_limiter,
    required_permission,
)

READER_TOKEN = "reader-token-" + "r" * 40
EDITOR_TOKEN = "editor-token-" + "e" * 40
ADMIN_TOKEN = "admin-token-" + "a" * 40


def _secured_app(**overrides) -> FastAPI:
    configured = Settings(
        authentication_enabled=True,
        auth_tokens={
            READER_TOKEN: "reader",
            EDITOR_TOKEN: "editor",
            ADMIN_TOKEN: "admin",
        },
        **overrides,
    )
    app = FastAPI()
    app.add_middleware(ApiSecurityMiddleware, settings=configured)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/items")
    async def read_items():
        return []

    @app.post("/api/items")
    async def create_item():
        return {"ok": True}

    @app.delete("/api/items/one")
    async def delete_item():
        return {"ok": True}

    @app.put("/api/settings")
    async def update_settings():
        return {"ok": True}

    @app.post("/api/upload/init")
    async def begin_upload():
        return {"ok": True}

    @app.post("/api/projects/one/process")
    async def process_project():
        return {"ok": True}

    return app


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_health_is_public_but_api_reads_require_authentication():
    rate_limiter.clear()
    transport = httpx.ASGITransport(app=_secured_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        missing = await client.get("/api/items")
        invalid = await client.get("/api/items", headers=_auth("x" * 64))
        allowed = await client.get("/api/items", headers=_auth(READER_TOKEN))

    assert health.status_code == 200
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert invalid.status_code == 401
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_roles_separate_read_mutate_jobs_settings_and_destructive_actions():
    rate_limiter.clear()
    transport = httpx.ASGITransport(app=_secured_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        reader_mutate = await client.post("/api/items", headers=_auth(READER_TOKEN))
        editor_mutate = await client.post("/api/items", headers=_auth(EDITOR_TOKEN))
        editor_job = await client.post(
            "/api/projects/one/process", headers=_auth(EDITOR_TOKEN)
        )
        editor_delete = await client.delete("/api/items/one", headers=_auth(EDITOR_TOKEN))
        editor_settings = await client.put("/api/settings", headers=_auth(EDITOR_TOKEN))
        admin_delete = await client.delete("/api/items/one", headers=_auth(ADMIN_TOKEN))
        admin_settings = await client.put("/api/settings", headers=_auth(ADMIN_TOKEN))

    assert reader_mutate.status_code == 403
    assert reader_mutate.json()["detail"]["required"] == Permission.MUTATE.value
    assert editor_mutate.status_code == 200
    assert editor_job.status_code == 200
    assert editor_delete.status_code == 403
    assert editor_settings.status_code == 403
    assert admin_delete.status_code == 200
    assert admin_settings.status_code == 200


@pytest.mark.asyncio
async def test_upload_and_job_start_rate_limits_are_enforced_per_token():
    rate_limiter.clear()
    transport = httpx.ASGITransport(app=_secured_app(
        rate_limit_upload_per_minute=1,
        rate_limit_job_start_per_minute=1,
    ))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first_upload = await client.post("/api/upload/init", headers=_auth(ADMIN_TOKEN))
        second_upload = await client.post("/api/upload/init", headers=_auth(ADMIN_TOKEN))
        first_job = await client.post(
            "/api/projects/one/process", headers=_auth(EDITOR_TOKEN)
        )
        second_job = await client.post(
            "/api/projects/one/process", headers=_auth(EDITOR_TOKEN)
        )

    assert first_upload.status_code == 200
    assert second_upload.status_code == 429
    assert second_upload.headers["retry-after"]
    assert first_job.status_code == 200
    assert second_job.status_code == 429


@pytest.mark.asyncio
async def test_repeated_bad_credentials_are_rate_limited():
    rate_limiter.clear()
    transport = httpx.ASGITransport(app=_secured_app(rate_limit_auth_failures_per_minute=1))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/api/items", headers=_auth("x" * 64))
        second = await client.get("/api/items", headers=_auth("y" * 64))

    assert first.status_code == 401
    assert second.status_code == 429
    assert second.json()["detail"]["category"] == "auth"


def test_permission_classifier_does_not_treat_all_project_posts_as_jobs():
    assert required_permission("POST", "/api/project-groups") is Permission.MUTATE
    assert required_permission("POST", "/api/projects/one/process") is Permission.JOB_START
    assert required_permission("DELETE", "/api/spaces/one") is Permission.DESTRUCTIVE
    assert required_permission("POST", "/api/skills/upload") is Permission.CODE_UPLOAD
    assert required_permission("OPTIONS", "/api/settings") is None
    assert is_job_start_path("/api/projects/one/project")
    assert not is_job_start_path("/api/projects")
