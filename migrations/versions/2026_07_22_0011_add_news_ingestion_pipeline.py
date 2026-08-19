"""Add news ingestion pipeline tables.

Revision ID: 0011_news_pipeline
Revises: 0010_event_log
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0011_news_pipeline"
down_revision = "0010_event_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_news",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_category", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "is_gated_relevant",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index("ix_raw_news_published_at", "raw_news", ["published_at"])

    op.create_table(
        "enriched_news",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "raw_news_id",
            sa.BigInteger(),
            sa.ForeignKey("raw_news.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("surprise_factor", sa.Integer(), nullable=True),
        sa.Column(
            "currency_impact",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "inflation_impact",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "employment_growth_impact",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "gold_analysis",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("historical_analog", sa.Text(), nullable=True),
        sa.Column("invalidation", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column(
            "market_context",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_enriched_news_tier", "enriched_news", ["tier"])
    op.create_index(
        "ix_enriched_news_raw_news_id",
        "enriched_news",
        ["raw_news_id"],
    )
    op.create_index(
        "ix_enriched_news_gold_net_direction",
        "enriched_news",
        [sa.text("(gold_analysis ->> 'net_direction')")],
    )

    op.create_table(
        "source_health",
        sa.Column("source_name", sa.Text(), primary_key=True),
        sa.Column("last_item_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_checked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "is_healthy",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_table("source_health")
    op.drop_index(
        "ix_enriched_news_gold_net_direction",
        table_name="enriched_news",
    )
    op.drop_index("ix_enriched_news_raw_news_id", table_name="enriched_news")
    op.drop_index("ix_enriched_news_tier", table_name="enriched_news")
    op.drop_table("enriched_news")
    op.drop_index("ix_raw_news_published_at", table_name="raw_news")
    op.drop_table("raw_news")
