"""Step 14 A: permanent Myfxbook retail sentiment snapshots and a last-of-day view."""

from alembic import op
import sqlalchemy as sa

revision = "0043_retail_sentiment_snapshots"
down_revision = "0042_news_episode_assets"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "retail_sentiment_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("pair", sa.Text(), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("long_pct", sa.Numeric(6, 2), nullable=False),
        sa.Column("short_pct", sa.Numeric(6, 2), nullable=False),
        sa.Column("long_positions", sa.Integer(), nullable=True),
        sa.Column("short_positions", sa.Integer(), nullable=True),
        sa.Column("avg_long_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("avg_short_price", sa.Numeric(18, 6), nullable=True),
        sa.UniqueConstraint("source", "pair", "taken_at", name="uq_retail_sentiment_snapshot"),
    )
    op.create_index("ix_retail_sentiment_pair_taken", "retail_sentiment_snapshots", ["pair", "taken_at"])
    op.execute("""
        CREATE VIEW retail_sentiment_daily AS
        SELECT DISTINCT ON (source, pair, (taken_at AT TIME ZONE 'UTC')::date)
               source, pair, (taken_at AT TIME ZONE 'UTC')::date AS day, taken_at, long_pct, short_pct,
               long_positions, short_positions, avg_long_price, avg_short_price
        FROM retail_sentiment_snapshots
        ORDER BY source, pair, (taken_at AT TIME ZONE 'UTC')::date, taken_at DESC
    """)


def downgrade():
    op.execute("DROP VIEW IF EXISTS retail_sentiment_daily")
    op.drop_table("retail_sentiment_snapshots")
