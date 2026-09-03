"""Index normalized content titles for duplicate detection.

Revision ID: 20260903_01_normalized_title_index
Revises: 20260831_02_query_indexes
"""

from alembic import op

revision = "20260903_01_normalized_title_index"
down_revision = "20260831_02_query_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_content_items_normalized_title "
        "ON content_items (lower(trim(title)))"
    )


def downgrade() -> None:
    op.drop_index("ix_content_items_normalized_title", table_name="content_items")
