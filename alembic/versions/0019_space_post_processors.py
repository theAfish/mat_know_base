"""Add space post processor profiles.

Revision ID: 0019_space_post_processors
Revises: 0018
Create Date: 2026-06-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0019_space_post_processors"
down_revision = "0018"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "post_processors" not in _columns("spaces"):
        op.add_column(
            "spaces",
            sa.Column(
                "post_processors",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )


def downgrade() -> None:
    if "post_processors" in _columns("spaces"):
        op.drop_column("spaces", "post_processors")
