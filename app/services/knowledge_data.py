"""Knowledge document records for the replacement Data view."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    KnowledgeDocumentPage, KnowledgeDocumentSection, KnowledgeFigure, KnowledgeObject,
    KnowledgeSourceDocument, KnowledgeSourceFile, KnowledgeTable,
)


def _record(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


async def get_knowledge_document_detail(session: AsyncSession, document_id: int) -> dict[str, Any] | None:
    """Return a document and its six associated record sets."""
    document = (await session.execute(
        select(KnowledgeSourceDocument).where(KnowledgeSourceDocument.id == document_id)
    )).scalar_one_or_none()
    if document is None:
        return None
    files = (await session.execute(
        select(KnowledgeSourceFile).where(KnowledgeSourceFile.document_id == document_id)
        .order_by(KnowledgeSourceFile.is_duplicate.asc(), KnowledgeSourceFile.original_filename.asc())
    )).scalars().all()
    pages = (await session.execute(
        select(KnowledgeDocumentPage).where(KnowledgeDocumentPage.document_id == document_id)
        .order_by(KnowledgeDocumentPage.page_number.asc())
    )).scalars().all()
    sections = (await session.execute(
        select(KnowledgeDocumentSection).where(KnowledgeDocumentSection.document_id == document_id)
        .order_by(KnowledgeDocumentSection.section_order.asc())
    )).scalars().all()
    objects = (await session.execute(
        select(KnowledgeObject).where(KnowledgeObject.document_id == document_id)
        .order_by(KnowledgeObject.page_start.asc().nullslast(), KnowledgeObject.id.asc())
    )).scalars().all()
    figures = (await session.execute(
        select(KnowledgeFigure).where(
            KnowledgeFigure.document_id == document_id,
            KnowledgeFigure.interpretation_status.not_like("ignored%"),
        ).order_by(KnowledgeFigure.page_number.asc(), KnowledgeFigure.figure_index.asc())
    )).scalars().all()
    tables = (await session.execute(
        select(KnowledgeTable).where(KnowledgeTable.document_id == document_id)
        .order_by(KnowledgeTable.page_number.asc(), KnowledgeTable.table_index.asc())
    )).scalars().all()
    return {"document": _record(document), "files": [_record(row) for row in files],
            "pages": [_record(row) for row in pages], "sections": [_record(row) for row in sections],
            "objects": [_record(row) for row in objects], "figures": [_record(row) for row in figures],
            "tables": [_record(row) for row in tables]}
