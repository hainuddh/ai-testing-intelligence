"""Move subscriptions and leases from the singleton to managed recipient mailboxes."""

import sqlalchemy as sa
from alembic import op

revision = "20261011_01_email_recipients"
down_revision = "20261010_01_email_digest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_digest_recipients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    # INSERT ... SELECT also works in PostgreSQL's offline migration generation.
    op.execute(
        sa.text(
            "INSERT INTO email_digest_recipients "
            "(email, name, enabled, started_at, created_at, lease_token, lease_until) "
            "SELECT lower(trim(recipient)), '', true, started_at, "
            "COALESCE(started_at, CURRENT_TIMESTAMP), lease_token, lease_until "
            "FROM email_digest_preferences WHERE trim(recipient) <> ''"
        )
    )
    with op.batch_alter_table("email_digest_preferences") as batch:
        batch.drop_column("recipient")
        batch.drop_column("started_at")
        batch.drop_column("lease_token")
        batch.drop_column("lease_until")


def downgrade() -> None:
    with op.batch_alter_table("email_digest_preferences") as batch:
        batch.add_column(sa.Column("recipient", sa.String(254), nullable=False, server_default=""))
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("lease_token", sa.String(36), nullable=True))
        batch.add_column(sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    # The old schema supports one mailbox; retain the first active one on rollback.
    op.execute(
        sa.text(
            "UPDATE email_digest_preferences SET "
            "recipient = COALESCE((SELECT email FROM email_digest_recipients "
            "ORDER BY enabled DESC, id LIMIT 1), ''), "
            "started_at = (SELECT started_at FROM email_digest_recipients "
            "ORDER BY enabled DESC, id LIMIT 1), "
            "enabled = enabled AND EXISTS "
            "(SELECT 1 FROM email_digest_recipients WHERE enabled = true)"
        )
    )
    op.drop_table("email_digest_recipients")
