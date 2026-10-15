"""Add brand slug to products

Revision ID: d4e6f5a7b8c9
Revises: c3d5e4f6a7b8
Create Date: 2026-10-01 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e6f5a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d5e4f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("products", sa.Column("brand_slug", sa.String(120), nullable=True))
    op.create_index("ix_products_brand_slug", "products", ["brand_slug"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_products_brand_slug", table_name="products")
    op.drop_column("products", "brand_slug")
