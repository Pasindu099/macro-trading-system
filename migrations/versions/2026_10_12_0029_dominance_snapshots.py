"""Daily four-factor FX dominance attribution."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0029_dominance_snapshots"
down_revision = "0028_ecb_series_observations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "dominance_snapshots",
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("shares", JSONB(), nullable=False),
        sa.Column("pair_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_date", "window_days"),
    )


def downgrade():
    op.drop_table("dominance_snapshots")
