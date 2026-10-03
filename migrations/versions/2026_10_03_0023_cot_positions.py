"""Add CFTC Traders in Financial Futures positions.

Revision ID: 0023_cot_positions
Revises: 0022_rates_quality_spreads
"""

from alembic import op
import sqlalchemy as sa


revision = "0023_cot_positions"
down_revision = "0022_rates_quality_spreads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cot_positions",
        sa.Column("report_date", sa.Date(), primary_key=True),
        sa.Column("contract_code", sa.String(10), primary_key=True),
        sa.Column("category", sa.String(20), primary_key=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("long", sa.BigInteger(), nullable=False),
        sa.Column("short", sa.BigInteger(), nullable=False),
        sa.Column("spreading", sa.BigInteger(), nullable=True),
        sa.Column("open_interest", sa.BigInteger(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "category IN ('dealer', 'asset_manager', 'leveraged_funds', 'other_reportable', 'nonreportable')",
            name="ck_cot_positions_category",
        ),
    )
    op.create_index("ix_cot_positions_currency_date", "cot_positions", ["currency", "report_date"])


def downgrade() -> None:
    op.drop_index("ix_cot_positions_currency_date", table_name="cot_positions")
    op.drop_table("cot_positions")
