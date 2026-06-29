"""OpenAI-powered analysis of central bank policy statements.

For each scraped statement, we send the text to the OpenAI Chat Completions
API and get back a structured JSON with tone score, outlook directions, key
phrases, tone shift vs prior meeting, and retail-trader bullets.

Results are persisted to cb_policy_reports (analyze once, cache forever).
Each bank is capped at 12 reports — oldest are purged automatically.

Actual PDF documents from CB websites are downloaded and stored locally.
We keep only the last 2 PDFs per bank (oldest is deleted when a 3rd arrives).
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
from sqlalchemy import delete, desc, select, asc
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CbPolicyReport
from app.processing.cb_policy_scraper import StatementRecord
from app.settings import get_settings

logger = logging.getLogger(__name__)

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
MAX_REPORTS_PER_BANK = 12
MAX_PDFS_PER_BANK = 2

CB_POLICY_PDF_DIR = Path("data/cb_policy_pdfs")

_BANK_NAMES = {
    "FED":  "Federal Reserve (FOMC)",
    "ECB":  "European Central Bank",
    "BOE":  "Bank of England",
    "BOJ":  "Bank of Japan",
    "RBA":  "Reserve Bank of Australia",
    "BOC":  "Bank of Canada",
    "SNB":  "Swiss National Bank",
    "RBNZ": "Reserve Bank of New Zealand",
}

_SYSTEM_PROMPT = """\
You are a senior macro economist and FX strategist specialising in central bank \
monetary policy. Your task is to analyse official central bank statements and \
extract structured intelligence for FX traders.

