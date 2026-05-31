"""Add intelligence news alerts table.

Revision ID: 0005_add_intelligence_news_alerts
Revises: 0004_add_rate_probability_tables
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_add_intelligence_news_alerts"
down_revision = "0004_add_rate_probability_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS intelligence")
    op.create_table(
        "news_alerts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("relevance_score", sa.Integer(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("was_surfaced", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "affected_currencies",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "affected_assets",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("implied_tier", sa.Text(), nullable=True),
        sa.Column("risk_tone_implication", sa.Text(), nullable=True),
        sa.Column(
            "currency_implications",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("alert_text", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "triggered_state_change",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("resulting_dominance_state_id", sa.BigInteger(), nullable=True),
        schema="intelligence",
    )
    op.create_unique_constraint(
        "uq_news_alerts_content_hash",
        "news_alerts",
        ["content_hash"],
        schema="intelligence",
    )
    op.create_index(
        "ix_news_alerts_surfaced_detected",
        "news_alerts",
        ["was_surfaced", "detected_at"],
        schema="intelligence",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_news_alerts_surfaced_detected",
        table_name="news_alerts",
        schema="intelligence",
    )
    op.drop_constraint(
        "uq_news_alerts_content_hash",
        "news_alerts",
        schema="intelligence",
        type_="unique",
    )
    op.drop_table("news_alerts", schema="intelligence")
