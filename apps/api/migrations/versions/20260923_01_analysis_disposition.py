"""Separate analysis completion from radar disposition.

Revision ID: 20260923_01_analysis_disposition
Revises: 20260904_01_related_links
"""

import sqlalchemy as sa
from alembic import op

revision = "20260923_01_analysis_disposition"
down_revision = "20260904_01_related_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "content_items",
        sa.Column("analysis_disposition", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "content_items",
        sa.Column("filter_reason", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_content_items_analysis_disposition",
        "content_items",
        ["analysis_disposition"],
    )
    op.execute(
        """
        UPDATE content_items
        SET analysis_status = 'analyzed',
            analysis_disposition = 'filtered',
            filter_reason = 'legacy_filtered'
        WHERE analysis_status = 'filtered'
        """
    )
    op.execute(
        """
        UPDATE content_items
        SET analysis_disposition = CASE
            WHEN testing_relevance_score >= 60 AND testing_value_score >= 60 THEN 'radar'
            ELSE 'watch'
        END
        WHERE analysis_status = 'analyzed' AND analysis_disposition IS NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE content_items
        SET analysis_status = 'filtered'
        WHERE analysis_disposition = 'filtered'
        """
    )
    op.drop_index("ix_content_items_analysis_disposition", table_name="content_items")
    op.drop_column("content_items", "filter_reason")
    op.drop_column("content_items", "analysis_disposition")