Return ONLY valid JSON — no markdown fences, no explanation, no preamble. \
Use the exact field names and value options specified."""

_ANALYSIS_SCHEMA = """\
{
  "tone_score": <float, -5.0 = extremely dovish, 0 = neutral, +5.0 = extremely hawkish>,
  "tone_label": <"extremely_dovish"|"dovish"|"slightly_dovish"|"neutral"|"slightly_hawkish"|"hawkish"|"extremely_hawkish">,
  "inflation_outlook": <"falling"|"below_target"|"stable"|"rising"|"well_above_target">,
  "inflation_summary": "<one sentence on the bank's inflation assessment>",
  "growth_outlook": <"weak"|"slowing"|"moderate"|"strong">,
  "growth_summary": "<one sentence on the bank's growth assessment>",
  "labor_outlook": <"loose"|"easing"|"balanced"|"tight">,
  "labor_summary": "<one sentence on the bank's labor market assessment>",
  "key_phrases": ["<phrase1>", "<phrase2>", "<phrase3>"],
  "tone_change_vs_prior": "<one sentence comparing language to the previous meeting — use 'First data point — no prior meeting for comparison.' if no prior>",
  "retail_bullets": [
    "<key implication #1 for retail FX traders in plain English>",
    "<key implication #2>",
    "<key implication #3>"
  ]
}"""


# ── AI prompt ────────────────────────────────────────────────────────────────

def _build_prompt(bank: str, meeting_date: date, text: str, prior_text: str | None) -> str:
    bank_name = _BANK_NAMES.get(bank, bank)
    parts = [
        f"Analyse this {bank_name} monetary policy statement dated {meeting_date.strftime('%B %d, %Y')}.",
        "",
        "=== STATEMENT ===",
        text[:4000],
        "",
    ]
    if prior_text:
        parts += ["=== PREVIOUS MEETING STATEMENT (for comparison) ===", prior_text[:1500], ""]
    else:
        parts.append("(No prior meeting in dataset — mark tone_change_vs_prior accordingly.)")
    parts += ["Return this exact JSON structure:", _ANALYSIS_SCHEMA]
    return "\n".join(parts)


# ── JSON parsing / validation ────────────────────────────────────────────────

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


_VALID_TONE   = frozenset(["extremely_dovish","dovish","slightly_dovish","neutral","slightly_hawkish","hawkish","extremely_hawkish"])
_VALID_INFL   = frozenset(["falling","below_target","stable","rising","well_above_target"])
_VALID_GROWTH = frozenset(["weak","slowing","moderate","strong"])
_VALID_LABOR  = frozenset(["loose","easing","balanced","tight"])


def _validated(raw: dict[str, Any]) -> dict[str, Any]:
    tone_label = str(raw.get("tone_label","neutral")).lower()
    if tone_label not in _VALID_TONE:   tone_label = "neutral"
    inflation  = str(raw.get("inflation_outlook","stable")).lower()
    if inflation not in _VALID_INFL:    inflation  = "stable"
    growth     = str(raw.get("growth_outlook","moderate")).lower()
    if growth not in _VALID_GROWTH:     growth     = "moderate"
    labor      = str(raw.get("labor_outlook","balanced")).lower()
    if labor not in _VALID_LABOR:       labor      = "balanced"
    return {
        "tone_score":           _safe_float(raw.get("tone_score")),
        "tone_label":           tone_label,
        "inflation_outlook":    inflation,
        "inflation_summary":    _safe_str(raw.get("inflation_summary")),
        "growth_outlook":       growth,
        "growth_summary":       _safe_str(raw.get("growth_summary")),
        "labor_outlook":        labor,
        "labor_summary":        _safe_str(raw.get("labor_summary")),
        "key_phrases":          _safe_list(raw.get("key_phrases"),  max_items=5),
        "tone_change_vs_prior": _safe_str(raw.get("tone_change_vs_prior"), 400),
        "retail_bullets":       _safe_list(raw.get("retail_bullets"), max_items=3, max_len=250),
    }


# ── OpenAI call ──────────────────────────────────────────────────────────────

async def _call_openai(prompt: str) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.openai_api_key:
        logger.error("OPENAI_API_KEY not set — skipping analysis")
        return None
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
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
                        {"role": "user",   "content": prompt},
                    ],
                    "max_tokens": 700,
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


# ── PDF download & storage ────────────────────────────────────────────────────

def pdf_path(bank: str, meeting_date: date) -> Path:
    return CB_POLICY_PDF_DIR / bank / f"{meeting_date.isoformat()}.pdf"


async def download_and_store_pdf(bank: str, meeting_date: date, pdf_url: str) -> bool:
    """Download the actual PDF from the CB website and cache to disk.

    Returns True if saved successfully (or already cached).
    """
    if not pdf_url:
        return False

    dest = pdf_path(bank, meeting_date)
    if dest.exists():
        return True

    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "application/pdf,*/*",
            },
        ) as client:
            resp = await client.get(pdf_url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "html" in content_type and len(resp.content) < 50_000:
                logger.warning("PDF URL %s returned HTML (likely a 404 page), skipping", pdf_url)
                return False
            if len(resp.content) < 1_000:
                logger.warning("PDF %s too small (%d bytes), skipping", pdf_url, len(resp.content))
                return False
    except httpx.HTTPError as exc:
        logger.warning("Failed to download PDF for %s %s: %s", bank, meeting_date, exc)
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    logger.info("Saved PDF %s/%s (%d KB)", bank, meeting_date, len(resp.content) // 1024)
    return True


def _enforce_pdf_limit(bank: str) -> int:
    """Delete PDF files beyond MAX_PDFS_PER_BANK for a bank, keeping the newest.

    Files are named YYYY-MM-DD.pdf so lexicographic sort = chronological sort.
    Returns number of files deleted.
    """
    bank_dir = CB_POLICY_PDF_DIR / bank
    if not bank_dir.exists():
        return 0
    pdfs = sorted(bank_dir.glob("*.pdf"), key=lambda p: p.stem, reverse=True)
    to_delete = pdfs[MAX_PDFS_PER_BANK:]
    for p in to_delete:
        p.unlink(missing_ok=True)
        logger.info("Deleted old PDF: %s", p)
    return len(to_delete)


# ── DB helpers ────────────────────────────────────────────────────────────────

async def upsert_statement(
    session: AsyncSession,
    record: StatementRecord,
) -> CbPolicyReport:
    """Insert or update a scraped statement using PostgreSQL native UPSERT."""
    stmt = (
        pg_insert(CbPolicyReport)
        .values(
            bank=record["bank"],
            meeting_date=record["meeting_date"],
            report_type=record.get("report_type", "statement"),
            source_url=record.get("source_url"),
            source_pdf_url=record.get("source_pdf_url") or None,
            raw_text=record.get("raw_text"),
        )
        .on_conflict_do_update(
            constraint="uq_cb_policy_reports_bank_date",
            set_={
                "source_url":     pg_insert(CbPolicyReport).excluded.source_url,
                "source_pdf_url": pg_insert(CbPolicyReport).excluded.source_pdf_url,
                "raw_text":       pg_insert(CbPolicyReport).excluded.raw_text,
            },
        )
        .returning(CbPolicyReport.id)
    )
    result = await session.execute(stmt)
    report_id = result.scalar_one()
    await session.flush()
    existing = await session.get(CbPolicyReport, report_id)
    assert existing is not None
    return existing


async def _enforce_limit(session: AsyncSession, bank: str) -> int:
    """Delete the oldest reports for a bank beyond MAX_REPORTS_PER_BANK."""
    keep_ids_q = await session.execute(
        select(CbPolicyReport.id, CbPolicyReport.meeting_date)
        .where(CbPolicyReport.bank == bank)
        .order_by(desc(CbPolicyReport.meeting_date))
        .limit(MAX_REPORTS_PER_BANK)
    )
    keep_rows = keep_ids_q.all()
    if len(keep_rows) < MAX_REPORTS_PER_BANK:
        return 0

    keep_ids = {row.id for row in keep_rows}
    to_delete_q = await session.execute(
        select(CbPolicyReport.id, CbPolicyReport.meeting_date)
        .where(CbPolicyReport.bank == bank, CbPolicyReport.id.not_in(keep_ids))
    )
    to_delete = to_delete_q.all()
    if not to_delete:
        return 0

    result = await session.execute(
        delete(CbPolicyReport).where(
            CbPolicyReport.bank == bank,
            CbPolicyReport.id.not_in(keep_ids),
        )
    )
    deleted = result.rowcount
    logger.info("Pruned %d old report(s) for %s", deleted, bank)
    return deleted


async def analyze_report(session: AsyncSession, report: CbPolicyReport) -> bool:
    """Run OpenAI analysis on one report and persist results."""
    if not report.raw_text:
        logger.warning("Skipping %s %s — no raw text", report.bank, report.meeting_date)
        return False

    prior = await session.scalar(
        select(CbPolicyReport)
        .where(
            CbPolicyReport.bank == report.bank,
            CbPolicyReport.meeting_date < report.meeting_date,
            CbPolicyReport.raw_text.is_not(None),
        )
        .order_by(asc(CbPolicyReport.meeting_date))
        .limit(1)
    )

    prompt = _build_prompt(
        bank=report.bank,
        meeting_date=report.meeting_date,
        text=report.raw_text,
        prior_text=prior.raw_text if prior else None,
    )

    raw = await _call_openai(prompt)
    if raw is None:
        return False

    v = _validated(raw)
    report.tone_score           = v["tone_score"]
    report.tone_label           = v["tone_label"]
    report.inflation_outlook    = v["inflation_outlook"]
    report.inflation_summary    = v["inflation_summary"]
    report.growth_outlook       = v["growth_outlook"]
    report.growth_summary       = v["growth_summary"]
    report.labor_outlook        = v["labor_outlook"]
    report.labor_summary        = v["labor_summary"]
    report.key_phrases          = v["key_phrases"]
    report.tone_change_vs_prior = v["tone_change_vs_prior"]
    report.retail_bullets       = v["retail_bullets"]
    report.full_analysis        = raw
    report.analyzed_at          = datetime.now(timezone.utc)

    await session.flush()
    logger.info(
        "Analyzed %s %s — tone=%.1f (%s)",
        report.bank, report.meeting_date,
        float(report.tone_score or 0), report.tone_label,
    )
    return True


async def run_full_pipeline(
    session: AsyncSession,
    records: list[StatementRecord],
    reanalyze: bool = False,
) -> dict[str, int]:
    """Upsert scraped records, analyze new ones, enforce per-bank limits, download PDFs."""
    counts = {"upserted": 0, "analyzed": 0, "skipped": 0, "pruned": 0}

    for record in records:
        report = await upsert_statement(session, record)
        counts["upserted"] += 1

        if report.analyzed_at is not None and not reanalyze:
            counts["skipped"] += 1
            continue

        success = await analyze_report(session, report)
        counts["analyzed" if success else "skipped"] += 1

    # Enforce 12-report cap per bank
    banks_seen = {r["bank"] for r in records}
    for bank in banks_seen:
        counts["pruned"] += await _enforce_limit(session, bank)

    await session.commit()

    # Download the last 2 actual PDFs per bank (after commit so DB failures don't block this)
    for bank in banks_seen:
        bank_records = sorted(
            [r for r in records if r["bank"] == bank and r.get("source_pdf_url")],
            key=lambda r: r["meeting_date"],
            reverse=True,
        )
        for rec in bank_records[:MAX_PDFS_PER_BANK]:
            await download_and_store_pdf(bank, rec["meeting_date"], rec.get("source_pdf_url", ""))
        _enforce_pdf_limit(bank)

    return counts
