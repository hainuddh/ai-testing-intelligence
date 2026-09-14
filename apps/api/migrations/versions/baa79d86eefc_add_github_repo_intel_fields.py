"""add github repo intel fields

Revision ID: baa79d86eefc
Revises: bd634a654a8a
Create Date: 2026-09-16 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'baa79d86eefc'
down_revision: Union[str, None] = 'bd634a654a8a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('github_repos', sa.Column('intel_summary', sa.Text(), nullable=True))
    op.add_column('github_repos', sa.Column('testing_value_analysis', sa.Text(), nullable=True))
    op.add_column('github_repos', sa.Column('applicable_scenarios', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column('github_repos', sa.Column('adoption_suggestions', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column('github_repos', sa.Column('testing_value_score', sa.Integer(), nullable=True))
    op.add_column('github_repos', sa.Column('intel_status', sa.String(length=30), nullable=False, server_default='none'))
    op.create_index(op.f('ix_github_repos_testing_value_score'), 'github_repos', ['testing_value_score'], unique=False)
    op.create_index(op.f('ix_github_repos_intel_status'), 'github_repos', ['intel_status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_github_repos_intel_status'), table_name='github_repos')
    op.drop_index(op.f('ix_github_repos_testing_value_score'), table_name='github_repos')
    op.drop_column('github_repos', 'intel_status')
    op.drop_column('github_repos', 'testing_value_score')
    op.drop_column('github_repos', 'adoption_suggestions')
    op.drop_column('github_repos', 'applicable_scenarios')
    op.drop_column('github_repos', 'testing_value_analysis')
    op.drop_column('github_repos', 'intel_summary')