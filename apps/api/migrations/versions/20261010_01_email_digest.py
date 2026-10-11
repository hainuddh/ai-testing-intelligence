"""Persist daily email subscriptions, report snapshots and delivery receipts."""

import sqlalchemy as sa
from alembic import op

revision = "20261010_01_email_digest"
down_revision = "20261009_01_github_exploration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_digest_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("recipient", sa.String(254), nullable=False),
        sa.Column("value_threshold", sa.Integer(), nullable=False),
        sa.Column("send_time", sa.String(5), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "email_digest_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recipient", sa.String(254), nullable=False),
        sa.Column("business_date", sa.String(10), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("recipient", "business_date"),
    )
    op.create_index(
        "ix_email_digest_deliveries_recipient", "email_digest_deliveries", ["recipient"]
    )
    op.create_index("ix_email_digest_deliveries_status", "email_digest_deliveries", ["status"])
    op.create_table(
        "email_digest_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "delivery_id",
            sa.Integer(),
            sa.ForeignKey("email_digest_deliveries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_id", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.UniqueConstraint("delivery_id", "content_hash"),
    )
    op.create_index("ix_email_digest_items_delivery_id", "email_digest_items", ["delivery_id"])
    op.create_index("ix_email_digest_items_content_hash", "email_digest_items", ["content_hash"])


def downgrade() -> None:
    op.drop_table("email_digest_items")
    op.drop_table("email_digest_deliveries")
    op.drop_table("email_digest_preferences")
