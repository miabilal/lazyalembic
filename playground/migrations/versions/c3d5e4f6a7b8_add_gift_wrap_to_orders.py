"""Add gift wrap to orders

Revision ID: c3d5e4f6a7b8
Revises: b2e4d3c5f6a7
Create Date: 2026-09-12 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d5e4f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2e4d3c5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("orders", sa.Column("gift_wrap", sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("orders", "gift_wrap")
