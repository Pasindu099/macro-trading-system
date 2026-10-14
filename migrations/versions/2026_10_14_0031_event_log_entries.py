"""Event Log entries: free-text events, trade fields, raw vs final AI notes."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0031_event_log_entries"
down_revision = "0030_event_reaction_stats"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("event_reaction_notes", "indicator_release_id", nullable=True)
    for column in (
        sa.Column("event_title", sa.Text(), nullable=True),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=True),
        sa.Column("pairs", ARRAY(sa.String(7)), nullable=False, server_default="{}"),
        sa.Column("expectation_before", sa.Text(), nullable=True),
        sa.Column("outcome_after", sa.Text(), nullable=True),
        sa.Column("traded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("direction", sa.String(5), nullable=True),
        sa.Column("entry_price", sa.Numeric(14, 6), nullable=True),
        sa.Column("exit_price", sa.Numeric(14, 6), nullable=True),
        sa.Column("size", sa.Numeric(16, 2), nullable=True),
        sa.Column("pnl_pips", sa.Numeric(10, 1), nullable=True),
        sa.Column("pnl_amount", sa.Numeric(16, 2), nullable=True),
        sa.Column("ai_note_raw", sa.Text(), nullable=True),
        sa.Column("ai_note_final", sa.Text(), nullable=True),
    ):
        op.add_column("event_reaction_notes", column)
    op.create_check_constraint("ck_event_reaction_notes_direction", "event_reaction_notes",
                               "direction IS NULL OR direction IN ('long', 'short')")
    op.create_check_constraint("ck_event_reaction_notes_event", "event_reaction_notes",
                               "indicator_release_id IS NOT NULL OR event_title IS NOT NULL")
    op.execute("""
        UPDATE event_reaction_notes n
        SET ai_note_raw = n.ai_interpretation, ai_note_final = n.ai_interpretation
        WHERE n.ai_interpretation IS NOT NULL
    """)
    op.execute("""
        UPDATE event_reaction_notes n
        SET event_date = (r.released_at AT TIME ZONE 'UTC')::date, currency = c.currency_code
        FROM indicator_releases r
        JOIN indicators i ON i.id = r.indicator_id
        JOIN countries c ON c.code = i.country_code
        WHERE r.id = n.indicator_release_id
    """)
    op.drop_column("event_reaction_notes", "ai_interpretation")


def downgrade():
    op.add_column("event_reaction_notes", sa.Column("ai_interpretation", sa.Text(), nullable=True))
    op.execute("UPDATE event_reaction_notes SET ai_interpretation = ai_note_final")
    op.execute("DELETE FROM event_reaction_notes WHERE indicator_release_id IS NULL")
    op.drop_constraint("ck_event_reaction_notes_event", "event_reaction_notes")
    op.drop_constraint("ck_event_reaction_notes_direction", "event_reaction_notes")
    for name in ("ai_note_final", "ai_note_raw", "pnl_amount", "pnl_pips", "size", "exit_price",
                 "entry_price", "direction", "traded", "outcome_after", "expectation_before",
                 "pairs", "event_type", "currency", "event_date", "event_title"):
        op.drop_column("event_reaction_notes", name)
    op.alter_column("event_reaction_notes", "indicator_release_id", nullable=False)
