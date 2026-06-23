"""Add editable schema proposal drafts and immutable revision history.

Revision ID: 0017
Revises: 0016
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    proposal_columns = {
        column["name"] for column in inspector.get_columns("schema_proposals")
    }
    if "rationale" not in proposal_columns:
        op.add_column("schema_proposals", sa.Column("rationale", sa.Text()))
    if "reviewer_notes" not in proposal_columns:
        op.add_column("schema_proposals", sa.Column("reviewer_notes", sa.Text()))
    if not inspector.has_table("schema_proposal_revisions"):
        op.create_table(
            "schema_proposal_revisions",
            sa.Column("revision_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("revision_number", sa.Integer(), nullable=False),
            sa.Column("payload", postgresql.JSONB(), nullable=False),
            sa.Column("evidence_workflow_ids", postgresql.JSONB(), nullable=False),
            sa.Column("analysis", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("rationale", sa.Text()),
            sa.Column("author", sa.String(255), nullable=False),
            sa.Column("author_type", sa.String(32), nullable=False),
            sa.Column("change_note", sa.Text()),
            sa.Column("validation_errors", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("proposal_id", "revision_number", name="uq_schema_proposal_revision"),
        )
    indexes = {
        item["name"] for item in sa.inspect(op.get_bind()).get_indexes("schema_proposal_revisions")
    }
    if "ix_schema_proposal_revision_history" not in indexes:
        op.create_index(
            "ix_schema_proposal_revision_history",
            "schema_proposal_revisions",
            ["proposal_id", "revision_number"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("schema_proposal_revisions"):
        op.drop_table("schema_proposal_revisions")
    columns = {column["name"] for column in inspector.get_columns("schema_proposals")}
    if "reviewer_notes" in columns:
        op.drop_column("schema_proposals", "reviewer_notes")
    if "rationale" in columns:
        op.drop_column("schema_proposals", "rationale")
