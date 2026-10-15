"""Create products

Revision ID: a1f3c2d4e5b6
Revises: 
Create Date: 2026-09-01 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1f3c2d4e5b6'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("products")
