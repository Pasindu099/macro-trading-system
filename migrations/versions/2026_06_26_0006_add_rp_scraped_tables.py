"""Add scraped rate probability tables (rateprobability.com).

Revision ID: 0006_add_rp_scraped_tables
Revises: 0005_add_intelligence_news_alerts
Create Date: 2026-06-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_add_rp_scraped_tables"
down_revision = "0005_add_intelligence_news_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rp_scraped_meetings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("bank", sa.String(length=10), nullable=False),
        sa.Column("meeting_date", sa.Date(), nullable=False),
        sa.Column("implied_rate", sa.Numeric(6, 3), nullable=True),
        sa.Column("cut_prob", sa.Numeric(6, 2), nullable=True),
        sa.Column("hold_prob", sa.Numeric(6, 2), nullable=True),
        sa.Column("hike_prob", sa.Numeric(6, 2), nullable=True),
        sa.Column("delta_bps", sa.Numeric(8, 2), nullable=True),
        sa.Column("cumulative_moves", sa.Numeric(6, 2), nullable=True),
        sa.Column(
            "scraped_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("bank", "meeting_date", name="uq_rp_scraped_meetings_bank_date"),
    )
    op.create_index("ix_rp_scraped_meetings_bank", "rp_scraped_meetings", ["bank", "meeting_date"])

    op.create_table(
        "rp_scraped_summary",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("bank", sa.String(length=10), nullable=False, unique=True),
        sa.Column("current_rate", sa.Numeric(6, 3), nullable=True),
        sa.Column("next_meeting_date", sa.Date(), nullable=True),
        sa.Column("next_cut_prob", sa.Numeric(6, 2), nullable=True),
        sa.Column("next_hold_prob", sa.Numeric(6, 2), nullable=True),
        sa.Column("next_hike_prob", sa.Numeric(6, 2), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_rp_scraped_summary_bank", "rp_scraped_summary", ["bank"])


def downgrade() -> None:
    op.drop_index("ix_rp_scraped_summary_bank", table_name="rp_scraped_summary")
    op.drop_table("rp_scraped_summary")
    op.drop_index("ix_rp_scraped_meetings_bank", table_name="rp_scraped_meetings")
    op.drop_table("rp_scraped_meetings")
