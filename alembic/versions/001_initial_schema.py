"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-04-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── assets ──────────────────────────────────────────────────
    processing_status = sa.Enum(
        "PENDING", "STORED", "EXTRACTED", "GRAPHED", "FAILED",
        name="processing_status",
        create_type=True,
    )

    op.create_table(
        "assets",
        sa.Column("asset_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("sha256", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column("filename", sa.Text, nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("s3_bucket", sa.String(63), nullable=False),
        sa.Column("s3_key", sa.Text, nullable=False),
        sa.Column(
            "status", processing_status, nullable=False, server_default="PENDING"
        ),
        sa.Column("metadata", JSONB, server_default="{}"),
        sa.Column("embedding", Vector(1536), nullable=True),
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

    # ── ingestion_batches ───────────────────────────────────────
    op.create_table(
        "ingestion_batches",
        sa.Column("batch_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("label", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # ── batch_assets ────────────────────────────────────────────
    op.create_table(
        "batch_assets",
        sa.Column("batch_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_id", UUID(as_uuid=True), primary_key=True),
    )

    # ── knowledge_nodes ─────────────────────────────────────────
    op.create_table(
        "knowledge_nodes",
        sa.Column("node_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("properties", JSONB, server_default="{}"),
        sa.Column("source_asset_id", UUID(as_uuid=True), nullable=True),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # ── knowledge_edges ─────────────────────────────────────────
    op.create_table(
        "knowledge_edges",
        sa.Column("edge_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_node_id", UUID(as_uuid=True), nullable=False),
        sa.Column("target_node_id", UUID(as_uuid=True), nullable=False),
        sa.Column("relation_type", sa.String(100), nullable=False),
        sa.Column("properties", JSONB, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_edges_source", "knowledge_edges", ["source_node_id"])
    op.create_index("ix_edges_target", "knowledge_edges", ["target_node_id"])
    op.create_index("ix_edges_relation", "knowledge_edges", ["relation_type"])


def downgrade() -> None:
    op.drop_table("knowledge_edges")
    op.drop_table("knowledge_nodes")
    op.drop_table("batch_assets")
    op.drop_table("ingestion_batches")
    op.drop_table("assets")
    sa.Enum(name="processing_status").drop(op.get_bind(), checkfirst=True)
    op.execute("DROP EXTENSION IF EXISTS vector")
