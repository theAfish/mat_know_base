"""Add optional ``review_prompt`` column to spaces.

Lets each space override the projection-reviewer prompt. When NULL the
reviewer falls back to a default selected by ``purpose``.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "spaces",
        sa.Column("review_prompt", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("spaces", "review_prompt")
