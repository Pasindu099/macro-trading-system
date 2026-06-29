"""add source_pdf_url to cb_policy_reports

Revision ID: 0008_add_cb_policy_source_pdf_url
Revises: 0007_add_cb_policy_reports
Create Date: 2026-06-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0008_add_cb_policy_source_pdf_url"
down_revision = "0007_add_cb_policy_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cb_policy_reports",
        sa.Column("source_pdf_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cb_policy_reports", "source_pdf_url")
