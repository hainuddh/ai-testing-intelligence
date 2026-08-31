"""Add query performance indexes for listing endpoints.

Revision ID: 20260831_02_query_indexes
Revises: 20260831_01_content_filter_index
"""

from alembic import op

revision = "20260831_02_query_indexes"
down_revision = "20260831_01_content_filter_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_content_items_ranking",
        "content_items",
        ["analysis_status", "testing_value_score", "fetched_at", "id"],
    )
    op.create_index(
        "ix_content_items_status_fetched",
        "content_items",
        ["analysis_status", "fetched_at", "id"],
    )
    op.create_index(
        "ix_content_items_status_next_analysis",
        "content_items",
        ["analysis_status", "next_analysis_at"],
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_content_items_title_trgm"
            " ON content_items USING gin (title gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_content_items_summary_trgm"
            " ON content_items USING gin (summary gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_content_items_body_trgm"
            " ON content_items USING gin (body gin_trgm_ops)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_content_items_body_trgm")
        op.execute("DROP INDEX IF EXISTS ix_content_items_summary_trgm")
        op.execute("DROP INDEX IF EXISTS ix_content_items_title_trgm")
    op.drop_index("ix_content_items_status_next_analysis", table_name="content_items")
    op.drop_index("ix_content_items_status_fetched", table_name="content_items")
    op.drop_index("ix_content_items_ranking", table_name="content_items")
