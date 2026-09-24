"""add github preferences

Revision ID: bd634a654a8a
Revises: 690ce9c38fbf
Create Date: 2026-09-16 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "bd634a654a8a"
down_revision: str | None = "690ce9c38fbf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "github_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("topics", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("github_preferences")
