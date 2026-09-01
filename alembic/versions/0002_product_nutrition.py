"""M1: §16.2 nutrition attributes on products

Adds `protein_grams` and `diet`. Both nullable — the §16.2 table gives them
only for the Protein Kitchen meal boxes; Nova Stationery and Saffron Tiffin Co.
lines carry neither.

Revision ID: 0002_nutrition
Revises: 0001_initial
Create Date: 2026-09-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_nutrition"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("protein_grams", sa.Integer(), nullable=True))
    op.add_column("products", sa.Column("diet", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "diet")
    op.drop_column("products", "protein_grams")
