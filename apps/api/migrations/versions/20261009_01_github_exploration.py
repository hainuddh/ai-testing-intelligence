"""Persist automatic GitHub exploration and report publication identity."""

import sqlalchemy as sa
from alembic import op

revision = "20261009_01_github_exploration"
down_revision = "20260924_01_merge_github_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("github_repos", sa.Column("github_id", sa.BigInteger(), nullable=True))
    op.create_index("uq_github_repos_github_id", "github_repos", ["github_id"], unique=True)
    op.add_column("github_reports", sa.Column("report_key", sa.String(64), nullable=True))
    op.create_index("uq_github_reports_report_key", "github_reports", ["report_key"], unique=True)
    op.create_table(
        "github_exploration_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.String(64), nullable=True),
        sa.Column("discovered_count", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("github_reports.id"), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("search_cursors", sa.JSON(), nullable=False),
    )
    op.execute(
        "INSERT INTO github_exploration_state "
        "(id, status, discovered_count, search_cursors) "
        "VALUES (1, 'never_run', 0, '{}')"
    )


def downgrade() -> None:
    op.drop_table("github_exploration_state")
    op.drop_index("uq_github_reports_report_key", table_name="github_reports")
    op.drop_column("github_reports", "report_key")
    op.drop_index("uq_github_repos_github_id", table_name="github_repos")
    op.drop_column("github_repos", "github_id")
