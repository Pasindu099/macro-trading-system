"""Initial schema: countries, indicators, indicator_releases, ingestion_runs.

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-18

Creates the core 4 tables per spec §4.

Design notes:
- All timestamps are TIMESTAMPTZ (timezone-aware).
- indicator_releases uses a generated column for 'surprise' (actual - estimate).
- Partial index on (is_latest) = true for fast "current values" queries.
- indicators has both primary_category and secondary_categories (array) for
  multi-categorization (e.g. Avg Hourly Earnings appears under both Labor
  and Inflation).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────
    # countries
    # ──────────────────────────────────────────────────────────
    op.create_table(
        "countries",
        sa.Column("code", sa.String(length=2), primary_key=True),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("central_bank", sa.Text(), nullable=False),
        sa.Column("cb_inflation_target", sa.Numeric(4, 2), nullable=True),
        sa.Column("cb_mandate_type", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
    )

    # ──────────────────────────────────────────────────────────
    # indicators
    # ──────────────────────────────────────────────────────────
    op.create_table(
        "indicators",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column(
            "country_code",
            sa.String(length=2),
            sa.ForeignKey("countries.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("primary_category", sa.Text(), nullable=False),
        sa.Column(
            "secondary_categories",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("comparison", sa.Text(), nullable=True),
        sa.Column("frequency", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column(
            "is_higher_better_for_currency",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "importance",
            sa.SmallInteger(),
            nullable=False,
            server_default="2",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "canonical_name",
            "country_code",
            name="uq_indicators_canonical_country",
        ),
    )
    op.create_index(
        "ix_indicators_country_category",
        "indicators",
        ["country_code", "primary_category"],
    )

    # ──────────────────────────────────────────────────────────
    # indicator_releases
    # ──────────────────────────────────────────────────────────
    op.create_table(
        "indicator_releases",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "indicator_id",
            sa.Integer(),
            sa.ForeignKey("indicators.id", ondelete="CASCADE"),
            nullable=True,  # nullable so unmapped events can still be stored
        ),
        sa.Column("period", sa.Text(), nullable=True),
        sa.Column("period_start_date", sa.Date(), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actual", sa.Numeric(20, 6), nullable=True),
        sa.Column("previous", sa.Numeric(20, 6), nullable=True),
        sa.Column("estimate", sa.Numeric(20, 6), nullable=True),
        sa.Column("change", sa.Numeric(20, 6), nullable=True),
        sa.Column("change_percentage", sa.Numeric(20, 6), nullable=True),
        # 'surprise' is a generated column: actual - estimate. NULL when either is NULL.
        sa.Column(
            "surprise",
            sa.Numeric(20, 6),
            sa.Computed("actual - estimate", persisted=True),
            nullable=True,
        ),
        sa.Column(
            "retrieved_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "is_latest",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(),
            nullable=True,
        ),
    )
    # Time-series query: latest values for an indicator, sorted by period
    op.create_index(
        "ix_releases_indicator_period",
        "indicator_releases",
        ["indicator_id", sa.text("period_start_date DESC")],
    )
    # Revision history: all versions of one (indicator, period), newest first
    op.create_index(
        "ix_releases_indicator_period_retrieved",
        "indicator_releases",
        ["indicator_id", "period", sa.text("retrieved_at DESC")],
    )
    # Fast filter for "current values only"
    op.create_index(
        "ix_releases_is_latest",
        "indicator_releases",
        ["indicator_id", "period_start_date"],
        postgresql_where=sa.text("is_latest = true"),
    )
    # Calendar queries
    op.create_index(
        "ix_releases_released_at",
        "indicator_releases",
        [sa.text("released_at DESC")],
    )

    # ──────────────────────────────────────────────────────────
    # ingestion_runs
    # ──────────────────────────────────────────────────────────
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_type", sa.Text(), nullable=False),
        sa.Column(
            "countries_fetched",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "events_inserted",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "events_updated",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "api_calls_used",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("errors", postgresql.JSONB(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="'running'",
        ),
    )
    op.create_index(
        "ix_ingestion_runs_started_at",
        "ingestion_runs",
        [sa.text("started_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_ingestion_runs_started_at", table_name="ingestion_runs")
    op.drop_table("ingestion_runs")

    op.drop_index("ix_releases_released_at", table_name="indicator_releases")
    op.drop_index("ix_releases_is_latest", table_name="indicator_releases")
    op.drop_index("ix_releases_indicator_period_retrieved", table_name="indicator_releases")
    op.drop_index("ix_releases_indicator_period", table_name="indicator_releases")
    op.drop_table("indicator_releases")

    op.drop_index("ix_indicators_country_category", table_name="indicators")
    op.drop_table("indicators")

    op.drop_table("countries")