"""Add cb_policy_reports table for AI-analyzed monetary policy statements.

Revision ID: 0007_add_cb_policy_reports
Revises: 0006_add_rp_scraped_tables
Create Date: 2026-06-29
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from alembic import op

revision = "0007_add_cb_policy_reports"
down_revision = "0006_add_rp_scraped_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cb_policy_reports",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("bank", sa.String(length=10), nullable=False),
        sa.Column("meeting_date", sa.Date(), nullable=False),
        sa.Column("report_type", sa.String(length=30), nullable=False, server_default="'statement'"),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        # AI-derived fields
        sa.Column("tone_score", sa.Numeric(4, 2), nullable=True),
        sa.Column("tone_label", sa.String(length=30), nullable=True),
        sa.Column("inflation_outlook", sa.String(length=30), nullable=True),
        sa.Column("inflation_summary", sa.Text(), nullable=True),
        sa.Column("growth_outlook", sa.String(length=30), nullable=True),
        sa.Column("growth_summary", sa.Text(), nullable=True),
        sa.Column("labor_outlook", sa.String(length=30), nullable=True),
        sa.Column("labor_summary", sa.Text(), nullable=True),
        sa.Column("key_phrases", ARRAY(sa.Text()), nullable=True),
        sa.Column("tone_change_vs_prior", sa.Text(), nullable=True),
        sa.Column("retail_bullets", ARRAY(sa.Text()), nullable=True),
        sa.Column("full_analysis", JSONB(), nullable=True),
        sa.Column(
            "analyzed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "scraped_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("bank", "meeting_date", name="uq_cb_policy_reports_bank_date"),
    )
    op.create_index(
        "ix_cb_policy_reports_bank_date",
        "cb_policy_reports",
        ["bank", "meeting_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_cb_policy_reports_bank_date", table_name="cb_policy_reports")
    op.drop_table("cb_policy_reports")
