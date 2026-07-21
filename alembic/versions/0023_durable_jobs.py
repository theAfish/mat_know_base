"""Add durable background jobs.

Revision ID: 0023_durable_jobs
Revises: 0022_schema_drift_cleanup
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0023_durable_jobs"
down_revision = "0022_schema_drift_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("project_id", sa.String(64)),
        sa.Column("request_id", sa.String(128)),
        sa.Column("idempotency_key", sa.String(255), unique=True),
        sa.Column("active_key", sa.String(255), unique=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("current_message", sa.Text(), nullable=False, server_default="Queued"),
        sa.Column("events", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column("error_category", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_background_jobs_status_updated", "background_jobs", ["status", "updated_at"])
    op.create_index("ix_background_jobs_project_kind", "background_jobs", ["project_id", "kind"])


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT count(*) FROM background_jobs")).scalar_one()
    if count:
        raise RuntimeError(
            "Refusing to drop populated background_jobs during the preservation window"
        )
    op.drop_table("background_jobs")
