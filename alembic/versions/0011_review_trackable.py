"""Add review_trackable to spaces, supersession columns to projections.

- ``spaces.review_trackable`` (bool, default true): when true, running review
  preserves prior projection rows by creating a new REVIEWED projection that
  *supersedes* them; when false, the legacy behaviour (in-place winner
  update + soft-delete losers) applies.
- ``projections.superseded_by_id`` (uuid, nullable): points to the newer
  projection that replaced this one.
- ``projections.supersedes_ids`` (jsonb, nullable): list of older
  projection_ids that this reviewed projection consolidated.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "spaces",
        sa.Column(
            "review_trackable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "projections",
        sa.Column(
            "superseded_by_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "projections",
        sa.Column(
            "supersedes_ids",
            postgresql.JSONB(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_projection_superseded_by",
        "projections",
        ["superseded_by_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_projection_superseded_by", table_name="projections")
    op.drop_column("projections", "supersedes_ids")
    op.drop_column("projections", "superseded_by_id")
    op.drop_column("spaces", "review_trackable")
