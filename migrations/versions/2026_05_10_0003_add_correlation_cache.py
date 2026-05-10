"""Add correlation lab cache.

Revision ID: 0003_add_correlation_cache
Revises: 0002_user_accounts
Create Date: 2026-05-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_add_correlation_cache"
down_revision = "0002_user_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "correlation_cache" not in inspector.get_table_names():
        op.create_table(
            "correlation_cache",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("pair", sa.String(length=10), nullable=False),
            sa.Column("series_key", sa.String(length=50), nullable=False),
            sa.Column("month", sa.Date(), nullable=False),
            sa.Column("value", sa.Numeric(18, 6), nullable=True),
            sa.Column(
                "fetched_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.UniqueConstraint(
                "pair",
                "series_key",
                "month",
                name="uq_correlation_cache_series",
            ),
        )
    index_names = {index["name"] for index in inspector.get_indexes("correlation_cache")}
    if "ix_correlation_cache_pair_series_month" not in index_names:
        op.create_index(
            "ix_correlation_cache_pair_series_month",
            "correlation_cache",
            ["pair", "series_key", "month"],
        )


def downgrade() -> None:
    op.drop_index("ix_correlation_cache_pair_series_month", table_name="correlation_cache")
    op.drop_table("correlation_cache")
