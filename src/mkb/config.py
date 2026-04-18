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

    # ── LLM / Agent ─────────────────────────────────────────────
    extraction_model: str = "openai/qwen-plus"
    google_api_key: str = ""
    openai_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("OPENAI_API_KEY", "MKB_OPENAI_API_KEY"),
    )
    openai_api_base: str = Field(
        default="",
        validation_alias=AliasChoices("OPENAI_API_BASE", "MKB_OPENAI_API_BASE"),
    )

    # ── UI ──────────────────────────────────────────────────────
    ui_port: int = 8501

    model_config = {"env_prefix": "MKB_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
