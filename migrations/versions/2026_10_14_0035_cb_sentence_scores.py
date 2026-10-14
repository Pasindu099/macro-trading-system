"""Tone scores per sentence and method (lexicon_v1 now; an AI method can sit alongside) (Step 12 Part F)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0035_cb_sentence_scores"
down_revision = "0034_cb_speeches"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cb_sentence_scores",
        sa.Column("sentence_id", sa.BigInteger(), sa.ForeignKey("cb_speech_sentences.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("method", sa.String(24), primary_key=True),
        sa.Column("score", sa.Numeric(5, 3), nullable=False),
        sa.Column("raw_score", sa.Numeric(6, 3), nullable=False),
        sa.Column("hits", JSONB(), nullable=False, server_default="[]"),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("score BETWEEN -2 AND 2", name="ck_cb_sentence_scores_range"),
    )


def downgrade():
    op.drop_table("cb_sentence_scores")
