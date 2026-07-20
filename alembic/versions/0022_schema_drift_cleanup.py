"""Move remaining runtime compatibility changes into Alembic.

Revision ID: 0022_schema_drift_cleanup
Revises: 0021_post_processor_scripts
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0022_schema_drift_cleanup"
down_revision = "0021_post_processor_scripts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    raw_columns = {
        column["name"] for column in inspector.get_columns("raw_workflow_extractions")
    }
    if "checkpoint" not in raw_columns:
        op.add_column(
            "raw_workflow_extractions",
            sa.Column("checkpoint", postgresql.JSONB(), nullable=True),
        )
    if "checkpoint_updated_at" not in raw_columns:
        op.add_column(
            "raw_workflow_extractions",
            sa.Column("checkpoint_updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    skill_columns = {
        column["name"]: column for column in inspector.get_columns("custom_skills")
    }
    for name in ("created_at", "updated_at"):
        if skill_columns[name]["nullable"]:
            op.alter_column(
                "custom_skills",
                name,
                existing_type=sa.DateTime(timezone=True),
                nullable=False,
            )

    project_fks = {
        fk["name"] for fk in inspector.get_foreign_keys("research_projects")
    }
    if "fk_research_projects_group_id" in project_fks:
        op.drop_constraint(
            "fk_research_projects_group_id",
            "research_projects",
            type_="foreignkey",
        )


def downgrade() -> None:
    op.create_foreign_key(
        "fk_research_projects_group_id",
        "research_projects",
        "project_groups",
        ["group_id"],
        ["group_id"],
        ondelete="SET NULL",
    )
    for name in ("updated_at", "created_at"):
        op.alter_column(
            "custom_skills",
            name,
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
    op.drop_column("raw_workflow_extractions", "checkpoint_updated_at")
    op.drop_column("raw_workflow_extractions", "checkpoint")
