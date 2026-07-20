"""Central configuration loaded from environment / .env / config.yaml.

Priority order (highest → lowest):
1. Environment variables (``MKB_*``)
2. ``.env`` file (infrastructure secrets)
3. ``config.yaml`` (user-tunable settings)
4. Built-in defaults below
"""

from __future__ import annotations

from enum import Enum
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, YamlConfigSettingsSource

# Allow the YAML file location to be overridden via env for testing.
_CONFIG_YAML = Path(__file__).parent.parent.parent / "config.yaml"


class DeploymentMode(str, Enum):
    """Security posture for one MKB process."""

    DEVELOPMENT = "development"
    LOCAL = "local"
    PRODUCTION = "production"


class UnsafeConfigurationError(RuntimeError):
    """Raised when the configured deployment boundary is unsafe."""


class Settings(BaseSettings):
    # ── Deployment boundary ─────────────────────────────────────
    deployment_mode: DeploymentMode = DeploymentMode.DEVELOPMENT
    api_host: str = "127.0.0.1"
    api_port: int = 8503
    cors_origins: list[str] = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
    authentication_enabled: bool = False
    # JSON object supplied through MKB_AUTH_TOKENS, mapping opaque bearer tokens
    # to one of: reader, editor, admin. Never place this in config.yaml.
    auth_tokens: dict[str, str] = Field(default_factory=dict, repr=False)
    # Uploaded Python is equivalent to arbitrary host code execution. This
    # switch is deliberately environment-only and is not exposed in the UI.
    allow_uploaded_python: bool = False

    # ── PostgreSQL ──────────────────────────────────────────────
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_user: str = "mkb"
    pg_password: str = "mkb_dev"
    pg_database: str = "mkb"

    @property
    def pg_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )

    @property
    def pg_dsn_sync(self) -> str:
        return (
            f"postgresql+psycopg://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )

    # ── MinIO / S3 ──────────────────────────────────────────────
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket_raw: str = "raw"
    s3_bucket_processed: str = "processed"
    s3_bucket_archive: str = "archive"
    s3_bucket_temp: str = "temp"

    # Local mirror for processed outputs (organized by batch/asset)
    processed_local_root: str = "data/processed"

    # ── PDF Processing Backend ──────────────────────────────────
    # "local" → local MinerU VLM (default), "mineru_api" → MinerU cloud API
    pdf_backend: str = "local"
    mineru_api_base: str = "https://mineru.net/api/v4"
    mineru_api_token: str = ""
    # vlm | pipeline (only used for mineru_api)
    mineru_api_model_version: str = "vlm"
    mineru_api_language: str = "en"
    mineru_api_enable_ocr: bool = False
    mineru_api_enable_formula: bool = True
    mineru_api_enable_table: bool = True
    # Polling timeout for MinerU API (seconds)
    mineru_api_timeout: int = 600

    # Persisted runtime overrides (written by Settings UI)
    runtime_settings_path: str = "data/runtime_settings.json"

    # ── LLM / Agent ─────────────────────────────────────────────
    extraction_model: str = "openai/qwen-plus"
    # Multimodal model used by vision-capable projection tools.
    # Falls back to extraction_model when empty; set to e.g. "openai/qwen-vl-plus".
    vision_model: str = ""
    google_api_key: str = ""
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_API_KEY", "MKB_LLM_API_KEY"),
    )
    llm_api_base: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_API_BASE", "MKB_LLM_API_BASE"),
    )
    openai_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("OPENAI_API_KEY", "MKB_OPENAI_API_KEY"),
    )
    openai_api_base: str = Field(
        default="",
        validation_alias=AliasChoices("OPENAI_API_BASE", "MKB_OPENAI_API_BASE"),
    )
    # Timeout passed through LiteLLM/httpx for agent model calls.
    agent_llm_timeout: int = 300
    # Retries for transient provider/network issues during agent runs.
    agent_retry_count: int = 5

    # ── Job concurrency ─────────────────────────────────────────
    # Maximum number of background agent jobs (extract/project/etc.) that run
    # concurrently.  Extra jobs are queued and start as slots free up.
    max_concurrent_jobs: int = 3

    # ── UI ──────────────────────────────────────────────────────
    ui_port: int = 8501

    # ── Logging ─────────────────────────────────────────────────
    # "DEBUG" → verbose, includes agent dialogs/tool calls, full MinerU
    # output, and third-party library traces. "INFO" → concise app-level
    # messages only. Logs are written to ``log_dir`` (rotated) and the
    # console.
    log_level: str = "INFO"
    log_dir: str = "logs"
    # Max size of each rolling log file in MB before rotation.
    log_file_max_mb: int = 20
    log_file_backup_count: int = 5

    # ── Upload and archive budgets ──────────────────────────────
    upload_max_file_mb: int = 100
    upload_max_total_mb: int = 500
    upload_max_files: int = 1000
    upload_max_path_depth: int = 12
    archive_max_expanded_mb: int = 500
    archive_max_member_mb: int = 100
    archive_max_members: int = 2000
    archive_max_compression_ratio: int = 100
    archive_max_nesting: int = 3

    rate_limit_upload_per_minute: int = 30
    rate_limit_assistant_per_minute: int = 20
    rate_limit_job_start_per_minute: int = 30
    rate_limit_auth_failures_per_minute: int = 10

    model_config = {"env_prefix": "MKB_", "env_file": ".env", "extra": "ignore"}

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, origins: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in origins:
            origin = str(raw).strip().rstrip("/")
            if origin == "*":
                cleaned.append(origin)
                continue
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"Invalid CORS origin: {raw!r}")
            if parsed.path or parsed.query or parsed.fragment or parsed.username:
                raise ValueError(f"CORS origins must be scheme + authority only: {raw!r}")
            cleaned.append(origin)
        if not cleaned:
            raise ValueError("At least one CORS origin must be configured")
        return list(dict.fromkeys(cleaned))

    @field_validator("auth_tokens")
    @classmethod
    def validate_auth_tokens(cls, tokens: dict[str, str]) -> dict[str, str]:
        valid_roles = {"reader", "editor", "admin"}
        for token, role in tokens.items():
            if len(token) < 32:
                raise ValueError("Authentication tokens must contain at least 32 characters")
            if role not in valid_roles:
                raise ValueError(f"Unknown authentication role: {role!r}")
        return tokens

    @staticmethod
    def _is_loopback_host(host: str) -> bool:
        normalized = host.strip().strip("[]").lower()
        if normalized == "localhost":
            return True
        try:
            return ip_address(normalized).is_loopback
        except ValueError:
            return False

    def startup_issues(
        self,
        *,
        host: str | None = None,
        log_level: str | None = None,
    ) -> tuple[list[str], list[str]]:
        """Return warnings and fatal errors for the effective deployment."""
        warnings: list[str] = []
        errors: list[str] = []
        effective_host = host or self.api_host
        effective_log_level = (log_level or self.log_level).upper()

        is_loopback = self._is_loopback_host(effective_host)
        if not is_loopback and self.deployment_mode is not DeploymentMode.PRODUCTION:
            errors.append(f"API host {effective_host!r} is not loopback in local mode")
        if "*" in self.cors_origins:
            errors.append("Wildcard CORS origins are not allowed")

        default_credentials = []
        if self.pg_password == "mkb_dev":
            default_credentials.append("PostgreSQL")
        if self.s3_access_key == "minioadmin" or self.s3_secret_key == "minioadmin":
            default_credentials.append("MinIO")

        if self.deployment_mode is DeploymentMode.PRODUCTION:
            if default_credentials:
                errors.append(
                    "Production cannot use default credentials for: "
                    + ", ".join(default_credentials)
                )
            if effective_log_level == "DEBUG":
                errors.append("Production cannot run with DEBUG logging")
            if not self.authentication_enabled:
                errors.append("Production requires authentication")
            elif not self.auth_tokens:
                errors.append("Production requires at least one configured authentication token")
            if self.allow_uploaded_python:
                errors.append("Production cannot execute uploaded Python in the API process")
        else:
            if default_credentials:
                warnings.append(
                    "Disposable local-development credentials are active for: "
                    + ", ".join(default_credentials)
                )
            if effective_log_level == "DEBUG":
                warnings.append("DEBUG logging may contain sensitive research or model data")
            if self.allow_uploaded_python:
                warnings.append(
                    "Uploaded Python execution is enabled and has access to host files, "
                    "environment variables, and the network"
                )
        return warnings, errors

    def validate_startup(
        self,
        *,
        host: str | None = None,
        log_level: str | None = None,
    ) -> list[str]:
        warnings, errors = self.startup_issues(host=host, log_level=log_level)
        if errors:
            raise UnsafeConfigurationError("; ".join(errors))
        return warnings

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        **kwargs: Any,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_file = _CONFIG_YAML if _CONFIG_YAML.is_file() else None
        sources: list[Any] = [init_settings, env_settings, dotenv_settings]
        if yaml_file:
            sources.append(YamlConfigSettingsSource(settings_cls, yaml_file=yaml_file))
        return tuple(sources)


settings = Settings()
