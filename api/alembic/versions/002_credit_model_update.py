"""credit model update

Revision ID: 002
Revises: 001
Create Date: 2024-12-22

Updates the credit model to support reservation-based billing:
- Rename users.credits to users.total_credits
- Add users.spent_credits for tracking completed task consumption
- Add tasks.credits_required for per-task cost tracking
"""
from alembic import op
import sqlalchemy as sa

revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename credits to total_credits
    op.alter_column('users', 'credits', new_column_name='total_credits')

    # Add spent_credits column (consumed by completed tasks)
    op.add_column('users', sa.Column('spent_credits', sa.Integer(), nullable=False, server_default='0'))

    # Add credits_required to tasks (always 1 for now)
    op.add_column('tasks', sa.Column('credits_required', sa.Integer(), nullable=False, server_default='1'))


def downgrade() -> None:
    # Remove credits_required from tasks
    op.drop_column('tasks', 'credits_required')

    # Remove spent_credits from users
    op.drop_column('users', 'spent_credits')

    # Rename total_credits back to credits
    op.alter_column('users', 'total_credits', new_column_name='credits')
