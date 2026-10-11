"""Keep per-mailbox receipts for GitHub research in combined daily digests."""

import sqlalchemy as sa
from alembic import op

revision = "20261011_02_email_github_items"
down_revision = "20261011_01_email_recipients"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_digest_github_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "delivery_id",
            sa.Integer(),
            sa.ForeignKey("email_digest_deliveries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("repo_key", sa.String(255), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.UniqueConstraint("delivery_id", "repo_key"),
    )
    op.create_index(
        "ix_email_digest_github_items_delivery_id", "email_digest_github_items", ["delivery_id"]
    )
    op.create_index(
        "ix_email_digest_github_items_repo_key", "email_digest_github_items", ["repo_key"]
    )


def downgrade() -> None:
    op.drop_table("email_digest_github_items")
