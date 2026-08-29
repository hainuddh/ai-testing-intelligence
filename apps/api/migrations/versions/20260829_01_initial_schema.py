"""Create the initial application schema.

Revision ID: 20260829_01
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "20260829_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if "users" not in existing:
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("username", sa.String(100), nullable=False),
            sa.Column("password_hash", sa.String(255), nullable=False),
            sa.Column("role", sa.String(30), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_users_username", "users", ["username"], unique=True)

    if "sources" not in existing:
        op.create_table(
            "sources",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("source_type", sa.String(50), nullable=False),
            sa.Column("homepage_url", sa.String(2048)),
            sa.Column("description", sa.Text()),
            sa.Column("languages", sa.JSON(), nullable=False),
            sa.Column("trust_level", sa.Integer(), nullable=False),
            sa.Column("topics", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("health_status", sa.String(30), nullable=False),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_sources_name", "sources", ["name"], unique=True)
        op.create_index("ix_sources_source_type", "sources", ["source_type"])
        op.create_index("ix_sources_status", "sources", ["status"])
        op.create_index("ix_sources_health_status", "sources", ["health_status"])

    if "source_endpoints" not in existing:
        op.create_table(
            "source_endpoints",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("endpoint_type", sa.String(30), nullable=False),
            sa.Column("url", sa.String(2048), nullable=False),
            sa.Column("fetch_interval_minutes", sa.Integer(), nullable=False),
            sa.Column("max_items_per_run", sa.Integer(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("health_status", sa.String(30), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_source_endpoints_source_id", "source_endpoints", ["source_id"])
        op.create_index("ix_source_endpoints_endpoint_type", "source_endpoints", ["endpoint_type"])

    if "content_items" not in existing:
        op.create_table(
            "content_items",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=False),
            sa.Column("title", sa.String(500), nullable=False),
            sa.Column("url", sa.String(2048), nullable=False),
            sa.Column("url_hash", sa.String(64), nullable=False),
            sa.Column("summary", sa.Text()),
            sa.Column("body", sa.Text()),
            sa.Column("published_at", sa.DateTime(timezone=True)),
            sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_content_items_source_id", "content_items", ["source_id"])
        op.create_index("ix_content_items_url_hash", "content_items", ["url_hash"], unique=True)
        op.create_index("ix_content_items_published_at", "content_items", ["published_at"])
        op.create_index("ix_content_items_fetched_at", "content_items", ["fetched_at"])

    if "fetch_runs" not in existing:
        op.create_table(
            "fetch_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "endpoint_id",
                sa.Integer(),
                sa.ForeignKey("source_endpoints.id"),
                nullable=False,
            ),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("items_created", sa.Integer(), nullable=False),
            sa.Column("error", sa.Text()),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
        )
        op.create_index("ix_fetch_runs_endpoint_id", "fetch_runs", ["endpoint_id"])
        op.create_index("ix_fetch_runs_status", "fetch_runs", ["status"])
        op.create_index("ix_fetch_runs_started_at", "fetch_runs", ["started_at"])


def downgrade() -> None:
    pass
