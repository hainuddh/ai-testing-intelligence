"""add github preferences

Revision ID: bd634a654a8a
Revises: 690ce9c38fbf
Create Date: 2026-09-16 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'bd634a654a8a'
down_revision: Union[str, None] = '690ce9c38fbf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('github_preferences',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('topics', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('github_preferences')