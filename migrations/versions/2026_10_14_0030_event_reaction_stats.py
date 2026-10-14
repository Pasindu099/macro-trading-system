"""Release-day close-to-close FX reaction statistics per indicator and pair."""

from alembic import op
import sqlalchemy as sa

revision = "0030_event_reaction_stats"
down_revision = "0029_dominance_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "event_reaction_stats",
        sa.Column("indicator_id", sa.Integer(), sa.ForeignKey("indicators.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pair", sa.String(7), nullable=False),
        sa.Column("direction_currency", sa.String(3), nullable=False),
        sa.Column("sample_n", sa.Integer(), nullable=False),
        sa.Column("avg_abs_move_pips", sa.Numeric(12, 2), nullable=True),
        sa.Column("beat_n", sa.Integer(), nullable=False),
        sa.Column("beat_avg_pips", sa.Numeric(12, 2), nullable=True),
        sa.Column("miss_n", sa.Integer(), nullable=False),
        sa.Column("miss_avg_pips", sa.Numeric(12, 2), nullable=True),
        sa.Column("hit_n", sa.Integer(), nullable=False),
        sa.Column("hit_rate", sa.Numeric(6, 4), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("indicator_id", "pair"),
    )


def downgrade():
    op.drop_table("event_reaction_stats")
