"""Persist bounded web discovery and distinguish articles from subscription trials."""

import sqlalchemy as sa
from alembic import op

revision = "20261018_01_web_discovery"
down_revision = "20261017_01_source_exploration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_candidates",
        sa.Column("endpoint_type", sa.String(30), nullable=False, server_default="rss"),
    )
    op.add_column(
        "source_candidates",
        sa.Column("check_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "source_candidates",
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "source_discovery_pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("url_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("evidence_url", sa.String(2048), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("topics", sa.JSON(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_check_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
    )
    op.create_index("ix_source_discovery_pages_status", "source_discovery_pages", ["status"])
    op.create_index(
        "ix_source_discovery_pages_next_check_at", "source_discovery_pages", ["next_check_at"]
    )


def downgrade() -> None:
    op.drop_table("source_discovery_pages")
    with op.batch_alter_table("source_candidates") as batch:
        batch.drop_column("success_count")
        batch.drop_column("check_count")
        batch.drop_column("endpoint_type")
