"""Store published individual policy votes with their publication time."""

from alembic import op
import sqlalchemy as sa

revision = "0044_cb_votes"
down_revision = "0043_retail_sentiment_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cb_votes",
        sa.Column("bank", sa.Text(), nullable=False),
        sa.Column("meeting_date", sa.Date(), nullable=False),
        sa.Column("member", sa.Text(), nullable=False),
        sa.Column("vote", sa.Text(), nullable=False),
        sa.Column("preferred_change_bp", sa.Numeric(7, 2)),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("bank", "meeting_date", "member"),
    )
    op.create_index("ix_cb_votes_published", "cb_votes", ["bank", "published_at"])


def downgrade():
    op.drop_table("cb_votes")
