"""Daily point-in-time snapshots of the experimental model-implied policy rule."""

from alembic import op
import sqlalchemy as sa

revision = "0046_policy_model_daily"
down_revision = "0045_boj_projection_sources"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "policy_model_daily",
        sa.Column("bank", sa.String(8), primary_key=True),
        sa.Column("data_date", sa.Date(), primary_key=True),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("missing_inputs", sa.JSON(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("model_rate", sa.Numeric(8, 4)),
        sa.Column("range_low", sa.Numeric(8, 4)),
        sa.Column("range_high", sa.Numeric(8, 4)),
        sa.Column("market_rate_3m", sa.Numeric(8, 4)),
        sa.Column("gap_bp", sa.Numeric(9, 2)),
        sa.Column("confidence", sa.Text()),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("policy_model_daily")
