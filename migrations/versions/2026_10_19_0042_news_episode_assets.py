"""News episode links and versioned daily cross-asset observations."""

from alembic import op
import sqlalchemy as sa

revision = "0042_news_episode_assets"
down_revision = "0041_newswire_actuals"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "news_item_episodes",
        sa.Column("news_item_id", sa.BigInteger(), sa.ForeignKey("news_items.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("episode_id", sa.BigInteger(), sa.ForeignKey("situation_episodes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_news_item_episodes_episode", "news_item_episodes", ["episode_id"])
    op.create_table(
        "cross_asset_daily_snapshots",
        sa.Column("observation_date", sa.Date(), primary_key=True),
        sa.Column("series_id", sa.Text(), primary_key=True),
        sa.Column("value", sa.Numeric(20, 8), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade():
    op.drop_table("cross_asset_daily_snapshots")
    op.drop_index("ix_news_item_episodes_episode", table_name="news_item_episodes")
    op.drop_table("news_item_episodes")
