"""Add cb_policy_documents and cb_economic_projections tables.

Revision ID: 0009_cb_docs
Revises: 0008_cb_pdf_url
Create Date: 2026-06-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

# revision identifiers, used by Alembic.
revision = "0009_cb_docs"
down_revision = "0008_cb_pdf_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── cb_policy_documents ───────────────────────────────────────────────────
    op.create_table(
        "cb_policy_documents",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("bank", sa.String(10), nullable=False),
        sa.Column("doc_type", sa.String(20), nullable=False, server_default="'statement'"),
        sa.Column("doc_date", sa.Date(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("tone_score", sa.Numeric(4, 2), nullable=True),
        sa.Column("tone_label", sa.String(30), nullable=True),
        sa.Column("inflation_outlook", sa.String(30), nullable=True),
        sa.Column("inflation_summary", sa.Text(), nullable=True),
        sa.Column("growth_outlook", sa.String(30), nullable=True),
        sa.Column("growth_summary", sa.Text(), nullable=True),
        sa.Column("labor_outlook", sa.String(30), nullable=True),
        sa.Column("labor_summary", sa.Text(), nullable=True),
        sa.Column("key_phrases", ARRAY(sa.Text()), nullable=True),
        sa.Column("tone_change_vs_prior", sa.Text(), nullable=True),
        sa.Column("retail_bullets", ARRAY(sa.Text()), nullable=True),
        sa.Column("full_analysis", JSONB(), nullable=True),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("bank", "doc_date", "doc_type", name="uq_cb_policy_docs"),
    )
    op.create_index(
        "ix_cb_policy_documents_bank_date",
        "cb_policy_documents",
        ["bank", "doc_date"],
    )

    # ── cb_economic_projections ────────────────────────────────────────────────
    op.create_table(
        "cb_economic_projections",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("bank", sa.String(10), nullable=False),
        sa.Column("projection_date", sa.Date(), nullable=False),
        sa.Column("horizon_label", sa.String(30), nullable=False),
        sa.Column("horizon_year", sa.Integer(), nullable=True),
        sa.Column("inflation_forecast", sa.Numeric(6, 3), nullable=True),
        sa.Column("gdp_forecast", sa.Numeric(6, 3), nullable=True),
        sa.Column("unemployment_forecast", sa.Numeric(6, 3), nullable=True),
        sa.Column(
            "source_doc_id",
            sa.BigInteger(),
            sa.ForeignKey("cb_policy_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "bank", "projection_date", "horizon_label", name="uq_cb_econ_proj"
        ),
    )
    op.create_index(
        "ix_cb_economic_projections_bank_date",
        "cb_economic_projections",
        ["bank", "projection_date"],
    )


def downgrade() -> None:
    op.drop_table("cb_economic_projections")
    op.drop_table("cb_policy_documents")
