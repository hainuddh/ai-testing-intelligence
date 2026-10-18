"""Persist source lifecycle, focus preferences and isolated discovery trials."""

import sqlalchemy as sa
from alembic import op

revision = "20261017_01_source_exploration"
down_revision = "20261011_03_digest_batches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("digest_batch_endpoints", sa.Column("skip_reason", sa.Text(), nullable=True))
    op.add_column(
        "sources",
        sa.Column("previous_status", sa.String(30), nullable=False, server_default="active"),
    )
    op.create_table(
        "source_exploration_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("topics", sa.JSON(), nullable=False),
        sa.Column("languages", sa.JSON(), nullable=False),
        sa.Column("excluded_terms", sa.JSON(), nullable=False),
        sa.Column("max_trial_sources", sa.Integer(), nullable=False),
        sa.Column("discovery_cursor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_completed_at", sa.DateTime(timezone=True)),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("last_error", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "source_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("homepage_url", sa.String(2048), nullable=False),
        sa.Column("feed_url", sa.String(2048), nullable=False, unique=True),
        sa.Column("topics", sa.JSON(), nullable=False),
        sa.Column("language", sa.String(30), nullable=False),
        sa.Column("discovery_kind", sa.String(30), nullable=False),
        sa.Column("evidence_url", sa.String(2048), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("samples", sa.JSON(), nullable=False),
        sa.Column("seen_urls", sa.JSON(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("relevant_count", sa.Integer(), nullable=False),
        sa.Column("analysis_count", sa.Integer(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("next_check_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column(
            "promoted_source_id", sa.Integer(), sa.ForeignKey("sources.id", ondelete="SET NULL")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_source_candidates_status", "source_candidates", ["status"])
    op.create_index("ix_source_candidates_next_check_at", "source_candidates", ["next_check_at"])
    # Existing configured sources were historically left in draft despite active collection.
    op.execute(
        sa.text(
            "UPDATE sources SET status='active' WHERE status='draft' AND EXISTS "
            "(SELECT 1 FROM source_endpoints WHERE source_endpoints.source_id=sources.id "
            "AND source_endpoints.enabled=true)"
        )
    )


def downgrade() -> None:
    op.drop_table("source_candidates")
    op.drop_table("source_exploration_preferences")
    with op.batch_alter_table("sources") as batch:
        batch.drop_column("previous_status")
    with op.batch_alter_table("digest_batch_endpoints") as batch:
        batch.drop_column("skip_reason")
