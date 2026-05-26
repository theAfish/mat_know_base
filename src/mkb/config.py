"""Central configuration loaded from environment / .env file."""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
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
    openai_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("OPENAI_API_KEY", "MKB_OPENAI_API_KEY"),
    )
    openai_api_base: str = Field(
        default="",
        validation_alias=AliasChoices("OPENAI_API_BASE", "MKB_OPENAI_API_BASE"),
    )

    # ── Job concurrency ─────────────────────────────────────────
    # Maximum number of background agent jobs (extract/project/etc.) that run
    # concurrently.  Extra jobs are queued and start as slots free up.
    max_concurrent_jobs: int = 3

    # ── UI ──────────────────────────────────────────────────────
    ui_port: int = 8501

    model_config = {"env_prefix": "MKB_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
