"""Add canonical workflows and raw lifecycle metadata.

Revision ID: 0013
Revises: 0012
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("raw_workflow_extractions", sa.Column("record_status", sa.String(32), nullable=False, server_default="active"))
    op.add_column("raw_workflow_extractions", sa.Column("supersedes_extraction_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_table(
        "canonical_workflows",
        sa.Column("canonicalization_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_extraction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("canonicalizer_version", sa.String(64), nullable=False),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="IN_PROGRESS"),
        sa.Column("graph", postgresql.JSONB(), nullable=True),
        sa.Column("provenance", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("canonicalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "version", name="uq_canonical_workflow_project_version"),
    )
    op.create_index("ix_canonical_workflow_project_created", "canonical_workflows", ["project_id", "created_at"])
    op.create_index("ix_canonical_workflow_raw", "canonical_workflows", ["raw_extraction_id"])


def downgrade() -> None:
    op.drop_index("ix_canonical_workflow_raw", table_name="canonical_workflows")
    op.drop_index("ix_canonical_workflow_project_created", table_name="canonical_workflows")
    op.drop_table("canonical_workflows")
    op.drop_column("raw_workflow_extractions", "supersedes_extraction_id")
    op.drop_column("raw_workflow_extractions", "record_status")
