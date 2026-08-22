"""Add Knowledge Bank source, object, retrieval, and recommendation tables.

Revision ID: 0017_knowledge_bank
Revises: 0016_event_innovation
Create Date: 2026-08-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0017_knowledge_bank"
down_revision = "0016_event_innovation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_source_documents",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("file_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("original_path", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("publisher", sa.Text(), nullable=True),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("date_confidence", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(12), nullable=True),
        sa.Column(
            "document_type",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'market_research'"),
        ),
        sa.Column(
            "extraction_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("extraction_version", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_warnings", postgresql.JSONB(), nullable=True),
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
    )

    op.create_table(
        "knowledge_source_files",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("original_path", sa.Text(), nullable=False, unique=True),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column(
            "document_id",
            sa.BigInteger(),
            sa.ForeignKey("knowledge_source_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "duplicate_of_document_id",
            sa.BigInteger(),
            sa.ForeignKey("knowledge_source_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "is_duplicate",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("file_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
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
    )
    op.create_index(
        "ix_knowledge_source_files_hash",
        "knowledge_source_files",
        ["file_hash"],
    )

    op.create_table(
        "knowledge_document_pages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "document_id",
            sa.BigInteger(),
            sa.ForeignKey("knowledge_source_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("cleaned_text", sa.Text(), nullable=True),
        sa.Column("extraction_version", sa.Text(), nullable=False),
        sa.Column("extraction_warnings", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("document_id", "page_number", name="uq_knowledge_doc_page"),
    )

    op.create_table(
        "knowledge_document_sections",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "document_id",
            sa.BigInteger(),
            sa.ForeignKey("knowledge_source_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section_order", sa.Integer(), nullable=False),
        sa.Column("section_title", sa.Text(), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("cleaned_text", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_knowledge_sections_document_order",
        "knowledge_document_sections",
        ["document_id", "section_order"],
    )

    op.create_table(
        "knowledge_objects",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "document_id",
            sa.BigInteger(),
            sa.ForeignKey("knowledge_source_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "section_id",
            sa.BigInteger(),
            sa.ForeignKey("knowledge_document_sections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("analyst", sa.Text(), nullable=True),
        sa.Column("institution", sa.Text(), nullable=True),
        sa.Column("knowledge_type", sa.Text(), nullable=False),
        sa.Column("concise_statement", sa.Text(), nullable=False),
        sa.Column("detailed_explanation", sa.Text(), nullable=True),
        sa.Column("supporting_passage", sa.Text(), nullable=True),
        sa.Column("assets", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("instruments", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("countries", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("central_banks", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("macro_themes", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("event_types", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column(
            "market_regime",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("direction", sa.Text(), nullable=True),
        sa.Column("time_horizon", sa.Text(), nullable=True),
        sa.Column("confidence_language", sa.Text(), nullable=True),
        sa.Column(
            "supporting_evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "contradictory_evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "catalysts",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "risks",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "invalidation_conditions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("attribution_type", sa.Text(), nullable=False),
        sa.Column("extraction_model", sa.Text(), nullable=True),
        sa.Column("extraction_version", sa.Text(), nullable=True),
        sa.Column("extraction_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "review_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
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
    )
    op.create_index(
        "ix_knowledge_objects_type_date",
        "knowledge_objects",
        ["knowledge_type", "publication_date"],
    )
    op.create_index(
        "ix_knowledge_objects_review",
        "knowledge_objects",
        ["review_status"],
    )

    op.create_table(
        "knowledge_relationships",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "source_object_id",
            sa.BigInteger(),
            sa.ForeignKey("knowledge_objects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_object_id",
            sa.BigInteger(),
            sa.ForeignKey("knowledge_objects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relationship_type", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Text(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "source_object_id",
            "target_object_id",
            "relationship_type",
            name="uq_knowledge_relationship",
        ),
    )

    op.create_table(
        "knowledge_embeddings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "object_id",
            sa.BigInteger(),
            sa.ForeignKey("knowledge_objects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column("embedding_version", sa.Text(), nullable=False),
        sa.Column("embedding", postgresql.JSONB(), nullable=True),
        sa.Column("embedding_text_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "object_id",
            "embedding_model",
            "embedding_version",
            name="uq_knowledge_embedding_version",
        ),
    )

    op.create_table(
        "knowledge_news_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("affected_entities", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("affected_assets", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("event_category", sa.Text(), nullable=True),
        sa.Column("novelty_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("impact_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("duplicate_cluster", sa.Text(), nullable=True),
        sa.Column(
            "processing_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("provider", "external_id", name="uq_knowledge_news_provider_id"),
    )

    op.create_table(
        "knowledge_recommendations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("recommendation_id", sa.String(64), nullable=False, unique=True),
        sa.Column("analysis_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "headline_event_id",
            sa.BigInteger(),
            sa.ForeignKey("knowledge_news_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "information_available",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "prices_used",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "retrieved_research_objects",
            postgresql.ARRAY(sa.BigInteger()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "scenarios",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "probabilities",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("selected_instrument", sa.Text(), nullable=True),
        sa.Column("direction", sa.Text(), nullable=True),
        sa.Column("proposed_entry", sa.Text(), nullable=True),
        sa.Column("stop", sa.Text(), nullable=True),
        sa.Column(
            "targets",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("horizon", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Text(), nullable=True),
        sa.Column("invalidation", sa.Text(), nullable=True),
        sa.Column(
            "recommendation_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("supersedes_recommendation_id", sa.String(64), nullable=True),
        sa.Column("entry_triggered", sa.Boolean(), nullable=True),
        sa.Column("entry_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_reason", sa.Text(), nullable=True),
        sa.Column("return_value", sa.Numeric(12, 6), nullable=True),
        sa.Column("maximum_favourable_excursion", sa.Numeric(12, 6), nullable=True),
        sa.Column("maximum_adverse_excursion", sa.Numeric(12, 6), nullable=True),
        sa.Column("thesis_outcome", sa.Text(), nullable=True),
        sa.Column("execution_outcome", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_knowledge_recommendations_analysis_ts",
        "knowledge_recommendations",
        ["analysis_timestamp"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_recommendations_analysis_ts",
        table_name="knowledge_recommendations",
    )
    op.drop_table("knowledge_recommendations")
    op.drop_table("knowledge_news_events")
    op.drop_table("knowledge_embeddings")
    op.drop_table("knowledge_relationships")
    op.drop_index("ix_knowledge_objects_review", table_name="knowledge_objects")
    op.drop_index("ix_knowledge_objects_type_date", table_name="knowledge_objects")
    op.drop_table("knowledge_objects")
    op.drop_index(
        "ix_knowledge_sections_document_order",
        table_name="knowledge_document_sections",
    )
    op.drop_table("knowledge_document_sections")
    op.drop_table("knowledge_document_pages")
    op.drop_index("ix_knowledge_source_files_hash", table_name="knowledge_source_files")
    op.drop_table("knowledge_source_files")
    op.drop_table("knowledge_source_documents")
