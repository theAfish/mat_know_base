"""Add agent_annotations column to knowledge_frames.

Stores persistent agent memory: clarification Q&A history and resolved
feedback items, so agents don't re-ask the same questions on re-runs.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_frames",
        sa.Column("agent_annotations", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_frames", "agent_annotations")
