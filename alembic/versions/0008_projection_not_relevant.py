"""Add NOT_RELEVANT value to projection_status enum.

When the projection agent determines that the source paper has no relevant
data for the space's domain, it marks the projection as NOT_RELEVANT instead
of extracting empty data.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-19
"""

from alembic import op


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE projection_status ADD VALUE IF NOT EXISTS 'NOT_RELEVANT'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op.
    pass
