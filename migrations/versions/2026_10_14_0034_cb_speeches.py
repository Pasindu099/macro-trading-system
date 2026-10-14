"""Speeches, attributed investingLive comments and extracted policy sentences (Step 12 Part E)."""

from alembic import op
import sqlalchemy as sa

revision = "0034_cb_speeches"
down_revision = "0033_cb_manual_imports"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cb_speeches",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("bank", sa.String(8), nullable=False),
        sa.Column("speaker_id", sa.String(32), nullable=True),
        sa.Column("speech_date", sa.Date(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False, unique=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("source", sa.String(48), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_cb_speeches_bank_date", "cb_speeches", ["bank", "speech_date"])
    op.create_table(
        "cb_speaker_comments",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("news_id", sa.BigInteger(), sa.ForeignKey("raw_news.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bank", sa.String(8), nullable=False),
        sa.Column("speaker_id", sa.String(32), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.UniqueConstraint("news_id", "speaker_id", name="uq_cb_speaker_comments"),
    )
    op.create_index("ix_cb_speaker_comments_bank_time", "cb_speaker_comments", ["bank", "published_at"])
    op.create_table(
        "cb_speech_sentences",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("source_type", sa.String(8), nullable=False),            # speech | comment
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("bank", sa.String(8), nullable=False),
        sa.Column("speaker_id", sa.String(32), nullable=True),
        sa.Column("sentence_date", sa.Date(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("sentence", sa.Text(), nullable=False),
        sa.Column("theme", sa.String(24), nullable=False),
        sa.CheckConstraint("source_type IN ('speech', 'comment')", name="ck_cb_speech_sentences_source"),
        sa.UniqueConstraint("source_type", "source_id", "position", name="uq_cb_speech_sentences"),
    )
    op.create_index("ix_cb_speech_sentences_speaker_date", "cb_speech_sentences", ["bank", "speaker_id", "sentence_date"])


def downgrade():
    op.drop_table("cb_speech_sentences")
    op.drop_table("cb_speaker_comments")
    op.drop_table("cb_speeches")
