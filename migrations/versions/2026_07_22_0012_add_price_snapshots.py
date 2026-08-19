"""Add price_snapshots table.

Revision ID: 0012_price_snapshots
Revises: 0011_news_pipeline
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0012_price_snapshots"
down_revision = "0011_news_pipeline"
branch_labels = None
depends_on = None

price_snapshot_type = postgresql.ENUM(
    "immediate",
    "15m",
    "1h",
    "4h",
    "eod",
    name="price_snapshot_type",
    create_type=False,
)


def upgrade() -> None:
    price_snapshot_type.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "price_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "enriched_news_id",
            sa.BigInteger(),
            sa.ForeignKey("enriched_news.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("instrument", sa.String(30), nullable=False),
        sa.Column("snapshot_type", price_snapshot_type, nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Numeric(20, 6), nullable=False),
        sa.Column("price_change_from_immediate_pct", sa.Numeric(10, 4), nullable=True),
    )
    op.create_index(
        "ix_price_snapshots_enriched_news_id",
        "price_snapshots",
        ["enriched_news_id"],
    )
    op.create_index(
        "ix_price_snapshots_instrument_snapshot_type",
        "price_snapshots",
        ["instrument", "snapshot_type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_price_snapshots_instrument_snapshot_type",
        table_name="price_snapshots",
    )
    op.drop_index(
        "ix_price_snapshots_enriched_news_id",
        table_name="price_snapshots",
    )
    op.drop_table("price_snapshots")
    price_snapshot_type.drop(op.get_bind(), checkfirst=True)
