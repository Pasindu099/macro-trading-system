"""Rate probability scraped data — /api/rate-probability and /rate-probability page."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

router = APIRouter(tags=["rate-probability-scraped"])
templates = Jinja2Templates(directory=str(Path("app/web/templates")))
SessionDep = Depends(get_session)

BANK_ORDER = ("FED", "ECB", "BOE", "BOJ", "RBA", "BOC", "RBNZ", "SNB")

BANK_FULL_NAMES: dict[str, str] = {
    "FED":  "Federal Reserve",
    "ECB":  "European Central Bank",
    "BOE":  "Bank of England",
    "BOJ":  "Bank of Japan",
    "RBA":  "Reserve Bank of Australia",
    "BOC":  "Bank of Canada",
    "RBNZ": "Reserve Bank of New Zealand",
    "SNB":  "Swiss National Bank",
}

RATE_LABELS: dict[str, str] = {
    "FED":  "Fed Funds Rate",
    "ECB":  "Deposit Facility Rate",
    "BOE":  "Bank Rate",
    "BOJ":  "Policy Rate",
    "RBA":  "Cash Rate",
    "BOC":  "Overnight Rate",
    "RBNZ": "OCR",
    "SNB":  "SNB Policy Rate",
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _fmt_date(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y")
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    return str(value)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _load_summary(session: AsyncSession) -> dict[str, dict[str, Any]]:
    result = await session.execute(
        text("SELECT bank, current_rate, next_meeting_date, next_cut_prob, next_hold_prob, next_hike_prob, updated_at FROM rp_scraped_summary ORDER BY bank")
    )
    rows = result.mappings().all()
    return {
        row["bank"]: {
            "current_rate":      _safe_float(row["current_rate"]),
            "next_meeting_date": row["next_meeting_date"],
            "next_cut_prob":     _safe_float(row["next_cut_prob"]),
            "next_hold_prob":    _safe_float(row["next_hold_prob"]),
            "next_hike_prob":    _safe_float(row["next_hike_prob"]),
            "updated_at":        row["updated_at"],
        }
        for row in rows
    }


async def _load_meetings(session: AsyncSession, bank: str | None = None) -> dict[str, list[dict[str, Any]]]:
    if bank:
        result = await session.execute(
            text(
                "SELECT bank, meeting_date, implied_rate, cut_prob, hold_prob, hike_prob, delta_bps, cumulative_moves "
                "FROM rp_scraped_meetings WHERE bank = :bank AND meeting_date >= :today "
                "ORDER BY bank, meeting_date"
            ),
            {"bank": bank, "today": date.today()},
        )
    else:
        result = await session.execute(
            text(
                "SELECT bank, meeting_date, implied_rate, cut_prob, hold_prob, hike_prob, delta_bps, cumulative_moves "
                "FROM rp_scraped_meetings WHERE meeting_date >= :today "
                "ORDER BY bank, meeting_date"
            ),
            {"today": date.today()},
        )

    by_bank: dict[str, list[dict[str, Any]]] = {}
    for row in result.mappings().all():
        b = row["bank"]
        if b not in by_bank:
            by_bank[b] = []
        by_bank[b].append({
            "date":             row["meeting_date"].isoformat() if row["meeting_date"] else None,
            "implied_rate":     _safe_float(row["implied_rate"]),
            "cut_prob":         _safe_float(row["cut_prob"]),
            "hold_prob":        _safe_float(row["hold_prob"]),
            "hike_prob":        _safe_float(row["hike_prob"]),
            "delta_bps":        _safe_float(row["delta_bps"]),
            "cumulative_moves": _safe_float(row["cumulative_moves"]),
        })
    return by_bank


def _dominant(cut: float | None, hold: float | None, hike: float | None) -> tuple[str, float | None]:
    opts = [("CUT", cut), ("HOLD", hold), ("HIKE", hike)]
    opts = [(k, v) for k, v in opts if v is not None]
    if not opts:
        return "—", None
    label, prob = max(opts, key=lambda x: x[1])
    return label, prob


def _global_updated_at(summary: dict[str, dict[str, Any]]) -> str:
    dates = [v["updated_at"] for v in summary.values() if v["updated_at"]]
    if not dates:
        return "Never"
    return max(dates).strftime("%Y-%m-%d %H:%M UTC")


# ── API endpoint ────────────────────────────────────────────────────────────

@router.get("/api/rate-probability")
async def get_rate_probability(session: AsyncSession = SessionDep) -> JSONResponse:
    """Return scraped rate probability data for all banks."""
    try:
        summary = await _load_summary(session)
        meetings = await _load_meetings(session)
    except Exception:
        return JSONResponse({"error": "Data not available — run scraper first"}, status_code=503)

    banks_payload: dict[str, Any] = {}
    for bank in BANK_ORDER:
        s = summary.get(bank, {})
        cut  = s.get("next_cut_prob")
        hold = s.get("next_hold_prob")
        hike = s.get("next_hike_prob")
        dominant, dom_prob = _dominant(cut, hold, hike)
        banks_payload[bank] = {
            "full_name":     BANK_FULL_NAMES.get(bank, bank),
            "rate_label":    RATE_LABELS.get(bank, "Policy Rate"),
            "current_rate":  s.get("current_rate"),
            "next_meeting":  s["next_meeting_date"].isoformat() if s.get("next_meeting_date") else None,
            "next_cut_prob": cut,
            "next_hold_prob": hold,
            "next_hike_prob": hike,
            "dominant":      dominant,
            "dominant_prob": dom_prob,
            "meetings":      meetings.get(bank, []),
        }

    updated_at = _global_updated_at(summary)
    return JSONResponse({"updated_at": updated_at, "banks": banks_payload})


# ── Page ─────────────────────────────────────────────────────────────────────

@router.get("/rate-probability", response_class=HTMLResponse)
async def rate_probability_page(
    request: Request,
    session: AsyncSession = SessionDep,
    background_tasks: BackgroundTasks = BackgroundTasks(),
) -> HTMLResponse:
    """Server-rendered rate probability dashboard from rateprobability.com data."""
    no_data = False
    try:
        summary = await _load_summary(session)
        meetings_by_bank = await _load_meetings(session)
        no_data = not summary
    except Exception:
        summary = {}
        meetings_by_bank = {}
        no_data = True

    # Build per-bank context blocks
    bank_cards = []
    for bank in BANK_ORDER:
        s = summary.get(bank, {})
        cut  = s.get("next_cut_prob")
        hold = s.get("next_hold_prob")
        hike = s.get("next_hike_prob")
        dominant, dom_prob = _dominant(cut, hold, hike)

        card_class = "rps-card"
        if dom_prob is not None and dom_prob > 50:
            if dominant == "CUT":
                card_class += " rps-card--cut"
            elif dominant == "HIKE":
                card_class += " rps-card--hike"

        next_dt = s.get("next_meeting_date")
        bank_cards.append({
            "bank":          bank,
            "full_name":     BANK_FULL_NAMES.get(bank, bank),
            "rate_label":    RATE_LABELS.get(bank, "Policy Rate"),
            "current_rate":  s.get("current_rate"),
            "next_meeting":  _fmt_date(next_dt),
            "cut_prob":      cut,
            "hold_prob":     hold,
            "hike_prob":     hike,
            "dominant":      dominant,
            "dominant_prob": dom_prob,
            "card_class":    card_class,
            "has_data":      bool(s),
        })

    # Per-bank meeting tables
    bank_tables: dict[str, list[dict[str, Any]]] = {}
    for bank in BANK_ORDER:
        rows = []
        for m in meetings_by_bank.get(bank, []):
            cut  = m.get("cut_prob")
            hold = m.get("hold_prob")
            hike = m.get("hike_prob")
            dominant, dom_prob = _dominant(cut, hold, hike)
            rows.append({
                **m,
                "date_fmt":       _fmt_date(date.fromisoformat(m["date"])) if m.get("date") else "—",
                "dominant":       dominant,
                "dominant_prob":  dom_prob,
                "dominant_class": "rps-cut" if dominant == "CUT" else "rps-hike" if dominant == "HIKE" else "rps-hold",
            })
        bank_tables[bank] = rows

    updated_at = _global_updated_at(summary)

    return templates.TemplateResponse(
        request,
        "rate_probability_scraped.html",
        {
            "page_title":     "Rate Probabilities | Macro Dashboard",
            "bank_cards":     bank_cards,
            "bank_tables":    bank_tables,
            "bank_order":     BANK_ORDER,
            "bank_names":     BANK_FULL_NAMES,
            "updated_at":     updated_at,
            "no_data":        no_data,
            "source_url":     "https://rateprobability.com",
        },
    )


# ── Admin: trigger scrape on demand ─────────────────────────────────────────

@router.post("/api/rate-probability/scrape")
async def trigger_scrape(background_tasks: BackgroundTasks) -> dict[str, str]:
    """Trigger a one-off scrape in the background. Returns immediately."""
    from scraper.rate_probability_scraper import run_scraper_async

    async def _run() -> None:
        statuses = await run_scraper_async()
        import logging
        logging.getLogger(__name__).info("On-demand scrape: %s", statuses)

    background_tasks.add_task(_run)
    return {"status": "scrape started"}
