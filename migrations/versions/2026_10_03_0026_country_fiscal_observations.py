"""Annual Eurostat general government balance as percent of GDP."""

from alembic import op
import sqlalchemy as sa

revision = "0026_country_fiscal_observations"
down_revision = "0025_fred_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "country_fiscal_observations",
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("balance_pct_gdp", sa.Numeric(8, 3), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="Eurostat"),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("country_code", "year"),
    )


def downgrade() -> None:
    op.drop_table("country_fiscal_observations")
