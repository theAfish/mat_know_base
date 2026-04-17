"""add knowledge base frame table

Revision ID: 004
Revises: 003
Create Date: 2026-04-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_base_frames",
        sa.Column("frame_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("batch_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="DRAFT"),
        sa.Column("frame_data", JSONB, server_default="{}"),
        sa.Column("frame_metadata", JSONB, server_default="{}"),
        sa.Column("check_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_kb_frames_batch", "knowledge_base_frames", ["batch_id"], if_not_exists=True)
    op.create_index("ix_kb_frames_status", "knowledge_base_frames", ["status"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_kb_frames_status", "knowledge_base_frames", if_exists=True)
    op.drop_index("ix_kb_frames_batch", "knowledge_base_frames", if_exists=True)
    op.drop_table("knowledge_base_frames")
