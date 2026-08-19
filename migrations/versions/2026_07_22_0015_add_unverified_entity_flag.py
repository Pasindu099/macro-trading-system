"""Add unverified entity audit flag to enriched_news.

Revision ID: 0015_unverified_entity_flag
Revises: 0014_enriched_news_country
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0015_unverified_entity_flag"
down_revision = "0014_enriched_news_country"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "enriched_news",
        sa.Column(
            "contains_unverified_entity",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("enriched_news", "contains_unverified_entity")
