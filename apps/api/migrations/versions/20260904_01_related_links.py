"""Add related links extracted from high-value content.

Revision ID: 20260904_01_related_links
Revises: 20260903_01_title_index
"""

import sqlalchemy as sa
from alembic import op

revision = "20260904_01_related_links"
down_revision = "20260903_01_title_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "content_items",
        sa.Column("related_links", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "content_items",
        sa.Column("related_links_extracted_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_column("content_items", "related_links_extracted_at")
    op.drop_column("content_items", "related_links")
