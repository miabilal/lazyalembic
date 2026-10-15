"""Create orders

Revision ID: b2e4d3c5f6a7
Revises: a1f3c2d4e5b6
Create Date: 2026-09-05 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2e4d3c5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1f3c2d4e5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_email", sa.String(255), nullable=False),
        sa.Column("total", sa.Numeric(10, 2), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("orders")
