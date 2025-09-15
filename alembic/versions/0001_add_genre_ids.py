"""Initial migration

Revision ID: 0001
Revises: 
Create Date: 2024-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add genre_ids column to subscribe_statistics table."""
    # Check if the column already exists
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    
    if 'subscribe_statistics' in inspector.get_table_names():
        columns = [col['name'] for col in inspector.get_columns('subscribe_statistics')]
        
        if 'genre_ids' not in columns:
            op.add_column('subscribe_statistics', sa.Column('genre_ids', sa.String(), nullable=True))


def downgrade() -> None:
    """Remove genre_ids column from subscribe_statistics table."""
    # Check if the column exists before dropping
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    
    if 'subscribe_statistics' in inspector.get_table_names():
        columns = [col['name'] for col in inspector.get_columns('subscribe_statistics')]
        
        if 'genre_ids' in columns:
            op.drop_column('subscribe_statistics', 'genre_ids')