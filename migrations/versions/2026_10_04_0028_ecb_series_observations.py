"""Store ECB Data Portal observations used by tracking and EUR price.

Revision ID: 0028_ecb_series_observations
Revises: 0027_situation_episodes
"""

from alembic import op
import sqlalchemy as sa


revision = "0028_ecb_series_observations"
down_revision = "0027_situation_episodes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ecb_series_observations",
        sa.Column("series_key", sa.String(100), primary_key=True),
        sa.Column("observation_date", sa.Date(), primary_key=True),
        sa.Column("value", sa.Numeric(14, 5), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ecb_series_observations")
