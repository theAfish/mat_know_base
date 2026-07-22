"""Persistent runtime settings overrides.

Most settings come from environment variables (see ``mkb.config``).  A few
settings — notably the PDF processing backend and MinerU API credentials — are
also exposed via the Web UI so end-users can change them without restarting the
server.  Those overrides are persisted to a JSON file (``runtime_settings.json``)
and merged on top of the environment-based defaults on every read.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from mkb.config import settings

# Keys that can be modified through the Settings UI / API.
# Limiting the surface area avoids clients accidentally rewriting
# infrastructure secrets like DB passwords.
ALLOWED_KEYS: set[str] = {
    "pdf_backend",
    "mineru_api_base",
    "mineru_api_token",
    "mineru_api_model_version",
    "mineru_api_language",
    "mineru_api_enable_ocr",
    "mineru_api_enable_formula",
    "mineru_api_enable_table",
    "mineru_api_timeout",
    # LLM / Agent
    "extraction_model",
    "vision_model",
    "agent_llm_timeout",
    "agent_retry_count",
    # System
    "log_level",
    "max_concurrent_jobs",
}

# Keys that should be masked when sent to the frontend.
SECRET_KEYS: set[str] = {"mineru_api_token"}

_lock = threading.Lock()


def _settings_path() -> Path:
    return Path(settings.runtime_settings_path).resolve()


def _load_raw() -> dict[str, Any]:
    path = _settings_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_raw(data: dict[str, Any]) -> None:
    """Atomically replace the settings file with owner-only permissions."""
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def get_overrides() -> dict[str, Any]:
    """Return the raw persisted overrides (no defaults)."""
    with _lock:
        return _load_raw()


def get_effective_settings() -> dict[str, Any]:
    """Defaults from environment + any persisted overrides."""
    overrides = get_overrides()
    result: dict[str, Any] = {}
    for key in ALLOWED_KEYS:
        result[key] = overrides.get(key, getattr(settings, key))
    return result


def get_setting(key: str) -> Any:
    if key not in ALLOWED_KEYS:
        raise KeyError(f"Unknown runtime setting: {key}")
    overrides = get_overrides()
    if key in overrides:
        return overrides[key]
    return getattr(settings, key)


def update_settings(updates: dict[str, Any]) -> dict[str, Any]:
    """Persist a partial update.  Unknown / disallowed keys are rejected."""
    unknown = [k for k in updates if k not in ALLOWED_KEYS]
    if unknown:
        raise ValueError(f"Unknown settings: {unknown}")

    # Light validation
    backend = updates.get("pdf_backend")
    if backend is not None and backend not in {"local", "mineru_api"}:
        raise ValueError(f"Invalid pdf_backend: {backend!r}")

    log_level = updates.get("log_level")
    if log_level is not None and log_level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ValueError(f"Invalid log_level: {log_level!r}")
    if (
        log_level is not None
        and settings.deployment_mode.value == "production"
        and log_level.upper() == "DEBUG"
    ):
        raise ValueError("DEBUG logging is forbidden in production")

    max_jobs = updates.get("max_concurrent_jobs")
    if max_jobs is not None and (not isinstance(max_jobs, int) or max_jobs < 1):
        raise ValueError(f"max_concurrent_jobs must be a positive integer, got {max_jobs!r}")

    agent_llm_timeout = updates.get("agent_llm_timeout")
    if agent_llm_timeout is not None and (
        not isinstance(agent_llm_timeout, int) or agent_llm_timeout < 1
    ):
        raise ValueError(
            f"agent_llm_timeout must be a positive integer, got {agent_llm_timeout!r}"
        )

    agent_retry_count = updates.get("agent_retry_count")
    if agent_retry_count is not None and (
        not isinstance(agent_retry_count, int) or agent_retry_count < 1
    ):
        raise ValueError(
            f"agent_retry_count must be a positive integer, got {agent_retry_count!r}"
        )

    with _lock:
        current = _load_raw()
        for key, value in updates.items():
            # Strip empty strings so they fall back to env defaults
            if isinstance(value, str) and value == "":
                current.pop(key, None)
            else:
                current[key] = value
        _save_raw(current)
    return get_effective_settings()


def public_view(values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mask secret fields for client consumption."""
    data = dict(values) if values is not None else get_effective_settings()
    for key in SECRET_KEYS:
        if key in data:
            val = data[key]
            data[key] = "" if not val else "********"
            data[f"{key}_set"] = bool(val)
    data["deployment_mode"] = settings.deployment_mode.value
    data["allow_uploaded_python"] = settings.allow_uploaded_python
    return data
