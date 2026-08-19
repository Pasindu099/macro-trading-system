"""Change enriched_news surprise_factor to text.

Revision ID: 0013_surprise_factor_text
Revises: 0012_price_snapshots
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0013_surprise_factor_text"
down_revision = "0012_price_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "enriched_news",
        "surprise_factor",
        existing_type=sa.Integer(),
        type_=sa.Text(),
        existing_nullable=True,
        postgresql_using="surprise_factor::text",
    )


def downgrade() -> None:
    op.alter_column(
        "enriched_news",
        "surprise_factor",
        existing_type=sa.Text(),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="NULLIF(surprise_factor, '')::integer",
    )
