"""Add testing intelligence analysis fields.

Revision ID: 20260830_02
Revises: 20260829_01
"""

import sqlalchemy as sa
from alembic import op

revision = "20260830_02"
down_revision = "20260829_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "content_items",
        sa.Column("analysis_status", sa.String(30), nullable=False, server_default="pending"),
    )
    op.add_column(
        "content_items",
        sa.Column("analysis_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("content_items", sa.Column("testing_relevance_score", sa.Integer()))
    op.add_column("content_items", sa.Column("testing_value_score", sa.Integer()))
    op.add_column("content_items", sa.Column("analysis_summary", sa.Text()))
    op.add_column("content_items", sa.Column("testing_value_analysis", sa.Text()))
    op.add_column(
        "content_items",
        sa.Column("applicable_scenarios", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "content_items",
        sa.Column("adoption_suggestions", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "content_items",
        sa.Column("analysis_risks", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "content_items",
        sa.Column("analysis_tags", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column("content_items", sa.Column("analysis_model", sa.String(200)))
    op.add_column("content_items", sa.Column("analysis_error", sa.Text()))
    op.add_column("content_items", sa.Column("analyzed_at", sa.DateTime(timezone=True)))
    op.add_column("content_items", sa.Column("next_analysis_at", sa.DateTime(timezone=True)))
    op.create_index("ix_content_items_analysis_status", "content_items", ["analysis_status"])
    op.create_index(
        "ix_content_items_testing_relevance_score",
        "content_items",
        ["testing_relevance_score"],
    )
    op.create_index(
        "ix_content_items_testing_value_score", "content_items", ["testing_value_score"]
    )
    op.create_index("ix_content_items_next_analysis_at", "content_items", ["next_analysis_at"])


def downgrade() -> None:
    op.drop_index("ix_content_items_next_analysis_at", table_name="content_items")
    op.drop_index("ix_content_items_testing_value_score", table_name="content_items")
    op.drop_index("ix_content_items_testing_relevance_score", table_name="content_items")
    op.drop_index("ix_content_items_analysis_status", table_name="content_items")
    for column in (
        "analyzed_at",
        "next_analysis_at",
        "analysis_error",
        "analysis_model",
        "analysis_tags",
        "analysis_risks",
        "adoption_suggestions",
        "applicable_scenarios",
        "testing_value_analysis",
        "analysis_summary",
        "testing_value_score",
        "testing_relevance_score",
        "analysis_attempts",
        "analysis_status",
    ):
        op.drop_column("content_items", column)
