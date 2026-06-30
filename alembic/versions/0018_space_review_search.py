"""Add space-level projection review search settings.

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("spaces")}

    if "review_allow_search" not in existing_columns:
        op.add_column(
            "spaces",
            sa.Column(
                "review_allow_search",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )

    if "review_search_tools" not in existing_columns:
        op.add_column(
            "spaces",
            sa.Column(
                "review_search_tools",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'[\"web\"]'::jsonb"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("spaces")}

    if "review_search_tools" in existing_columns:
        op.drop_column("spaces", "review_search_tools")
    if "review_allow_search" in existing_columns:
        op.drop_column("spaces", "review_allow_search")
