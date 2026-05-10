"""Add rate probability tables.

Revision ID: 0004_add_rate_probability_tables
Revises: 0003_add_correlation_cache
Create Date: 2026-05-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_add_rate_probability_tables"
down_revision = "0003_add_correlation_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cb_meetings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("bank", sa.String(length=10), nullable=False),
        sa.Column("meeting_dt", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "is_official",
            sa.Boolean(),
            nullable=True,
            server_default=sa.text("true"),
        ),
        sa.UniqueConstraint("bank", "meeting_dt", name="uq_cb_meetings_bank_dt"),
    )
    op.create_index("ix_cb_meetings_bank_dt", "cb_meetings", ["bank", "meeting_dt"])

    op.create_table(
        "rate_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("bank", sa.String(length=10), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("meeting_dt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("implied_rate", sa.Numeric(6, 4), nullable=False),
        sa.Column("cut_prob", sa.Numeric(5, 4), nullable=True),
        sa.Column("hold_prob", sa.Numeric(5, 4), nullable=True),
        sa.Column("hike_prob", sa.Numeric(5, 4), nullable=True),
        sa.Column("delta_bps", sa.Numeric(7, 2), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "bank",
            "snapshot_date",
            "meeting_dt",
            name="uq_rate_snapshots_bank_snapshot_meeting",
        ),
    )
    op.create_index(
        "ix_rate_snapshots_bank_snapshot",
        "rate_snapshots",
        ["bank", "snapshot_date"],
    )

    op.create_table(
        "ois_cache",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("bank", sa.String(length=10), nullable=False),
        sa.Column("curve_date", sa.Date(), nullable=False),
        sa.Column("tenor_days", sa.Integer(), nullable=False),
        sa.Column("rate", sa.Numeric(8, 6), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "bank",
            "curve_date",
            "tenor_days",
            name="uq_ois_cache_bank_curve_tenor",
        ),
    )
    op.create_index("ix_ois_cache_bank_curve", "ois_cache", ["bank", "curve_date"])


def downgrade() -> None:
    op.drop_index("ix_ois_cache_bank_curve", table_name="ois_cache")
    op.drop_table("ois_cache")
    op.drop_index("ix_rate_snapshots_bank_snapshot", table_name="rate_snapshots")
    op.drop_table("rate_snapshots")
    op.drop_index("ix_cb_meetings_bank_dt", table_name="cb_meetings")
    op.drop_table("cb_meetings")
