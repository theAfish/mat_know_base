"""add processed assets layer

Revision ID: 002
Revises: 001
Create Date: 2026-04-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create ProcessingType enum
    processing_type = sa.Enum(
        "MARKDOWN", "DATAFRAME", "IMAGE", "UNPROCESSABLE",
        name="processing_type",
        create_type=True,
    )

    # ── processed_assets ───────────────────────────────────────
    op.create_table(
        "processed_assets",
        sa.Column("processed_asset_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_id", UUID(as_uuid=True), nullable=False),
        sa.Column("processing_type", processing_type, nullable=False),
        sa.Column("output_format", sa.String(50), nullable=False),
        sa.Column("s3_bucket", sa.String(63), nullable=False),
        sa.Column("s3_key", sa.Text, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("conversion_metadata", JSONB, server_default="{}"),
        sa.Column("raw_asset_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_processed_asset_id", "processed_assets", ["asset_id"], if_not_exists=True)
    op.create_index("ix_processed_sha256", "processed_assets", ["sha256"], if_not_exists=True)

    # ── processing_logs ────────────────────────────────────────
    op.create_table(
        "processing_logs",
        sa.Column("log_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_id", UUID(as_uuid=True), nullable=False),
        sa.Column("processing_type", processing_type, nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("processed_asset_id", UUID(as_uuid=True), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("details", JSONB, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_processing_logs_asset_id", "processing_logs", ["asset_id"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_processing_logs_asset_id", "processing_logs", if_exists=True)
    op.drop_table("processing_logs")
    op.drop_index("ix_processed_sha256", "processed_assets", if_exists=True)
    op.drop_index("ix_processed_asset_id", "processed_assets", if_exists=True)
    op.drop_table("processed_assets")
    sa.Enum(name="processing_type").drop(op.get_bind(), checkfirst=True)
