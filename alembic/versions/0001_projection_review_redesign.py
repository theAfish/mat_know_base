"""Projection review redesign: add review fields to projections, drop reviewed_projections.

Revision ID: 0001
Revises:
Create Date: 2026-04-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add review-related columns to projections table
    op.add_column(
        "projections",
        sa.Column("times_reviewed", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "projections",
        sa.Column("review_notes", sa.Text(), nullable=True),
    )
    op.add_column(
        "projections",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "projections",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_projection_deleted_at", "projections", ["deleted_at"])

    # Drop the reviewed_projections table
    op.drop_index("ix_reviewed_projection_space_project", table_name="reviewed_projections")
    op.drop_table("reviewed_projections")


def downgrade() -> None:
    # Recreate reviewed_projections table
    op.create_table(
        "reviewed_projections",
        sa.Column("reviewed_projection_id", sa.UUID(), primary_key=True),
        sa.Column("space_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("frame_id", sa.UUID(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING", "IN_PROGRESS", "COMPLETED", "FAILED",
                "NEEDS_FEEDBACK", "REVIEWED",
                name="projection_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("validation_result", sa.JSON(), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("source_projection_ids", sa.JSON(), nullable=True),
        sa.Column("space_version", sa.Integer(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_reviewed_projection_space_project",
        "reviewed_projections",
        ["space_id", "project_id"],
    )

    # Remove added columns from projections
    op.drop_index("ix_projection_deleted_at", table_name="projections")
    op.drop_column("projections", "deleted_at")
    op.drop_column("projections", "reviewed_at")
    op.drop_column("projections", "review_notes")
    op.drop_column("projections", "times_reviewed")
