"""Add composite index for content filtering performance.

Revision ID: 20260831_01_content_filter_index
Revises: 20260830_02
"""

from alembic import op

revision = "20260831_01_content_filter_index"
down_revision = "20260830_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_content_items_analysis_filter",
        "content_items",
        ["analysis_status", "testing_relevance_score", "testing_value_score"],
    )


def downgrade() -> None:
    op.drop_index("ix_content_items_analysis_filter", table_name="content_items")
