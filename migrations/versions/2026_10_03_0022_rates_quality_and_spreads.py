"""Add rates outlier flags and derived yield spreads.

Revision ID: 0022_rates_quality_spreads
Revises: 0021_job_watermarks
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_rates_quality_spreads"
down_revision = "0021_job_watermarks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("government_yield_observations", "fx_spot_observations"):
        op.add_column(table, sa.Column("is_outlier", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "yield_spreads",
        sa.Column("spread_name", sa.String(20), primary_key=True),
        sa.Column("tenor", sa.String(4), primary_key=True),
        sa.Column("obs_date", sa.Date(), primary_key=True),
        sa.Column("base_yield", sa.Numeric(12, 6), nullable=False),
        sa.Column("quote_yield", sa.Numeric(12, 6), nullable=False),
        sa.Column("spread_bp", sa.Numeric(14, 6), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("yield_spreads")
    for table in ("fx_spot_observations", "government_yield_observations"):
        op.drop_column(table, "is_outlier")
