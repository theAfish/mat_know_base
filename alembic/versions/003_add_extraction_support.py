"""add extraction support columns

Revision ID: 003
Revises: 002
Create Date: 2026-04-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create ExtractionStatus enum
    extraction_status = sa.Enum(
        "PENDING", "IN_PROGRESS", "COMPLETED", "FAILED",
        name="extraction_status",
        create_type=True,
    )
    extraction_status.create(op.get_bind(), checkfirst=True)

    # Add extraction columns to ingestion_batches
    op.add_column(
        "ingestion_batches",
        sa.Column(
            "extraction_status",
            extraction_status,
            nullable=False,
            server_default="PENDING",
        ),
    )
    op.add_column(
        "ingestion_batches",
        sa.Column("extraction_metadata", JSONB, server_default="{}"),
    )

    # Add source_batch_id to knowledge_nodes
    op.add_column(
        "knowledge_nodes",
        sa.Column("source_batch_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_knowledge_nodes_batch", "knowledge_nodes", ["source_batch_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_nodes_batch", "knowledge_nodes", if_exists=True)
    op.drop_column("knowledge_nodes", "source_batch_id")
    op.drop_column("ingestion_batches", "extraction_metadata")
    op.drop_column("ingestion_batches", "extraction_status")
    sa.Enum(name="extraction_status").drop(op.get_bind(), checkfirst=True)
