"""initial tables

Revision ID: 001
Revises: 
Create Date: 2024-11-28 

"""
from alembic import op
import sqlalchemy as sa

revision = '001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('api_key', sa.String(36), primary_key=True),
        sa.Column('credits', sa.Integer(), nullable=False)
    )

    op.execute("""
        INSERT INTO users (name, api_key, credits) VALUES
        ('admin',      '123e4567-e89b-12d3-a456-426614174000', 1000),
        ('test_user1', '550e8400-e29b-41d4-a716-446655440000',  500),
        ('test_user2', 'c56a4180-65aa-42ec-a945-5fd21dec0538',  250)
    """)

    op.create_table(
        'tasks',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('owner_api_key', sa.String(36), sa.ForeignKey('users.api_key'), nullable=False),
        sa.Column('a', sa.Integer(), nullable=False),
        sa.Column('b', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('result', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('retry_count', sa.Integer(), server_default='0'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now())
    )
    
    op.create_index('idx_tasks_owner', 'tasks', ['owner_api_key'])
    op.create_index('idx_tasks_status', 'tasks', ['status'])

def downgrade() -> None:
    op.drop_table('tasks')
    op.drop_table('users')