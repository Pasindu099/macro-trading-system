"""Step 13 B: raw_news is the single permanent news store; add dedupe and full-text indexes."""

from alembic import op

revision = "0038_news_search_indexes"
down_revision = "0037_event_log_move_precision"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE INDEX IF NOT EXISTS ix_raw_news_url_md5 ON raw_news (md5(url))")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_raw_news_fts ON raw_news USING gin "
        "(to_tsvector('english', coalesce(title, '') || ' ' || coalesce(body, '')))"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_raw_news_source_published ON raw_news (source, published_at)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_raw_news_source_published")
    op.execute("DROP INDEX IF EXISTS ix_raw_news_fts")
    op.execute("DROP INDEX IF EXISTS ix_raw_news_url_md5")
