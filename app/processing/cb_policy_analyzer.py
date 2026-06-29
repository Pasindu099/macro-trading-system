"""OpenAI-powered analysis of central bank policy statements.

For each scraped statement, we send the text to the OpenAI Responses API and
get back a structured JSON with:
  - tone_score (-5 extremely dovish … +5 extremely hawkish)
  - inflation / growth / labor outlook + one-sentence summaries
  - key phrases that define the statement's language
  - tone change vs the prior meeting
  - 2-3 bullet points for retail FX traders

Results are persisted to cb_policy_reports so we only call the API once
per (bank, meeting_date). Re-runs skip already-analyzed rows.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select, asc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CbPolicyReport
from app.processing.cb_policy_scraper import StatementRecord
from app.settings import get_settings

logger = logging.getLogger(__name__)

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

_BANK_NAMES = {
    "FED": "Federal Reserve (FOMC)",
    "ECB": "European Central Bank",
    "BOE": "Bank of England",
    "BOJ": "Bank of Japan",
    "RBA": "Reserve Bank of Australia",
    "BOC": "Bank of Canada",
    "SNB": "Swiss National Bank",
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


def _build_prompt(
    bank: str,
    meeting_date: date,
    text: str,
    prior_text: str | None,
) -> str:
    bank_name = _BANK_NAMES.get(bank, bank)
    date_str = meeting_date.strftime("%B %d, %Y")

    parts = [
        f"Analyse this {bank_name} monetary policy statement dated {date_str}.",
        "",
        "=== STATEMENT ===",
        text[:4000],
        "",
    ]

    if prior_text:
        parts += [
            "=== PREVIOUS MEETING STATEMENT (for comparison) ===",
            prior_text[:1500],
            "",
        ]
    else:
        parts.append("(No prior meeting in dataset — mark tone_change_vs_prior accordingly.)")

    parts += [
        "Return this exact JSON structure:",
        _ANALYSIS_SCHEMA,
    ]

    return "\n".join(parts)


def _extract_json(raw: str) -> dict[str, Any] | None:
    """Try to parse JSON from the model response, handling markdown fences."""
    raw = raw.strip()
    # Strip markdown fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Find first {...} block
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return None


def _safe_float(v: Any, lo: float = -5.0, hi: float = 5.0) -> Decimal | None:
    try:
        f = float(v)
        return Decimal(str(max(lo, min(hi, round(f, 2)))))
    except (TypeError, ValueError):
        return None


def _safe_str(v: Any, max_len: int = 200) -> str | None:
    if v is None:
        return None
    return str(v).strip()[:max_len] or None


def _safe_list(v: Any, max_items: int = 5, max_len: int = 120) -> list[str] | None:
    if not isinstance(v, list):
        return None
    return [str(item).strip()[:max_len] for item in v if item][:max_items] or None


_VALID_TONE_LABELS = frozenset(
    ["extremely_dovish", "dovish", "slightly_dovish", "neutral",
     "slightly_hawkish", "hawkish", "extremely_hawkish"]
)
_VALID_INFLATION = frozenset(["falling", "below_target", "stable", "rising", "well_above_target"])
_VALID_GROWTH = frozenset(["weak", "slowing", "moderate", "strong"])
_VALID_LABOR = frozenset(["loose", "easing", "balanced", "tight"])


def _validated(raw: dict[str, Any]) -> dict[str, Any]:
    tone_label = str(raw.get("tone_label", "neutral")).lower()
    if tone_label not in _VALID_TONE_LABELS:
        tone_label = "neutral"
    inflation = str(raw.get("inflation_outlook", "stable")).lower()
    if inflation not in _VALID_INFLATION:
        inflation = "stable"
    growth = str(raw.get("growth_outlook", "moderate")).lower()
    if growth not in _VALID_GROWTH:
        growth = "moderate"
    labor = str(raw.get("labor_outlook", "balanced")).lower()
    if labor not in _VALID_LABOR:
        labor = "balanced"

    return {
        "tone_score": _safe_float(raw.get("tone_score")),
        "tone_label": tone_label,
        "inflation_outlook": inflation,
        "inflation_summary": _safe_str(raw.get("inflation_summary")),
        "growth_outlook": growth,
        "growth_summary": _safe_str(raw.get("growth_summary")),
        "labor_outlook": labor,
        "labor_summary": _safe_str(raw.get("labor_summary")),
        "key_phrases": _safe_list(raw.get("key_phrases"), max_items=5),
        "tone_change_vs_prior": _safe_str(raw.get("tone_change_vs_prior"), 400),
        "retail_bullets": _safe_list(raw.get("retail_bullets"), max_items=3, max_len=250),
    }


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
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 700,
                    "temperature": 0.2,
                },
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("OpenAI request failed: %s", exc)
        return None

    data = resp.json()
    raw_text = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    return _extract_json(raw_text)


async def upsert_statement(
    session: AsyncSession,
    record: StatementRecord,
) -> CbPolicyReport:
    """Insert or update a scraped statement (without analysis)."""
    existing = await session.scalar(
        select(CbPolicyReport).where(
            CbPolicyReport.bank == record["bank"],
            CbPolicyReport.meeting_date == record["meeting_date"],
        )
    )
    if existing is None:
        existing = CbPolicyReport(
            bank=record["bank"],
            meeting_date=record["meeting_date"],
            report_type=record.get("report_type", "statement"),
            source_url=record.get("source_url"),
            raw_text=record.get("raw_text"),
        )
        session.add(existing)
    else:
        # Update text in case scrape yielded more content
        if record.get("raw_text") and len(record["raw_text"]) > len(existing.raw_text or ""):
            existing.raw_text = record["raw_text"]
        if record.get("source_url"):
            existing.source_url = record["source_url"]

    await session.flush()
    return existing


async def analyze_report(
    session: AsyncSession,
    report: CbPolicyReport,
) -> bool:
    """Run OpenAI analysis on one report and persist results.

    Returns True if analysis was performed (or updated), False if skipped.
    """
    if not report.raw_text:
        logger.warning("Skipping %s %s — no raw text", report.bank, report.meeting_date)
        return False

    # Fetch the chronologically previous report for this bank
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
    prior_text = prior.raw_text if prior else None

    prompt = _build_prompt(
        bank=report.bank,
        meeting_date=report.meeting_date,
        text=report.raw_text,
        prior_text=prior_text,
    )

    raw = await _call_openai(prompt)
    if raw is None:
        return False

    validated = _validated(raw)
    report.tone_score = validated["tone_score"]
    report.tone_label = validated["tone_label"]
    report.inflation_outlook = validated["inflation_outlook"]
    report.inflation_summary = validated["inflation_summary"]
    report.growth_outlook = validated["growth_outlook"]
    report.growth_summary = validated["growth_summary"]
    report.labor_outlook = validated["labor_outlook"]
    report.labor_summary = validated["labor_summary"]
    report.key_phrases = validated["key_phrases"]
    report.tone_change_vs_prior = validated["tone_change_vs_prior"]
    report.retail_bullets = validated["retail_bullets"]
    report.full_analysis = raw
    report.analyzed_at = datetime.now(timezone.utc)

    await session.flush()
    logger.info("Analyzed %s %s — tone=%.1f (%s)", report.bank, report.meeting_date,
                float(report.tone_score or 0), report.tone_label)
    return True


async def run_full_pipeline(
    session: AsyncSession,
    records: list[StatementRecord],
    reanalyze: bool = False,
) -> dict[str, int]:
    """Upsert scraped records then analyze any not-yet-analyzed.

    Returns counts: {upserted, analyzed, skipped}.
    """
    counts = {"upserted": 0, "analyzed": 0, "skipped": 0}

    for record in records:
        report = await upsert_statement(session, record)
        counts["upserted"] += 1

        if report.analyzed_at is not None and not reanalyze:
            counts["skipped"] += 1
            continue

        success = await analyze_report(session, report)
        if success:
            counts["analyzed"] += 1
        else:
            counts["skipped"] += 1

    await session.commit()
    return counts
