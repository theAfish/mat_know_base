"""Add workflow maintenance queue and retrieval indexes.

Revision ID: 0016
Revises: 0015
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("workflow_maintenance_tasks"):
        op.create_table(
            "workflow_maintenance_tasks",
            sa.Column("task_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("task_type", sa.String(32), nullable=False),
            sa.Column("reason", sa.String(64), nullable=False),
            sa.Column("source_raw_extraction_id", postgresql.UUID(as_uuid=True)),
            sa.Column("source_canonicalization_id", postgresql.UUID(as_uuid=True)),
            sa.Column("target_schema_version", sa.String(32)),
            sa.Column("scope", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("requested_by", sa.String(255), nullable=False),
            sa.Column("result", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("error", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("started_at", sa.DateTime(timezone=True)),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
        )
    maintenance_indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("workflow_maintenance_tasks")}
    if "ix_workflow_maintenance_status" not in maintenance_indexes:
        op.create_index("ix_workflow_maintenance_status", "workflow_maintenance_tasks", ["status", "task_type", "created_at"])
    if "ix_workflow_maintenance_project" not in maintenance_indexes:
        op.create_index("ix_workflow_maintenance_project", "workflow_maintenance_tasks", ["project_id", "created_at"])
    if not inspector.has_table("workflow_index_entries"):
        op.create_table(
            "workflow_index_entries",
            sa.Column("entry_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("canonicalization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("index_type", sa.String(32), nullable=False),
            sa.Column("source_label", sa.Text(), nullable=False),
            sa.Column("target_label", sa.Text(), nullable=False),
            sa.Column("operation_label", sa.Text()),
            sa.Column("source_schema", sa.String(64)),
            sa.Column("target_schema", sa.String(64)),
            sa.Column("operation_template_id", sa.Text()),
            sa.Column("aliases", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("granularity_terms", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("path_node_ids", postgresql.JSONB(), nullable=False),
            sa.Column("raw_edge_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    workflow_indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("workflow_index_entries")}
    if "ix_workflow_index_direct" not in workflow_indexes:
        op.create_index("ix_workflow_index_direct", "workflow_index_entries", ["index_type", "source_label", "target_label"])
    if "ix_workflow_index_template" not in workflow_indexes:
        op.create_index("ix_workflow_index_template", "workflow_index_entries", ["source_schema", "operation_template_id", "target_schema"])
    if "ix_workflow_index_canonical" not in workflow_indexes:
        op.create_index("ix_workflow_index_canonical", "workflow_index_entries", ["canonicalization_id"])
    if "ix_workflow_index_aliases" not in workflow_indexes:
        op.create_index(
            "ix_workflow_index_aliases", "workflow_index_entries", ["aliases"],
            postgresql_using="gin",
        )
    if "ix_workflow_index_granularity" not in workflow_indexes:
        op.create_index(
            "ix_workflow_index_granularity", "workflow_index_entries", ["granularity_terms"],
            postgresql_using="gin",
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("workflow_index_entries"):
        op.drop_table("workflow_index_entries")
    if inspector.has_table("workflow_maintenance_tasks"):
        op.drop_table("workflow_maintenance_tasks")
