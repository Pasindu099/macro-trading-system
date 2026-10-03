"""Add central-bank projection tables (Fed SEP: values, dots, risk balance, forecast errors).

Revision ID: 0024_cb_projections
Revises: 0023_cot_positions
"""

from alembic import op
import sqlalchemy as sa


revision = "0024_cb_projections"
down_revision = "0023_cot_positions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cb_projection_values",
        sa.Column("bank", sa.String(8), primary_key=True),
        sa.Column("release_date", sa.Date(), primary_key=True),
        sa.Column("variable", sa.String(32), primary_key=True),
        sa.Column("horizon", sa.String(12), primary_key=True),   # '2026' | '2027' | '2028' | 'longer_run'
        sa.Column("stat", sa.String(12), primary_key=True),      # median | ct_low | ct_high | range_low | range_high
        sa.Column("value", sa.Numeric(8, 3), nullable=False),
        sa.CheckConstraint("stat IN ('median', 'ct_low', 'ct_high', 'range_low', 'range_high')", name="ck_cb_projection_values_stat"),
    )
    op.create_table(
        "cb_dots",
        sa.Column("bank", sa.String(8), primary_key=True),
        sa.Column("release_date", sa.Date(), primary_key=True),
        sa.Column("horizon", sa.String(12), primary_key=True),
        sa.Column("rate", sa.Numeric(6, 3), primary_key=True),
        sa.Column("participants", sa.SmallInteger(), nullable=False),
    )
    op.create_table(
        "cb_risk_balance",
        sa.Column("bank", sa.String(8), primary_key=True),
        sa.Column("release_date", sa.Date(), primary_key=True),
        sa.Column("variable", sa.String(32), primary_key=True),
        sa.Column("kind", sa.String(12), primary_key=True),      # uncertainty | risk
        sa.Column("lower_or_downside", sa.SmallInteger(), nullable=False),
        sa.Column("similar_or_balanced", sa.SmallInteger(), nullable=False),
        sa.Column("higher_or_upside", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("kind IN ('uncertainty', 'risk')", name="ck_cb_risk_balance_kind"),
    )
    op.create_table(
        "cb_projection_errors",
        sa.Column("bank", sa.String(8), primary_key=True),
        sa.Column("publication_year", sa.SmallInteger(), primary_key=True),
        sa.Column("variable", sa.String(32), primary_key=True),
        sa.Column("horizon", sa.String(12), primary_key=True),
        sa.Column("rmse", sa.Numeric(6, 3), nullable=False),
    )


def downgrade() -> None:
    for table in ("cb_projection_errors", "cb_risk_balance", "cb_dots", "cb_projection_values"):
        op.drop_table(table)
