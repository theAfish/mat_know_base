"""Add project_groups table and research_projects.group_id.

Lets users aggregate research projects under named groups (e.g. by topic
or material field). Each project may belong to at most one group; groups
can be folded/unfolded in the UI and operated on as a unit.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "project_groups" not in insp.get_table_names():
        op.create_table(
            "project_groups",
            sa.Column("group_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("color", sa.String(length=32), nullable=True),
            sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )

    rp_cols = {c["name"] for c in insp.get_columns("research_projects")}
    if "group_id" not in rp_cols:
        op.add_column(
            "research_projects",
            sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True),
        )

    rp_indexes = {i["name"] for i in insp.get_indexes("research_projects")}
    if "ix_research_projects_group_id" not in rp_indexes:
        op.create_index(
            "ix_research_projects_group_id",
            "research_projects",
            ["group_id"],
        )

    rp_fks = {fk["name"] for fk in insp.get_foreign_keys("research_projects")}
    if "fk_research_projects_group_id" not in rp_fks:
        op.create_foreign_key(
            "fk_research_projects_group_id",
            "research_projects",
            "project_groups",
            ["group_id"],
            ["group_id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if "research_projects" in tables:
        foreign_keys = {fk["name"] for fk in insp.get_foreign_keys("research_projects")}
        if "fk_research_projects_group_id" in foreign_keys:
            op.drop_constraint(
                "fk_research_projects_group_id", "research_projects", type_="foreignkey"
            )
        indexes = {index["name"] for index in insp.get_indexes("research_projects")}
        if "ix_research_projects_group_id" in indexes:
            op.drop_index("ix_research_projects_group_id", table_name="research_projects")
        columns = {column["name"] for column in insp.get_columns("research_projects")}
        if "group_id" in columns:
            op.drop_column("research_projects", "group_id")
    if "project_groups" in tables:
        op.drop_table("project_groups")
