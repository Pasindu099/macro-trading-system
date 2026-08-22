# Knowledge Bank

The Knowledge Bank is the durable corpus layer for market research. It stores original file references, SHA-256 hashes, page-level extraction, cleaned sections, structured knowledge objects, relationships, embeddings, news events, and immutable recommendations as separate records.

Embeddings and model outputs are derived artifacts. The canonical store is the database plus immutable source PDFs on disk.

## Architecture

- Source archive: `knowledge_source_files` records every discovered PDF path. `knowledge_source_documents` stores one canonical document per SHA-256 hash.
- Page representation: `knowledge_document_pages` stores raw and cleaned page text with page numbers.
- Sections: `knowledge_document_sections` stores first-pass page sections. Phase B can replace this with stronger document structure without touching raw pages.
- Visual artifacts: `knowledge_figures` stores extracted chart/image artifacts with page provenance and local artifact paths. `knowledge_tables` stores detected table-like text with parsed rows where possible.
- Structured knowledge: `knowledge_objects` and `knowledge_relationships` are ready for claims, frameworks, causal chains, risks, invalidations, trades, and thesis evolution.
- Search artifacts: `knowledge_embeddings` stores regenerable embedding vectors and embedding text hashes.
- News and recommendations: `knowledge_news_events` and `knowledge_recommendations` prepare the manual headline and accountability workflow.

## Local Setup

From PowerShell:

```powershell
cd C:\Users\wpmpo\OneDrive\Documents\macro-dashboard
Copy-Item .env.example .env
docker compose up -d postgres redis
alembic upgrade head
```

The dashboard uses FastAPI, Jinja templates, SQLAlchemy async sessions, Alembic, PostgreSQL, and Redis. PDF extraction currently uses `pypdf`, which is already declared in `pyproject.toml`.

## Ingest Brent Research

The first-pass scanner defaults to the local `Brent Research` folder:

```powershell
python -m app.knowledge.ingestion scan
```

Then extract visual artifacts and table candidates:

```powershell
python -m app.knowledge.ingestion visuals
```

To re-score existing image artifacts after improving the filter:

```powershell
python -m app.knowledge.ingestion visuals --classify-existing
```

When running inside Docker:

```powershell
docker exec -w /app macro_dashboard_app python -m app.knowledge.ingestion visuals
```

To scan a different folder:

```powershell
python -m app.knowledge.ingestion scan --path "C:\Users\wpmpo\OneDrive\Documents\macro-dashboard\Brent Research"
```

The command is idempotent. It:

- discovers PDFs recursively
- calculates a SHA-256 hash
- registers every source path
- deduplicates canonical documents by hash
- extracts page-level raw and cleaned text
- detects publication dates from filename/content where possible
- records warnings for missing metadata
- replaces page/section extraction for a document when reprocessed
- extracts embedded chart/image artifacts as derived local files
- filters obvious non-market/low-information images before they enter the visible review set
- detects table-like page text and stores rows for review

Original PDFs are not renamed, moved, edited, or deleted.

## Start the Dashboard

```powershell
docker compose up -d --build
```

Open:

```text
http://localhost:8000/knowledge-bank
```

## Run Tests

```powershell
python -m pytest tests\unit\test_knowledge_ingestion.py tests\unit\test_knowledge_schemas.py tests\unit\test_knowledge_retrieval.py
```

Or run the full suite:

```powershell
python -m pytest
```

## Reprocess Documents

Phase A supports full reprocessing through the scan command:

```powershell
python -m app.knowledge.ingestion scan --extraction-version pypdf-page-v1
```

Visual artifacts can be regenerated because they are derived from immutable PDFs:

```powershell
python -m app.knowledge.ingestion visuals
```

Existing visual artifacts can be reclassified without touching the original PDFs:

```powershell
python -m app.knowledge.ingestion visuals --classify-existing
```

A single-document reprocess command and human review editing endpoints are planned for Phase B.

## Duplicate Detection

Duplicates are detected by SHA-256 hash. If two paths point to identical bytes, the first hash creates the canonical document and later paths are recorded as duplicate source files. Different filenames do not bypass duplicate detection.

## Review Warnings

Open `/knowledge-bank` and filter by `Needs Review`. Common warning causes:

- publication date missing
- author missing
- institution missing
- no extractable page text

The detail page shows source files, warnings, cleaned sections, page text, and later structured objects.

## Future Providers

Do not hard-code a news provider. Future feeds should normalize into `knowledge_news_events` with provider, external ID, headline, body, source, timestamps, affected assets/entities, category, novelty, impact, duplicate cluster, and processing status.

## Current Limitations

- Phase A uses `pypdf` rather than Docling. The schema is versioned so Docling/PyMuPDF extraction can be added later and rerun.
- Visual extraction uses PyMuPDF and captures embedded image artifacts. It filters obvious non-market images and keeps borderline chart candidates for review, but it does not yet interpret chart meaning. Vision-model interpretation is the next layer.
- Phase A does not call an LLM or extract gold-standard claims/frameworks/trades yet.
- The review workflow UI is read-only in Phase A.
- Hybrid retrieval has deterministic ranking primitives and tests, but no embedding generation yet.
- Manual headline analysis schemas and no-trade guardrails are present, but the dashboard page is planned for Phase D.
