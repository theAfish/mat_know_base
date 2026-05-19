"""Add `purpose` column to spaces.

Lets a Space declare what kind of projection it produces:
- tabular_database (default, current behaviour)
- qa_benchmark
- skill_cards
- freeform

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "spaces",
        sa.Column(
            "purpose",
            sa.String(length=64),
            nullable=False,
            server_default="tabular_database",
        ),
    )


def downgrade() -> None:
    op.drop_column("spaces", "purpose")
