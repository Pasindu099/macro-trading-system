"""Add FRED series observations (same pattern as the yield/FX observation tables).

Revision ID: 0025_fred_observations
Revises: 0024_cb_projections
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0025_fred_observations"
down_revision = "0024_cb_projections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fred_observations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("series_id", sa.String(32), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(20, 6), nullable=False),
        sa.Column("realtime_start", sa.Date(), nullable=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # A revised value has a new hash → new row; readers take the newest row per date.
        sa.UniqueConstraint("series_id", "observation_date", "payload_hash", name="uq_fred_series_date_hash"),
    )
    op.create_index("ix_fred_observations_series_date", "fred_observations", ["series_id", "observation_date"])


def downgrade() -> None:
    op.drop_index("ix_fred_observations_series_date", table_name="fred_observations")
    op.drop_table("fred_observations")
