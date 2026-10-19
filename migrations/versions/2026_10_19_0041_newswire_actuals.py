"""Audit newswire actuals, calendar matches, and later EODHD confirmation."""

from alembic import op
import sqlalchemy as sa

revision = "0041_newswire_actuals"
down_revision = "0040_news_no_fx_link"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "newswire_actuals",
        sa.Column("news_item_id", sa.BigInteger(), sa.ForeignKey("news_items.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("scheduled_release_id", sa.BigInteger(), sa.ForeignKey("indicator_releases.id", ondelete="SET NULL")),
        sa.Column("newswire_release_id", sa.BigInteger(), sa.ForeignKey("indicator_releases.id", ondelete="SET NULL")),
        sa.Column("actual", sa.Numeric(20, 6)),
        sa.Column("consensus", sa.Numeric(20, 6)),
        sa.Column("eodhd_actual", sa.Numeric(20, 6)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("matched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('unparsed','unmatched','newswire','confirmed','discrepancy')",
                           name="ck_newswire_actuals_status"),
    )
    op.create_index("ix_newswire_actuals_status", "newswire_actuals", ["status"])


def downgrade():
    op.drop_index("ix_newswire_actuals_status", table_name="newswire_actuals")
    op.drop_table("newswire_actuals")
