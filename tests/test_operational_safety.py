"""Regression tests for the localhost-only operational boundary."""

from __future__ import annotations

import io
import logging
import stat
import zipfile

import pytest

from mkb.config import Settings, UnsafeConfigurationError
from mkb.logging_setup import RedactingFormatter, redact_sensitive
from mkb.web.uploads import (
    UploadBudgetExceeded,
    expand_temp_dir,
    write_stream_bounded,
)


def test_default_boundary_is_loopback_with_exact_cors_origins():
    configured = Settings()

    assert configured.api_host == "127.0.0.1"
    assert "*" not in configured.cors_origins
    assert all(origin.startswith("http://") for origin in configured.cors_origins)
    assert configured.log_level == "INFO"
    assert configured.allow_uploaded_python is False
    configured.validate_startup()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"api_host": "0.0.0.0"}, "not loopback"),
        ({"cors_origins": ["*"]}, "Wildcard CORS"),
        (
            {
                "deployment_mode": "production",
                "pg_password": "production-password",
                "s3_access_key": "production-access",
                "s3_secret_key": "production-secret",
            },
            "Production requires authentication",
        ),
        (
            {"deployment_mode": "production", "authentication_enabled": True},
            "at least one configured authentication token",
        ),
    ],
)
def test_unsafe_deployment_overrides_fail_closed(overrides, message):
    with pytest.raises(UnsafeConfigurationError, match=message):
        Settings(**overrides).validate_startup()


def test_persisted_debug_override_cannot_bypass_production_boundary():
    configured = Settings(
        deployment_mode="production",
        pg_password="production-password",
        s3_access_key="production-access",
        s3_secret_key="production-secret",
    )

    with pytest.raises(UnsafeConfigurationError, match="DEBUG logging"):
        configured.validate_startup(log_level="DEBUG")


def test_authenticated_production_can_bind_remotely_with_safe_configuration():
    configured = Settings(
        deployment_mode="production",
        api_host="0.0.0.0",
        authentication_enabled=True,
        auth_tokens={"a" * 64: "admin"},
        pg_password="production-password",
        s3_access_key="production-access",
        s3_secret_key="production-secret",
    )

    configured.validate_startup()


@pytest.mark.asyncio
async def test_cors_preflight_accepts_local_origin_and_rejects_unknown_origin():
    import httpx
    from mkb.web.api_server import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        allowed = await client.options(
            "/api/health",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        rejected = await client.options(
            "/api/health",
            headers={
                "Origin": "https://attacker.invalid",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers


def test_log_redaction_removes_credentials_and_signed_url_values():
    fake_secret = "recognizable-fake-secret-123"
    raw = (
        f"Authorization: Bearer {fake_secret} password={fake_secret} "
        f"https://user:{fake_secret}@example.invalid/file"
        f"?X-Amz-Signature={fake_secret}"
    )

    assert fake_secret not in redact_sensitive(raw)
    formatter = RedactingFormatter("%(message)s")
    record = logging.LogRecord("test", logging.INFO, __file__, 1, raw, (), None)
    assert fake_secret not in formatter.format(record)

    dictionary_style = repr({"Authorization": f"Bearer {fake_secret}", "api_key": fake_secret})
    assert fake_secret not in redact_sensitive(dictionary_style)


def test_runtime_settings_are_replaced_atomically_with_owner_only_permissions(tmp_path, monkeypatch):
    from mkb import runtime_settings

    path = tmp_path / "runtime_settings.json"
    monkeypatch.setattr(runtime_settings.settings, "runtime_settings_path", str(path))

    runtime_settings.update_settings({"mineru_api_token": "recognizable-fake-token"})

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".runtime_settings.json.*"))


def test_stream_budget_removes_partial_file(tmp_path):
    target = tmp_path / "partial.bin"

    with pytest.raises(UploadBudgetExceeded):
        write_stream_bounded(target, io.BytesIO(b"x" * 11), max_bytes=10)

    assert not target.exists()


def test_zip_bomb_is_rejected_and_partial_expansion_is_cleaned(tmp_path, monkeypatch):
    monkeypatch.setattr("mkb.config.settings.archive_max_compression_ratio", 2)
    root = tmp_path / "session"
    root.mkdir()
    archive = root / "bomb.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("large.txt", b"A" * 10_000)

    result = expand_temp_dir(root)

    assert result["extracted"] == []
    assert result["failed"]
    assert "compression ratio" in result["failed"][0]["error"]
    assert not (root / "bomb").exists()


def test_duplicate_archive_names_are_rejected(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    archive = root / "duplicate.zip"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("same.txt", b"first")
            zf.writestr("same.txt", b"second")

    result = expand_temp_dir(root)

    assert result["extracted"] == []
    assert "duplicate member" in result["failed"][0]["error"]
    assert not (root / "duplicate").exists()


def test_uploaded_python_is_disabled_by_default():
    from mkb.post_processors.registry import run_script

    with pytest.raises(PermissionError, match="disabled"):
        run_script(
            {"script_id": "11111111-1111-1111-1111-111111111111"},
            {"project": {}},
        )
