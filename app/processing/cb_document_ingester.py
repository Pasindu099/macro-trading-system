"""CB policy document ingester — scans data/policy/, extracts PDF text,
runs combined AI tone + projection analysis, and upserts to the DB.

Handles manually downloaded CB policy PDFs organised as:
  data/policy/{BANK}/.../{filename}.pdf

Date and doc-type are inferred from the file path and filename.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models import CbPolicyDocument, CbEconomicProjection
from app.settings import get_settings

logger = logging.getLogger(__name__)

POLICY_DIR = Path("data/policy")
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

_MONTHS = {
    # full names
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    # 3-letter abbreviations (match these AFTER full names so "march" beats "mar")
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9,
    "oct": 10, "nov": 11, "dec": 12,
    # "may" already covered above; no "may" abbreviation needed
}

# Banks that produce full monetary policy reports (embedded projections)
_REPORT_BANKS = {"BOE", "RBA", "RBNZ", "BOC", "SNB"}

_SYSTEM_PROMPT = """\
You are a senior macro economist and FX strategist specialising in central bank \
monetary policy. Analyse official central bank documents and extract structured \
intelligence for FX traders.

Return ONLY valid JSON — no markdown fences, no explanation, no preamble. \
Use the exact field names and value options specified."""

# ── Validation sets (copied inline to avoid circular imports) ─────────────────

_VALID_TONE   = frozenset([
    "extremely_dovish", "dovish", "slightly_dovish", "neutral",
    "slightly_hawkish", "hawkish", "extremely_hawkish",
])
_VALID_INFL   = frozenset(["falling", "below_target", "stable", "rising", "well_above_target"])
_VALID_GROWTH = frozenset(["weak", "slowing", "moderate", "strong"])
_VALID_LABOR  = frozenset(["loose", "easing", "balanced", "tight"])


def _safe_float(v: Any, lo: float = -5.0, hi: float = 5.0) -> Decimal | None:
    try:
        return Decimal(str(max(lo, min(hi, round(float(v), 2)))))
    except (TypeError, ValueError):
        return None


def _safe_str(v: Any, max_len: int = 200) -> str | None:
    if v is None:
        return None
    return str(v).strip()[:max_len] or None


def _safe_list(v: Any, max_items: int = 5, max_len: int = 120) -> list[str] | None:
    if not isinstance(v, list):
        return None
    return [str(i).strip()[:max_len] for i in v if i][:max_items] or None


def _validated(raw: dict[str, Any]) -> dict[str, Any]:
    tone_label = str(raw.get("tone_label", "neutral")).lower()
    if tone_label not in _VALID_TONE:
        tone_label = "neutral"
    inflation = str(raw.get("inflation_outlook", "stable")).lower()
    if inflation not in _VALID_INFL:
        inflation = "stable"
    growth = str(raw.get("growth_outlook", "moderate")).lower()
    if growth not in _VALID_GROWTH:
        growth = "moderate"
    labor = str(raw.get("labor_outlook", "balanced")).lower()
    if labor not in _VALID_LABOR:
        labor = "balanced"
    return {
        "tone_score":           _safe_float(raw.get("tone_score")),
        "tone_label":           tone_label,
        "inflation_outlook":    inflation,
        "inflation_summary":    _safe_str(raw.get("inflation_summary")),
        "growth_outlook":       growth,
        "growth_summary":       _safe_str(raw.get("growth_summary")),
        "labor_outlook":        labor,
        "labor_summary":        _safe_str(raw.get("labor_summary")),
        "key_phrases":          _safe_list(raw.get("key_phrases"), max_items=5),
        "tone_change_vs_prior": _safe_str(raw.get("tone_change_vs_prior"), 400),
        "retail_bullets":       _safe_list(raw.get("retail_bullets"), max_items=3, max_len=250),
    }


# ── Date / doc-type parsing ───────────────────────────────────────────────────

def _parse_date_and_type(bank: str, file_path: Path) -> tuple[date | None, str]:
    """Return (doc_date, doc_type) parsed from filename/path.

    Returns (None, 'statement') when the date cannot be determined.
    """
    stem = file_path.stem.lower()
    # normalise to forward-slash lowercase for all path checks
    path_lower = str(file_path).replace("\\", "/").lower()

    # ── Doc type ──────────────────────────────────────────────────────────────
    if "/statement/" in path_lower:
        doc_type = "statement"
    elif (
        "/projection/" in path_lower
        or "/projections/" in path_lower
        or "projections" in stem
    ):
        doc_type = "projection"
    elif bank in _REPORT_BANKS:
        doc_type = "report"
    elif bank == "BOJ":
        doc_type = "statement"
    elif bank == "ECB":
        # ECB main = statement, ECB/Projections covered above
        doc_type = "statement"
    else:
        doc_type = "statement"

    # ── Date ──────────────────────────────────────────────────────────────────
    doc_date: date | None = None

    if bank == "FED":
        m = re.search(r"(\d{8})", stem)
        if m:
            s = m.group(1)
            try:
                doc_date = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
            except ValueError:
                pass

    elif bank in ("BOE", "RBNZ"):
        # Look for month name + 4-digit year (e.g. "april-2026" or "May 2026")
        for month_name, month_num in _MONTHS.items():
            if month_name in stem:
                yr_m = re.search(r"(\d{4})", stem)
                if yr_m:
                    try:
                        doc_date = date(int(yr_m.group(1)), month_num, 1)
                    except ValueError:
                        pass
                else:
                    # short 2-digit year e.g. "mpsaug23", "MPSNov23"
                    yr2 = re.search(r"(\d{2})$", stem)
                    if yr2:
                        try:
                            doc_date = date(2000 + int(yr2.group(1)), month_num, 1)
                        except ValueError:
                            pass
                break
        if doc_date is None:
            # Fallback: YYYY-MM pattern in filename
            m = re.search(r"(\d{4})-(\d{2})", stem)
            if m:
                try:
                    doc_date = date(int(m.group(1)), int(m.group(2)), 1)
                except ValueError:
                    pass

    elif bank == "BOC":
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", stem)
        if m:
            try:
                doc_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass

    elif bank == "BOJ":
        m = re.search(r"k(\d{2})(\d{2})(\d{2})", stem)
        if m:
            try:
                doc_date = date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass

    elif bank == "ECB":
        # Projection: projections + YYYYMM
        if "projections" in stem:
            mp = re.search(r"projections(\d{4})(\d{2})", stem)
            if mp:
                try:
                    doc_date = date(int(mp.group(1)), int(mp.group(2)), 1)
                except ValueError:
                    pass
        if doc_date is None:
            # Statement: .ds + YYMMDD
            ms = re.search(r"\.ds(\d{2})(\d{2})(\d{2})", file_path.name.lower())
            if ms:
                try:
                    doc_date = date(2000 + int(ms.group(1)), int(ms.group(2)), int(ms.group(3)))
                except ValueError:
                    pass

    elif bank == "RBA":
        m = re.search(r"(\d{4})-(\d{2})(?:$|-)", stem)
        if m:
            try:
                doc_date = date(int(m.group(1)), int(m.group(2)), 1)
            except ValueError:
                pass

    elif bank == "SNB":
        m = re.search(r"(\d{8})", stem)
        if m:
            s = m.group(1)
            try:
                doc_date = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
            except ValueError:
                pass

    return doc_date, doc_type


# ── PDF text extraction ───────────────────────────────────────────────────────

def extract_pdf_text(path: Path, max_pages: int = 25) -> str:
    """Extract text from PDF using pypdf, up to max_pages."""
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.error("pypdf is not installed — cannot extract PDF text")
        return ""

    try:
        reader = PdfReader(str(path))
        pages_to_read = min(max_pages, len(reader.pages))
        parts: list[str] = []
        for i in range(pages_to_read):
            try:
                text = reader.pages[i].extract_text() or ""
                parts.append(text)
            except Exception as exc:
                logger.debug("Failed to extract page %d from %s: %s", i, path.name, exc)
        return "\n".join(parts)
    except Exception as exc:
        logger.error("Failed to read PDF %s: %s", path, exc)
        return ""


# ── OpenAI prompt ─────────────────────────────────────────────────────────────

_ANALYSIS_SCHEMA = """\
{
  "tone_score": <float -5.0=extremely dovish to +5.0=extremely hawkish>,
  "tone_label": <"extremely_dovish"|"dovish"|"slightly_dovish"|"neutral"|"slightly_hawkish"|"hawkish"|"extremely_hawkish">,
  "inflation_outlook": <"falling"|"below_target"|"stable"|"rising"|"well_above_target">,
  "inflation_summary": "<one sentence on the bank's inflation assessment>",
  "growth_outlook": <"weak"|"slowing"|"moderate"|"strong">,
  "growth_summary": "<one sentence on the bank's growth assessment>",
  "labor_outlook": <"loose"|"easing"|"balanced"|"tight">,
  "labor_summary": "<one sentence on the bank's labor market assessment>",
  "key_phrases": ["<phrase1>", "<phrase2>", "<phrase3>"],
  "tone_change_vs_prior": "<one sentence comparing to prior — use 'First data point' if no prior>",
  "retail_bullets": ["<implication 1 for FX traders>", "<implication 2>", "<implication 3>"],
  "projections": [
    {"metric": "inflation|gdp_growth|unemployment", "horizon": "<e.g. 2025 or 2025-Q4>", "horizon_year": <integer>, "value": <float>}
  ]
}"""


def _build_combined_prompt(
    bank: str,
    doc_date: date,
    doc_type: str,
    text: str,
    prior_text: str | None,
) -> str:
    """Build OpenAI prompt requesting tone analysis + projection extraction."""
    bank_names = {
        "FED": "Federal Reserve (FOMC)",
        "ECB": "European Central Bank",
        "BOE": "Bank of England",
        "BOJ": "Bank of Japan",
        "RBA": "Reserve Bank of Australia",
        "BOC": "Bank of Canada",
        "SNB": "Swiss National Bank",
        "RBNZ": "Reserve Bank of New Zealand",
    }
    bank_name = bank_names.get(bank, bank)
    doc_type_label = {
        "statement": "monetary policy statement",
        "projection": "economic projections / SEP document",
        "report": "monetary policy report",
        "upload": "policy document",
    }.get(doc_type, doc_type)

    parts = [
        f"Analyse this {bank_name} {doc_type_label} dated {doc_date.strftime('%B %d, %Y')}.",
        "",
        "=== DOCUMENT TEXT ===",
        text[:6000],
        "",
    ]

    if prior_text:
        parts += [
            "=== PREVIOUS DOCUMENT (for tone comparison) ===",
            prior_text[:1500],
            "",
        ]
    else:
        parts.append(
            "(No prior document in dataset — mark tone_change_vs_prior accordingly.)"
        )

    if doc_type == "projection":
        parts.append(
            "IMPORTANT: This is a projections document. Extract ALL numeric forecasts "
            "for inflation, GDP growth, and unemployment across all forecast horizons. "
            "The projections[] array is the primary output."
        )
    elif doc_type in ("report", "upload"):
        parts.append(
            "This document may contain embedded projections/forecasts. "
            "Extract any numeric forecasts found in the text into projections[]."
        )
    else:
        parts.append(
            "This is a rate decision statement. projections[] may be empty []."
        )

    parts += ["", "Return this exact JSON structure:", _ANALYSIS_SCHEMA]
    return "\n".join(parts)


# ── JSON parsing ──────────────────────────────────────────────────────────────

def _extract_json(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return None


# ── OpenAI call ───────────────────────────────────────────────────────────────

async def _call_openai(prompt: str) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.openai_api_key:
        logger.error("OPENAI_API_KEY not set — skipping analysis")
        return None
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                OPENAI_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.openai_model,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 1200,
                    "temperature": 0.2,
                },
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("OpenAI request failed: %s", exc)
        return None

    raw_text = (
        resp.json().get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    return _extract_json(raw_text)


# ── DB upsert helpers ─────────────────────────────────────────────────────────

async def _upsert_document(
    session: AsyncSession,
    bank: str,
    doc_date: date,
    doc_type: str,
    filename: str,
    file_path: str,
    raw_text: str,
    analysis: dict[str, Any] | None,
) -> CbPolicyDocument:
    """Upsert a CbPolicyDocument row and return the model instance."""
    now = datetime.now(timezone.utc)
    validated = _validated(analysis) if analysis else {}

    values: dict[str, Any] = {
        "bank": bank,
        "doc_date": doc_date,
        "doc_type": doc_type,
        "filename": filename,
        "file_path": file_path,
        "raw_text": raw_text[:50000] if raw_text else None,
    }
    if analysis:
        values.update({
            "tone_score": validated.get("tone_score"),
            "tone_label": validated.get("tone_label"),
            "inflation_outlook": validated.get("inflation_outlook"),
            "inflation_summary": validated.get("inflation_summary"),
            "growth_outlook": validated.get("growth_outlook"),
            "growth_summary": validated.get("growth_summary"),
            "labor_outlook": validated.get("labor_outlook"),
            "labor_summary": validated.get("labor_summary"),
            "key_phrases": validated.get("key_phrases"),
            "tone_change_vs_prior": validated.get("tone_change_vs_prior"),
            "retail_bullets": validated.get("retail_bullets"),
            "full_analysis": analysis,
            "analyzed_at": now,
        })

    stmt = (
        pg_insert(CbPolicyDocument)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_cb_policy_docs",
            set_={k: v for k, v in values.items() if k not in ("bank", "doc_date", "doc_type")},
        )
        .returning(CbPolicyDocument.id)
    )
    result = await session.execute(stmt)
    doc_id = result.scalar_one()
    await session.flush()

    doc_q = await session.execute(
        select(CbPolicyDocument).where(CbPolicyDocument.id == doc_id)
    )
    return doc_q.scalar_one()


async def _upsert_projections(
    session: AsyncSession,
    bank: str,
    projection_date: date,
    projections: list[dict[str, Any]],
    source_doc_id: int,
) -> int:
    """Upsert projection rows. Returns the count inserted/updated."""
    if not projections:
        return 0

    count = 0
    for proj in projections:
        metric = str(proj.get("metric", "")).lower()
        horizon = str(proj.get("horizon", "")).strip()
        if not metric or not horizon:
            continue

        try:
            value = float(proj.get("value", 0))
        except (TypeError, ValueError):
            continue

        horizon_year: int | None = None
        try:
            horizon_year = int(proj.get("horizon_year") or horizon[:4])
        except (TypeError, ValueError):
            pass

        row_values: dict[str, Any] = {
            "bank": bank,
            "projection_date": projection_date,
            "horizon_label": horizon[:30],
            "horizon_year": horizon_year,
            "source_doc_id": source_doc_id,
        }
        if metric == "inflation":
            row_values["inflation_forecast"] = Decimal(str(round(value, 3)))
        elif metric == "gdp_growth":
            row_values["gdp_forecast"] = Decimal(str(round(value, 3)))
        elif metric == "unemployment":
            row_values["unemployment_forecast"] = Decimal(str(round(value, 3)))
        else:
            continue

        stmt = (
            pg_insert(CbEconomicProjection)
            .values(**row_values)
            .on_conflict_do_update(
                constraint="uq_cb_econ_proj",
                set_={k: v for k, v in row_values.items()
                      if k not in ("bank", "projection_date", "horizon_label")},
            )
        )
        await session.execute(stmt)
        count += 1

    await session.flush()
    return count


# ── Main ingestion ────────────────────────────────────────────────────────────

async def ingest_documents(
    session: AsyncSession,
    bank: str | None = None,
    reanalyze: bool = False,
) -> dict[str, int]:
    """Scan POLICY_DIR, ingest new PDFs, run analysis.

    Returns counts: {"scanned": N, "new": N, "analyzed": N, "projections": N, "errors": N}
    """
    if not POLICY_DIR.exists():
        logger.warning("POLICY_DIR %s does not exist", POLICY_DIR)
        return {"scanned": 0, "new": 0, "analyzed": 0, "projections": 0, "errors": 0}

    counts = {"scanned": 0, "new": 0, "analyzed": 0, "projections": 0, "errors": 0}

    # Determine which bank directories to scan
    if bank:
        bank_dirs = [POLICY_DIR / bank] if (POLICY_DIR / bank).is_dir() else []
    else:
        bank_dirs = [d for d in POLICY_DIR.iterdir() if d.is_dir()]

    for bank_dir in bank_dirs:
        bank_code = bank_dir.name.upper()
        # Skip non-bank directories
        if bank_code not in {
            "FED", "ECB", "BOE", "BOJ", "RBA", "BOC", "SNB", "RBNZ"
        }:
            continue

        pdf_files = list(bank_dir.rglob("*.pdf"))
        counts["scanned"] += len(pdf_files)

        # Load existing docs for this bank to check for prior texts
        existing_q = await session.execute(
            select(CbPolicyDocument)
            .where(CbPolicyDocument.bank == bank_code)
            .order_by(CbPolicyDocument.doc_date.asc())
        )
        existing_by_key: dict[tuple[str, date, str], CbPolicyDocument] = {
            (d.bank, d.doc_date, d.doc_type): d
            for d in existing_q.scalars().all()
        }

        for pdf_path in sorted(pdf_files):
            doc_date, doc_type = _parse_date_and_type(bank_code, pdf_path)

            if doc_date is None:
                logger.warning("Could not parse date from %s — skipping", pdf_path.name)
                counts["errors"] += 1
                continue

            key = (bank_code, doc_date, doc_type)
            existing = existing_by_key.get(key)

            if existing and not reanalyze and existing.analyzed_at is not None:
                # Already analyzed — skip
                continue

            counts["new"] += 1
            logger.info("Ingesting %s / %s / %s", bank_code, doc_date, pdf_path.name)

            # Extract text
            raw_text = extract_pdf_text(pdf_path)
            if not raw_text.strip():
                logger.warning("No text extracted from %s", pdf_path.name)
                counts["errors"] += 1
                continue

            # Find prior document text for tone comparison
            prior_text: str | None = None
            same_type_docs = sorted(
                [d for k, d in existing_by_key.items()
                 if k[0] == bank_code and k[2] == doc_type and k[1] < doc_date],
                key=lambda d: d.doc_date,
            )
            if same_type_docs and same_type_docs[-1].raw_text:
                prior_text = same_type_docs[-1].raw_text[:1500]

            # Build and send prompt
            prompt = _build_combined_prompt(
                bank_code, doc_date, doc_type, raw_text, prior_text
            )
            try:
                analysis = await _call_openai(prompt)
            except Exception as exc:
                logger.error("OpenAI call failed for %s: %s", pdf_path.name, exc)
                counts["errors"] += 1
                analysis = None

            if analysis:
                counts["analyzed"] += 1

            # Upsert document
            try:
                doc = await _upsert_document(
                    session,
                    bank=bank_code,
                    doc_date=doc_date,
                    doc_type=doc_type,
                    filename=pdf_path.name,
                    file_path=str(pdf_path),
                    raw_text=raw_text,
                    analysis=analysis,
                )
                # Update the in-memory cache
                existing_by_key[key] = doc
            except Exception as exc:
                logger.error("Failed to upsert document %s: %s", pdf_path.name, exc)
                counts["errors"] += 1
                await session.rollback()
                continue

            # Upsert projections if any
            if analysis and analysis.get("projections"):
                try:
                    proj_count = await _upsert_projections(
                        session,
                        bank=bank_code,
                        projection_date=doc_date,
                        projections=analysis["projections"],
                        source_doc_id=doc.id,
                    )
                    counts["projections"] += proj_count
                except Exception as exc:
                    logger.error("Failed to upsert projections for %s: %s", pdf_path.name, exc)

    await session.commit()
    return counts


async def ingest_uploaded_file(
    session: AsyncSession,
    bank: str,
    doc_date: date,
    doc_type: str,
    filename: str,
    file_bytes: bytes,
) -> CbPolicyDocument:
    """Save an uploaded PDF to disk and analyze it.

    Saves to data/policy/{bank}/uploads/{filename}.
    Returns the upserted CbPolicyDocument.
    """
    upload_dir = POLICY_DIR / bank / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    dest = upload_dir / filename
    dest.write_bytes(file_bytes)
    logger.info("Saved uploaded PDF to %s", dest)

    # Extract text
    raw_text = extract_pdf_text(dest)

    # Find prior document for comparison (any doc for this bank, closest date before)
    prior_q = await session.execute(
        select(CbPolicyDocument)
        .where(
            CbPolicyDocument.bank == bank,
            CbPolicyDocument.doc_date < doc_date,
        )
        .order_by(CbPolicyDocument.doc_date.desc())
        .limit(1)
    )
    prior_doc = prior_q.scalar_one_or_none()
    prior_text = prior_doc.raw_text[:1500] if prior_doc and prior_doc.raw_text else None

    # AI analysis
    analysis: dict[str, Any] | None = None
    if raw_text.strip():
        prompt = _build_combined_prompt(bank, doc_date, doc_type, raw_text, prior_text)
        try:
            analysis = await _call_openai(prompt)
        except Exception as exc:
            logger.error("OpenAI call failed for upload %s: %s", filename, exc)

    # Upsert document
    doc = await _upsert_document(
        session,
        bank=bank,
        doc_date=doc_date,
        doc_type=doc_type,
        filename=filename,
        file_path=str(dest),
        raw_text=raw_text,
        analysis=analysis,
    )

    # Upsert projections
    if analysis and analysis.get("projections"):
        try:
            await _upsert_projections(
                session,
                bank=bank,
                projection_date=doc_date,
                projections=analysis["projections"],
                source_doc_id=doc.id,
            )
        except Exception as exc:
            logger.error("Failed to upsert projections for upload %s: %s", filename, exc)

    await session.commit()
    return doc


# ── Utility: count local PDFs ─────────────────────────────────────────────────

def count_local_pdfs(bank: str | None = None) -> int:
    """Return the number of PDF files found in POLICY_DIR."""
    if not POLICY_DIR.exists():
        return 0
    if bank:
        bank_dir = POLICY_DIR / bank.upper()
        if not bank_dir.is_dir():
            return 0
        return len(list(bank_dir.rglob("*.pdf")))
    return len(list(POLICY_DIR.rglob("*.pdf")))
