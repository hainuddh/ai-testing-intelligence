"""Merge GitHub tracking and analysis disposition migration branches."""

import sqlalchemy as sa
from alembic import op

revision = "20260924_01_merge_github_tracking"
down_revision = ("20260923_01_analysis_disposition", "baa79d86eefc")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This published revision has 33 characters; Alembic creates VARCHAR(32).
    # Widen before Alembic records the merged head. SQLite ignores string lengths.
    if op.get_context().dialect.name == "postgresql":
        op.alter_column(
            "alembic_version",
            "version_num",
            existing_type=sa.String(length=32),
            type_=sa.String(length=64),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Alembic still needs the current 33-character value until downgrade finishes.
    pass
