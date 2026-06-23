"""Add workflow correction metadata and schema curator storage.

Revision ID: 0014
Revises: 0013
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("raw_workflow_extractions", sa.Column("correction_reason", sa.Text()))
    op.add_column("raw_workflow_extractions", sa.Column("correction_author", sa.String(255)))
    op.add_column("raw_workflow_extractions", sa.Column("correction_details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("raw_workflow_extractions", sa.Column("review_flags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.create_table(
        "workflow_schema_versions",
        sa.Column("schema_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, unique=True),
        sa.Column("name", sa.String(32), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("change_summary", sa.Text()),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "schema_proposals",
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("proposal_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_workflow_ids", postgresql.JSONB(), nullable=False),
        sa.Column("analysis", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("base_schema_version", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("reviewed_by", sa.String(255)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("schema_proposals")
    op.drop_table("workflow_schema_versions")
    for name in ("review_flags", "correction_details", "correction_author", "correction_reason"):
        op.drop_column("raw_workflow_extractions", name)
