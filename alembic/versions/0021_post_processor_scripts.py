"""Add deterministic post-processor scripts.

Revision ID: 0021_post_processor_scripts
Revises: 0020_custom_skills
Create Date: 2026-07-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0021_post_processor_scripts"
down_revision = "0020_custom_skills"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "post_processor_scripts",
        sa.Column("script_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("post_processor_scripts")