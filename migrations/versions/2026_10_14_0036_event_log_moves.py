"""Event Log: manually entered price moves per instrument and horizon after a news event."""

from alembic import op
import sqlalchemy as sa

revision = "0036_event_log_moves"
down_revision = "0035_cb_sentence_scores"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "event_log_moves",
        sa.Column("note_id", sa.BigInteger(), sa.ForeignKey("event_reaction_notes.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("instrument", sa.String(8), primary_key=True),
        sa.Column("horizon", sa.String(4), primary_key=True),
        sa.Column("move", sa.Numeric(10, 1), nullable=False),     # pips (FX), USD (gold), points (indices)
        sa.CheckConstraint("horizon IN ('1m', '5m', '30m', '1h', '4h')", name="ck_event_log_moves_horizon"),
    )


def downgrade():
    op.drop_table("event_log_moves")
