"""Flag geopolitical news with no direct FX link."""

from alembic import op
import sqlalchemy as sa

revision = "0040_news_no_fx_link"
down_revision = "0039_news_items"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("news_items", sa.Column("no_fx_link", sa.Boolean(), nullable=False,
                                          server_default=sa.text("false")))
    op.create_index("ix_news_items_no_fx_link", "news_items", ["no_fx_link", "published_at"])


def downgrade():
    op.drop_index("ix_news_items_no_fx_link", table_name="news_items")
    op.drop_column("news_items", "no_fx_link")
