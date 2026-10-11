"""Persist daily preparation boundaries and independent delivery queues."""

import sqlalchemy as sa
from alembic import op

revision = "20261011_03_digest_batches"
down_revision = "20261011_02_email_github_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "digest_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_date", sa.String(10), nullable=False, unique=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prepare_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("send_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("sealed", sa.Boolean(), nullable=False),
        sa.Column("required_content_ids", sa.JSON(), nullable=False),
        sa.Column("candidate_content_ids", sa.JSON(), nullable=False),
        sa.Column("report_ids", sa.JSON(), nullable=False),
        sa.Column("github_status", sa.String(30), nullable=False),
        sa.Column("github_report_id", sa.Integer()),
        sa.Column("github_started_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_digest_batches_status", "digest_batches", ["status"])
    op.create_table(
        "digest_batch_endpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("digest_batches.id"), nullable=False),
        sa.Column("endpoint_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer()),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("batch_id", "endpoint_id"),
    )
    op.create_index("ix_digest_batch_endpoints_batch_id", "digest_batch_endpoints", ["batch_id"])
    with op.batch_alter_table("email_digest_deliveries") as batch:
        batch.add_column(sa.Column("batch_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_delivery_batch", "digest_batches", ["batch_id"], ["id"])
    op.add_column(
        "fetch_runs",
        sa.Column("content_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    with op.batch_alter_table("fetch_runs") as batch:
        batch.drop_column("content_ids")
    with op.batch_alter_table("email_digest_deliveries") as batch:
        batch.drop_constraint("fk_delivery_batch", type_="foreignkey")
        batch.drop_column("batch_id")
    op.drop_table("digest_batch_endpoints")
    op.drop_table("digest_batches")
