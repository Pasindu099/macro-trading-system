"""Add durable government-yield observations.

Revision ID: 0019_government_yields
Revises: 0018_knowledge_visual_artifacts
Create Date: 2026-08-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0019_government_yields"
down_revision = "0018_knowledge_visual_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "government_yield_observations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("provider_symbol", sa.String(40), nullable=False),
        sa.Column("provider_country_prefix", sa.String(4), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("maturity", sa.String(4), nullable=False),
        sa.Column("maturity_months", sa.SmallInteger(), nullable=False),
        sa.Column("yield_value", sa.Numeric(12, 6), nullable=False),
        sa.Column("market_observation_date", sa.Date(), nullable=False),
        sa.Column("provider_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("original_timezone", sa.Text(), nullable=True),
        sa.Column("market_timezone", sa.Text(), nullable=True),
        sa.Column("data_frequency", sa.String(20), nullable=False),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("quality_status", sa.String(30), nullable=False),
        sa.Column("observation_kind", sa.String(30), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("validation_errors", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint(
            "provider",
            "provider_symbol",
            "market_observation_date",
            "payload_hash",
            name="uq_gov_yield_provider_symbol_date_hash",
        ),
    )
    op.create_index(
        "ix_gov_yield_country_maturity_date",
        "government_yield_observations",
        ["country_code", "maturity", "market_observation_date"],
    )
    op.create_index(
        "ix_gov_yield_symbol_date",
        "government_yield_observations",
        ["provider_symbol", "market_observation_date"],
    )
    op.create_index(
        "ix_gov_yield_quality",
        "government_yield_observations",
        ["quality_status", "source_type"],
    )

    op.create_table(
        "government_yield_ingestion_status",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("job_name", sa.String(80), nullable=False, unique=True),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_scheduled_run", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observations_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("observations_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("symbols_missing", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("stale_symbols", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("errors", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("government_yield_ingestion_status")
    op.drop_index("ix_gov_yield_quality", table_name="government_yield_observations")
    op.drop_index("ix_gov_yield_symbol_date", table_name="government_yield_observations")
    op.drop_index("ix_gov_yield_country_maturity_date", table_name="government_yield_observations")
    op.drop_table("government_yield_observations")
