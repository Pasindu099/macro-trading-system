"""Idempotent local PDF ingestion for the Knowledge Bank."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    KnowledgeDocumentPage,
    KnowledgeDocumentSection,
    KnowledgeSourceDocument,
    KnowledgeSourceFile,
)
from app.db.session import session_scope
from app.knowledge.visuals import (
    extract_visual_artifacts,
    format_visual_summary,
    reclassify_existing_figures,
)

DEFAULT_RESEARCH_DIR = Path("Brent Research")
EXTRACTION_VERSION = "pypdf-page-v1"
MONTH_RE = (
    r"January|February|March|April|May|June|July|August|September|October|November|December"
)
PUBLICATION_DATE_RE = re.compile(rf"\b({MONTH_RE})\s+(\d{{1,2}}),\s+(\d{{4}})\b")


@dataclass
class IngestionSummary:
    pdfs_discovered: int = 0
    new_pdfs: int = 0
    existing_unchanged_pdfs: int = 0
    duplicates: int = 0
    changed_files: int = 0
    successfully_processed: int = 0
    needs_review: int = 0
    failed: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    raw_text: str
    cleaned_text: str


@dataclass(frozen=True)
class ExtractedPdf:
    page_count: int
    title: str | None
    author: str | None
    metadata: dict[str, str]
    pages: list[ExtractedPage]


def discover_pdf_files(root: Path) -> list[Path]:
    resolved = root.resolve()
    if not resolved.exists():
        return []
    return sorted(
        path
        for path in resolved.rglob("*.pdf")
        if path.is_file() and not _has_path_traversal(path, resolved)
    )


def detect_duplicate_hashes(file_hashes: dict[Path, str]) -> dict[str, list[Path]]:
    grouped: dict[str, list[Path]] = {}
    for path, file_hash in file_hashes.items():
        grouped.setdefault(file_hash, []).append(path)
    return {
        file_hash: paths
        for file_hash, paths in grouped.items()
        if len(paths) > 1
    }


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_publication_date(
    filename: str,
    first_pages_text: str,
    metadata: dict[str, str] | None = None,
) -> tuple[date | None, str]:
    sources = [filename, first_pages_text]
    metadata = metadata or {}
    metadata_title = metadata.get("/Title") or metadata.get("title")
    if metadata_title:
        sources.append(metadata_title)
    for source in sources:
        parsed = _parse_month_date(source)
        if parsed:
            confidence = "high" if source != filename else "medium"
            return parsed, confidence
    return None, "missing"


def infer_institution(filename: str, title: str | None = None) -> str | None:
    text = f"{filename} {title or ''}".lower()
    if "spectra markets" in text:
        return "Spectra Markets"
    return None


def infer_author(text: str, institution: str | None) -> str | None:
    if "brent donnelly" in text.lower() or institution == "Spectra Markets":
        return "Brent Donnelly"
    return None


def clean_page_text(text: str, title: str | None = None) -> str:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split()).strip()
        if not line:
            continue
        if "spectramarkets.com/amfx" in line.lower():
            continue
        if re.fullmatch(r"\d{1,2}/\d{1,2}", line):
            continue
        if title and line == title:
            lines.append(line)
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def extract_pdf(path: Path) -> ExtractedPdf:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("Install pypdf to extract PDF text.") from exc

    reader = PdfReader(str(path))
    metadata = {str(k): str(v) for k, v in (reader.metadata or {}).items()}
    title = metadata.get("/Title")
    author = metadata.get("/Author")
    pages: list[ExtractedPage] = []
    for index, page in enumerate(reader.pages, start=1):
        raw_text = page.extract_text() or ""
        pages.append(
            ExtractedPage(
                page_number=index,
                raw_text=raw_text,
                cleaned_text=clean_page_text(raw_text, title),
            )
        )
    return ExtractedPdf(
        page_count=len(reader.pages),
        title=title,
        author=author,
        metadata=metadata,
        pages=pages,
    )


def segment_pages(pages: list[ExtractedPage]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for page in pages:
        lines = [line.strip() for line in page.cleaned_text.splitlines() if line.strip()]
        if not lines:
            continue
        heading = _first_meaningful_heading(lines)
        sections.append(
            {
                "section_title": heading,
                "page_start": page.page_number,
                "page_end": page.page_number,
                "raw_text": page.raw_text,
                "cleaned_text": page.cleaned_text,
                "section_order": len(sections) + 1,
            }
        )
    return sections


async def scan_corpus(
    session: AsyncSession,
    root: Path = DEFAULT_RESEARCH_DIR,
    extraction_version: str = EXTRACTION_VERSION,
) -> IngestionSummary:
    summary = IngestionSummary()
    pdf_paths = discover_pdf_files(root)
    summary.pdfs_discovered = len(pdf_paths)
    now = datetime.now(timezone.utc)

    for pdf_path in pdf_paths:
        path_text = str(pdf_path.resolve())
        try:
            file_hash = sha256_file(pdf_path)
        except OSError as exc:
            summary.failed += 1
            summary.warnings.append(f"Could not hash {pdf_path.name}: {exc}")
            continue

        existing_file = await _source_file_by_path(session, path_text)
        existing_doc = await _document_by_hash(session, file_hash)
        duplicate = existing_doc is not None and (
            existing_file is None or existing_file.document_id != existing_doc.id
        )

        if existing_file and existing_file.file_hash == file_hash:
            summary.existing_unchanged_pdfs += 1
            existing_file.last_seen_at = now
            existing_file.is_duplicate = bool(duplicate or existing_file.is_duplicate)
            continue
        if existing_file and existing_file.file_hash != file_hash:
            summary.changed_files += 1

        if existing_doc:
            document = existing_doc
            summary.duplicates += 1
            duplicate_of_document_id = document.id
            is_duplicate = True
        else:
            document = KnowledgeSourceDocument(
                file_hash=file_hash,
                original_filename=pdf_path.name,
                original_path=path_text,
                document_type="market_research",
                extraction_status="pending",
                extraction_version=extraction_version,
                first_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(document)
            await session.flush()
            summary.new_pdfs += 1
            duplicate_of_document_id = None
            is_duplicate = False

        if existing_file:
            source_file = existing_file
            source_file.file_hash = file_hash
            source_file.document_id = document.id
            source_file.duplicate_of_document_id = duplicate_of_document_id
            source_file.is_duplicate = is_duplicate
            source_file.last_seen_at = now
            source_file.updated_at = now
        else:
            source_file = KnowledgeSourceFile(
                original_filename=pdf_path.name,
                original_path=path_text,
                file_hash=file_hash,
                document_id=document.id,
                duplicate_of_document_id=duplicate_of_document_id,
                is_duplicate=is_duplicate,
                file_size_bytes=pdf_path.stat().st_size,
                file_modified_at=datetime.fromtimestamp(
                    pdf_path.stat().st_mtime,
                    tz=timezone.utc,
                ),
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(source_file)
            await session.flush()

        if is_duplicate and existing_doc:
            document.processing_warnings = _append_warning(
                document.processing_warnings,
                f"Duplicate file discovered: {path_text}",
            )
            document.updated_at = now
            continue

        try:
            extracted = extract_pdf(pdf_path)
            first_text = "\n".join(page.raw_text for page in extracted.pages[:2])
            publication_date, date_confidence = parse_publication_date(
                pdf_path.name,
                first_text,
                extracted.metadata,
            )
            institution = infer_institution(pdf_path.name, extracted.title)
            author = extracted.author or infer_author(first_text, institution)
            warnings = []
            if not publication_date:
                warnings.append("Publication date could not be detected.")
            if not author:
                warnings.append("Author could not be detected.")
            if not institution:
                warnings.append("Institution could not be detected.")
            if not any(page.cleaned_text for page in extracted.pages):
                warnings.append("No extractable page text found.")

            document.title = extracted.title or pdf_path.stem
            document.author = author
            document.publisher = institution
            document.publication_date = publication_date
            document.date_confidence = date_confidence
            document.page_count = extracted.page_count
            document.language = "en"
            document.extraction_status = "needs_review" if warnings else "processed"
            document.extraction_version = extraction_version
            document.last_processed_at = now
            document.processing_warnings = warnings
            document.updated_at = now

            await _replace_pages_and_sections(session, document.id, extracted.pages)
            if warnings:
                summary.needs_review += 1
            else:
                summary.successfully_processed += 1
        except Exception as exc:
            document.extraction_status = "failed"
            document.last_processed_at = now
            document.processing_warnings = _append_warning(
                document.processing_warnings,
                f"Extraction failed: {exc}",
            )
            document.updated_at = now
            summary.failed += 1

    return summary


def format_summary(summary: IngestionSummary) -> str:
    data = summary.to_dict()
    labels = {
        "pdfs_discovered": "PDFs discovered",
        "new_pdfs": "New PDFs",
        "existing_unchanged_pdfs": "Existing unchanged PDFs",
        "duplicates": "Duplicates",
        "changed_files": "Changed files",
        "successfully_processed": "Successfully processed",
        "needs_review": "Needs review",
        "failed": "Failed",
    }
    lines = [f"{label}: {data[key]}" for key, label in labels.items()]
    if summary.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in summary.warnings)
    return "\n".join(lines)


async def _replace_pages_and_sections(
    session: AsyncSession,
    document_id: int,
    pages: list[ExtractedPage],
) -> None:
    await session.execute(
        delete(KnowledgeDocumentSection).where(
            KnowledgeDocumentSection.document_id == document_id
        )
    )
    await session.execute(
        delete(KnowledgeDocumentPage).where(KnowledgeDocumentPage.document_id == document_id)
    )
    for page in pages:
        session.add(
            KnowledgeDocumentPage(
                document_id=document_id,
                page_number=page.page_number,
                raw_text=page.raw_text,
                cleaned_text=page.cleaned_text,
                extraction_version=EXTRACTION_VERSION,
            )
        )
    for section in segment_pages(pages):
        session.add(KnowledgeDocumentSection(document_id=document_id, **section))
    await session.flush()


async def _source_file_by_path(
    session: AsyncSession,
    original_path: str,
) -> KnowledgeSourceFile | None:
    result = await session.execute(
        select(KnowledgeSourceFile).where(KnowledgeSourceFile.original_path == original_path)
    )
    return result.scalar_one_or_none()


async def _document_by_hash(
    session: AsyncSession,
    file_hash: str,
) -> KnowledgeSourceDocument | None:
    result = await session.execute(
        select(KnowledgeSourceDocument).where(KnowledgeSourceDocument.file_hash == file_hash)
    )
    return result.scalar_one_or_none()


def _parse_month_date(text: str) -> date | None:
    match = PUBLICATION_DATE_RE.search(text)
    if not match:
        return None
    month_name, day, year = match.groups()
    return datetime.strptime(f"{month_name} {day} {year}", "%B %d %Y").date()


def _first_meaningful_heading(lines: list[str]) -> str:
    for line in lines[:8]:
        if len(line) <= 120 and not re.search(r"spectramarkets\.com|^\d+/\d+$", line):
            return line
    return f"Page section"


def _append_warning(existing: list[str] | None, warning: str) -> list[str]:
    warnings = list(existing or [])
    if warning not in warnings:
        warnings.append(warning)
    return warnings


def _has_path_traversal(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return True
    return False


async def _run_scan(args: argparse.Namespace) -> None:
    async with session_scope() as session:
        summary = await scan_corpus(
            session,
            root=Path(args.path),
            extraction_version=args.extraction_version,
        )
    print(format_summary(summary))


async def _run_visuals(args: argparse.Namespace) -> None:
    async with session_scope() as session:
        if args.classify_existing:
            summary = await reclassify_existing_figures(session)
        else:
            summary = await extract_visual_artifacts(
                session,
                output_dir=Path(args.output_dir),
                limit=args.limit,
                document_id=args.document_id,
                progress=args.progress,
            )
    print(format_visual_summary(summary))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Knowledge Bank ingestion")
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan = subparsers.add_parser("scan", help="Scan and ingest local research PDFs")
    scan.add_argument(
        "--path",
        default=str(DEFAULT_RESEARCH_DIR),
        help="Folder to scan recursively for PDFs",
    )
    scan.add_argument(
        "--extraction-version",
        default=EXTRACTION_VERSION,
        help="Extraction version label used for idempotent reprocessing",
    )
    scan.set_defaults(func=_run_scan)
    visuals = subparsers.add_parser(
        "visuals",
        help="Extract figure/image artifacts and table candidates from registered PDFs",
    )
    visuals.add_argument(
        "--output-dir",
        default="data/knowledge_bank/visuals",
        help="Directory for derived image artifacts",
    )
    visuals.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum number of registered documents to process",
    )
    visuals.add_argument(
        "--document-id",
        type=int,
        default=None,
        help="Process one registered document ID",
    )
    visuals.add_argument(
        "--progress",
        action="store_true",
        help="Print each document as it is processed",
    )
    visuals.add_argument(
        "--classify-existing",
        action="store_true",
        help="Reclassify existing figure rows and mark obvious non-market images ignored",
    )
    visuals.set_defaults(func=_run_visuals)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
