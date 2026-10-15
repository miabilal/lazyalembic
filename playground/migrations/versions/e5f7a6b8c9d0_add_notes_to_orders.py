"""Add notes to orders

Revision ID: e5f7a6b8c9d0
Revises: c3d5e4f6a7b8
Create Date: 2026-10-02 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f7a6b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'c3d5e4f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("orders", sa.Column("notes", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("orders", "notes")
