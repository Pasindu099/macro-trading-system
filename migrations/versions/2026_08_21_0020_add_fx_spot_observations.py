"""Add durable FX spot observations.

Revision ID: 0020_fx_spot_observations
Revises: 0019_government_yields
Create Date: 2026-08-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0020_fx_spot_observations"
down_revision = "0019_government_yields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fx_spot_observations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("provider_symbol", sa.String(40), nullable=False),
        sa.Column("pair", sa.String(7), nullable=False),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("quote_currency", sa.String(3), nullable=False),
        sa.Column("close_value", sa.Numeric(20, 8), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("provider_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("data_frequency", sa.String(20), nullable=False),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("quality_status", sa.String(30), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("validation_errors", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint(
            "provider",
            "provider_symbol",
            "observation_date",
            "payload_hash",
            name="uq_fx_spot_provider_symbol_date_hash",
        ),
    )
    op.create_index("ix_fx_spot_pair_date", "fx_spot_observations", ["pair", "observation_date"])
    op.create_index("ix_fx_spot_quality", "fx_spot_observations", ["quality_status", "source_type"])


def downgrade() -> None:
    op.drop_index("ix_fx_spot_quality", table_name="fx_spot_observations")
    op.drop_index("ix_fx_spot_pair_date", table_name="fx_spot_observations")
    op.drop_table("fx_spot_observations")
