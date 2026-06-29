"""OpenAI-powered analysis of central bank policy statements.

For each scraped statement, we send the text to the OpenAI Chat Completions
API and get back a structured JSON with tone score, outlook directions, key
phrases, tone shift vs prior meeting, and retail-trader bullets.

Results are persisted to cb_policy_reports (analyze once, cache forever).
Each bank is capped at 12 reports — oldest are purged automatically.
PDFs are generated on-demand and cached to data/cb_policy_pdfs/.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
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
        "tone_score":         _safe_float(raw.get("tone_score")),
        "tone_label":         tone_label,
        "inflation_outlook":  inflation,
        "inflation_summary":  _safe_str(raw.get("inflation_summary")),
        "growth_outlook":     growth,
        "growth_summary":     _safe_str(raw.get("growth_summary")),
        "labor_outlook":      labor,
        "labor_summary":      _safe_str(raw.get("labor_summary")),
        "key_phrases":        _safe_list(raw.get("key_phrases"),  max_items=5),
        "tone_change_vs_prior": _safe_str(raw.get("tone_change_vs_prior"), 400),
        "retail_bullets":     _safe_list(raw.get("retail_bullets"), max_items=3, max_len=250),
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


# ── PDF generation ───────────────────────────────────────────────────────────

def pdf_path(bank: str, meeting_date: date) -> Path:
    return CB_POLICY_PDF_DIR / bank / f"{meeting_date.isoformat()}.pdf"


def generate_report_pdf(report: CbPolicyReport) -> bytes:
    """Build a formatted PDF for one policy report using reportlab."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.utils import simpleSplit

    W, H = A4
    buf = BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)

    # Colours
    bg       = colors.HexColor("#071018")
    panel    = colors.HexColor("#12202b")
    border   = colors.HexColor("#355063")
    text_c   = colors.HexColor("#dce8f2")
    muted    = colors.HexColor("#8fa5b5")
    accent   = colors.HexColor("#ff8c42")
    hawk_c   = colors.HexColor("#ff7a4a")
    dove_c   = colors.HexColor("#50b5ff")
    pos_c    = colors.HexColor("#23c483")
    neg_c    = colors.HexColor("#ff5a7e")

    score = float(report.tone_score or 0)
    tone_color = hawk_c if score > 1 else (dove_c if score < -1 else muted)
    bank_name = _BANK_NAMES.get(report.bank, report.bank)

    def page_bg() -> None:
        c.setFillColor(bg)
        c.rect(0, 0, W, H, stroke=0, fill=1)

    def wrap_text(txt: str, x: float, y: float, max_w: float, size: int = 9,
                  col: Any = None, bold: bool = False) -> float:
        c.setFillColor(col or muted)
        font = "Helvetica-Bold" if bold else "Helvetica"
        c.setFont(font, size)
        for line in simpleSplit(txt, font, size, max_w):
            if y < 60:
                c.showPage(); page_bg(); y = H - 60
            c.drawString(x, y, line)
            y -= size + 3
        return y

    def section_bar(y: float, label: str) -> float:
        c.setFillColor(accent)
        c.rect(40, y - 2, W - 80, 1, stroke=0, fill=1)
        c.setFillColor(accent)
        c.setFont("Helvetica-Bold", 7)
        c.drawString(40, y + 4, label.upper())
        return y - 18

    # ── Page 1 ──
    page_bg()

    # Header strip
    c.setFillColor(panel)
    c.rect(0, H - 90, W, 90, stroke=0, fill=1)
    c.setStrokeColor(accent)
    c.setLineWidth(3)
    c.line(0, H - 90, W, H - 90)

    # Bank name + date
    c.setFillColor(accent)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(40, H - 26, "MACRO DASHBOARD · CB POLICY TRACKER")
    c.setFillColor(text_c)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(40, H - 52, bank_name)
    c.setFillColor(muted)
    c.setFont("Helvetica", 10)
    c.drawString(40, H - 68, report.meeting_date.strftime("%B %d, %Y") + f"  ·  {report.report_type.title()}")

    # Tone score badge
    c.setFillColor(tone_color)
    c.roundRect(W - 140, H - 76, 100, 46, 6, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 24)
    score_str = f"{score:+.1f}"
    c.drawCentredString(W - 90, H - 52, score_str)
    c.setFont("Helvetica-Bold", 8)
    label_str = (report.tone_label or "neutral").replace("_", " ").upper()
    c.drawCentredString(W - 90, H - 66, label_str)

    y = H - 110

    # Outlook grid
    outlook_data = [
        ("Inflation", report.inflation_outlook, report.inflation_summary),
        ("Growth",    report.growth_outlook,    report.growth_summary),
        ("Labor",     report.labor_outlook,      report.labor_summary),
    ]
    cell_w = (W - 80 - 20) / 3
    for idx, (topic, outlook, summary) in enumerate(outlook_data):
        ox = 40 + idx * (cell_w + 10)
        c.setFillColor(panel)
        c.setStrokeColor(border)
        c.roundRect(ox, y - 54, cell_w, 58, 4, stroke=1, fill=1)
        c.setFillColor(accent)
        c.setFont("Helvetica-Bold", 7)
        c.drawString(ox + 8, y - 12, topic.upper())
        ov = (outlook or "—").replace("_", " ").title()
        c.setFillColor(text_c)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(ox + 8, y - 28, ov)
        if summary:
            c.setFillColor(muted)
            c.setFont("Helvetica", 7)
            for line in simpleSplit(summary, "Helvetica", 7, cell_w - 16)[:3]:
                c.drawString(ox + 8, y - 40, line)
                y_offset = -10
                break

    y -= 72

    # Key phrases
    if report.key_phrases:
        y = section_bar(y, "Key Policy Language")
        phrases = report.key_phrases or []
        px = 40
        for phrase in phrases[:6]:
            pw = len(phrase) * 5.5 + 16
            if px + pw > W - 40:
                px = 40; y -= 18
            c.setFillColor(panel)
            c.setStrokeColor(border)
            c.roundRect(px, y - 12, pw, 14, 4, stroke=1, fill=1)
            c.setFillColor(muted)
            c.setFont("Helvetica", 8)
            c.drawString(px + 8, y - 4, phrase)
            px += pw + 8
        y -= 28

    # What changed
    if report.tone_change_vs_prior:
        y = section_bar(y, "Tone Shift vs Prior Meeting")
        c.setFillColor(colors.HexColor("#f3ba6315"))
        c.setStrokeColor(colors.HexColor("#f3ba6340"))
        c.roundRect(40, y - 28, W - 80, 32, 4, stroke=1, fill=1)
        c.setFillColor(colors.HexColor("#f3ba63"))
        c.rect(40, y - 28, 3, 32, stroke=0, fill=1)
        y = wrap_text(report.tone_change_vs_prior, 52, y + 2, W - 100, size=9, col=text_c)
        y -= 10

    # Retail bullets
    if report.retail_bullets:
        y = section_bar(y, "Retail FX Trader Takeaways")
        for bullet in report.retail_bullets:
            c.setFillColor(accent)
            c.circle(48, y + 2, 3, stroke=0, fill=1)
            y = wrap_text(bullet, 58, y, W - 100, size=10, col=text_c)
            y -= 4

    # Full statement text
    if report.raw_text:
        y = section_bar(y - 4, "Full Statement Text")
        y = wrap_text(report.raw_text[:6000], 40, y, W - 80, size=8, col=muted)

    # Footer
    c.setFillColor(panel)
    c.rect(0, 0, W, 30, stroke=0, fill=1)
    c.setFillColor(muted)
    c.setFont("Helvetica", 7)
    c.drawString(40, 10, f"Generated by Macro Dashboard  ·  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    if report.source_url:
        c.drawRightString(W - 40, 10, f"Source: {report.source_url[:80]}")

    c.save()
    return buf.getvalue()


def save_report_pdf(report: CbPolicyReport) -> Path:
    """Generate and save PDF to disk. Returns the file path."""
    path = pdf_path(report.bank, report.meeting_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(generate_report_pdf(report))
    logger.info("Saved PDF: %s", path)
    return path


def delete_report_pdf(bank: str, meeting_date: date) -> None:
    """Remove cached PDF file if it exists."""
    p = pdf_path(bank, meeting_date)
    if p.exists():
        p.unlink()


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
            raw_text=record.get("raw_text"),
        )
        .on_conflict_do_update(
            constraint="uq_cb_policy_reports_bank_date",
            set_={
                "source_url": pg_insert(CbPolicyReport).excluded.source_url,
                "raw_text":   pg_insert(CbPolicyReport).excluded.raw_text,
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
    """Delete the oldest reports for a bank beyond MAX_REPORTS_PER_BANK.

    Also removes their cached PDF files. Returns number of rows deleted.
    """
    # IDs of reports to keep (newest MAX_REPORTS_PER_BANK)
    keep_ids_q = await session.execute(
        select(CbPolicyReport.id, CbPolicyReport.meeting_date)
        .where(CbPolicyReport.bank == bank)
        .order_by(desc(CbPolicyReport.meeting_date))
        .limit(MAX_REPORTS_PER_BANK)
    )
    keep_rows = keep_ids_q.all()
    if len(keep_rows) < MAX_REPORTS_PER_BANK:
        return 0  # Under the limit, nothing to prune

    keep_ids = {row.id for row in keep_rows}

    # Find the ones to delete
    to_delete_q = await session.execute(
        select(CbPolicyReport.id, CbPolicyReport.meeting_date)
        .where(
            CbPolicyReport.bank == bank,
            CbPolicyReport.id.not_in(keep_ids),
        )
    )
    to_delete = to_delete_q.all()
    if not to_delete:
        return 0

    # Delete PDF files for pruned reports
    for row in to_delete:
        delete_report_pdf(bank, row.meeting_date)

    # Delete DB rows
    result = await session.execute(
        delete(CbPolicyReport).where(
            CbPolicyReport.bank == bank,
            CbPolicyReport.id.not_in(keep_ids),
        )
    )
    deleted = result.rowcount
    logger.info("Pruned %d old report(s) for %s (limit=%d)", deleted, bank, MAX_REPORTS_PER_BANK)
    return deleted


async def analyze_report(
    session: AsyncSession,
    report: CbPolicyReport,
) -> bool:
    """Run OpenAI analysis on one report and persist results + PDF."""
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
    report.tone_score         = v["tone_score"]
    report.tone_label         = v["tone_label"]
    report.inflation_outlook  = v["inflation_outlook"]
    report.inflation_summary  = v["inflation_summary"]
    report.growth_outlook     = v["growth_outlook"]
    report.growth_summary     = v["growth_summary"]
    report.labor_outlook      = v["labor_outlook"]
    report.labor_summary      = v["labor_summary"]
    report.key_phrases        = v["key_phrases"]
    report.tone_change_vs_prior = v["tone_change_vs_prior"]
    report.retail_bullets     = v["retail_bullets"]
    report.full_analysis      = raw
    report.analyzed_at        = datetime.now(timezone.utc)

    await session.flush()

    # Generate and cache PDF
    try:
        save_report_pdf(report)
    except Exception as exc:
        logger.warning("PDF generation failed for %s %s: %s", report.bank, report.meeting_date, exc)

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
    """Upsert scraped records, analyze new ones, then enforce per-bank limit."""
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
    return counts
