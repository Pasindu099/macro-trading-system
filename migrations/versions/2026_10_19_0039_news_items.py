"""Step 13 C: tagged, deduplicated news items and per-raw-row filter status (permanent)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0039_news_items"
down_revision = "0038_news_search_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "news_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("raw_news_id", sa.BigInteger(), sa.ForeignKey("raw_news.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("currencies", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("news_type", sa.Text(), nullable=False),
        sa.Column("impact", sa.Text(), nullable=False),
        sa.Column("cb_decision", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("data_tier", sa.Text(), nullable=True),
        sa.Column("speakers", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("situation_ids", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("dominance_tier", sa.Text(), nullable=True),
        sa.Column("is_commodity", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("drivers", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("merged_sources", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("merged_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tag_version", sa.Text(), nullable=False),
        sa.Column("tagged_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("impact IN ('High', 'Medium', 'Low')", name="ck_news_items_impact"),
    )
    op.create_index("ix_news_items_published_at", "news_items", ["published_at"])
    op.create_index("ix_news_items_currencies", "news_items", ["currencies"], postgresql_using="gin")
    op.create_index("ix_news_items_impact_published", "news_items", ["impact", "published_at"])
    op.execute(
        "CREATE INDEX ix_news_items_fts ON news_items USING gin "
        "(to_tsvector('english', title || ' ' || coalesce(summary, '')))"
    )

    op.create_table(
        "news_raw_status",
        sa.Column("raw_news_id", sa.BigInteger(), sa.ForeignKey("raw_news.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("news_item_id", sa.BigInteger(), sa.ForeignKey("news_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('kept', 'dropped', 'merged')", name="ck_news_raw_status_status"),
    )
    op.create_index("ix_news_raw_status_item", "news_raw_status", ["news_item_id"])


def downgrade():
    op.drop_table("news_raw_status")
    op.drop_table("news_items")
