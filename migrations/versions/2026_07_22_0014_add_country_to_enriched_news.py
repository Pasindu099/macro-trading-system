"""Add country to enriched_news.

Revision ID: 0014_enriched_news_country
Revises: 0013_surprise_factor_text
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0014_enriched_news_country"
down_revision = "0013_surprise_factor_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("enriched_news", sa.Column("country", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("enriched_news", "country")
