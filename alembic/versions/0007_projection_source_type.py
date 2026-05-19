"""Add `source_type` column to projections.

Lets a Projection declare whether its data was extracted from the
agent-curated KnowledgeFrame ("frame", the default and legacy behaviour)
or directly from the project's processed Markdown ("markdown").

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projections",
        sa.Column(
            "source_type",
            sa.String(length=32),
            nullable=False,
            server_default="frame",
        ),
    )


def downgrade() -> None:
    op.drop_column("projections", "source_type")
