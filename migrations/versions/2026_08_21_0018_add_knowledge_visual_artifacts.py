"""Add Knowledge Bank figure and table artifact tables.

Revision ID: 0018_knowledge_visual_artifacts
Revises: 0017_knowledge_bank
Create Date: 2026-08-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0018_knowledge_visual_artifacts"
down_revision = "0017_knowledge_bank"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_figures",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "document_id",
            sa.BigInteger(),
            sa.ForeignKey("knowledge_source_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("figure_index", sa.Integer(), nullable=False),
        sa.Column("image_hash", sa.String(64), nullable=False),
        sa.Column("artifact_path", sa.Text(), nullable=False),
        sa.Column("image_format", sa.String(20), nullable=True),
        sa.Column("width_px", sa.Integer(), nullable=True),
        sa.Column("height_px", sa.Integer(), nullable=True),
        sa.Column("bbox", postgresql.JSONB(), nullable=True),
        sa.Column("nearby_text", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("extraction_method", sa.Text(), nullable=False),
        sa.Column(
            "interpretation_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("interpretation", postgresql.JSONB(), nullable=True),
        sa.Column("warnings", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("document_id", "image_hash", name="uq_knowledge_figure_hash"),
    )
    op.create_index(
        "ix_knowledge_figures_document_page",
        "knowledge_figures",
        ["document_id", "page_number"],
    )

    op.create_table(
        "knowledge_tables",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "document_id",
            sa.BigInteger(),
            sa.ForeignKey("knowledge_source_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("table_index", sa.Integer(), nullable=False),
        sa.Column("table_hash", sa.String(64), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("cleaned_text", sa.Text(), nullable=True),
        sa.Column("structured_rows", postgresql.JSONB(), nullable=True),
        sa.Column("extraction_method", sa.Text(), nullable=False),
        sa.Column(
            "interpretation_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("interpretation", postgresql.JSONB(), nullable=True),
        sa.Column("warnings", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("document_id", "table_hash", name="uq_knowledge_table_hash"),
    )
    op.create_index(
        "ix_knowledge_tables_document_page",
        "knowledge_tables",
        ["document_id", "page_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_tables_document_page", table_name="knowledge_tables")
    op.drop_table("knowledge_tables")
    op.drop_index("ix_knowledge_figures_document_page", table_name="knowledge_figures")
    op.drop_table("knowledge_figures")
