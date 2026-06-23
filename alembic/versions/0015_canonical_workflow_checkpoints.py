"""Add canonical workflow checkpoints for resumable drafting.

Revision ID: 0015
Revises: 0014
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("canonical_workflows")}
    if "checkpoint" not in columns:
        op.add_column("canonical_workflows", sa.Column("checkpoint", postgresql.JSONB(), nullable=True))
    if "checkpoint_updated_at" not in columns:
        op.add_column("canonical_workflows", sa.Column("checkpoint_updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("canonical_workflows")}
    if "checkpoint_updated_at" in columns:
        op.drop_column("canonical_workflows", "checkpoint_updated_at")
    if "checkpoint" in columns:
        op.drop_column("canonical_workflows", "checkpoint")
