"""Scraped rate probability records for the existing JSON API."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

BANK_ORDER = ("FED", "ECB", "BOE", "BOJ", "RBA", "BOC", "RBNZ", "SNB")
BANK_FULL_NAMES = {"FED": "Federal Reserve", "ECB": "European Central Bank",
                   "BOE": "Bank of England", "BOJ": "Bank of Japan",
                   "RBA": "Reserve Bank of Australia", "BOC": "Bank of Canada",
                   "RBNZ": "Reserve Bank of New Zealand", "SNB": "Swiss National Bank"}
RATE_LABELS = {"FED": "Fed Funds Rate", "ECB": "Deposit Facility Rate", "BOE": "Bank Rate",
               "BOJ": "Policy Rate", "RBA": "Cash Rate", "BOC": "Overnight Rate",
               "RBNZ": "OCR", "SNB": "SNB Policy Rate"}
# Third-party display only: hide a bank's scraped numbers once they are this old (Step 7 decision).
MAX_SCRAPED_AGE = timedelta(days=3)


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def get_scraped_rate_probability_data(session: AsyncSession, *, now: datetime | None = None) -> dict[str, Any]:
    """Load both scraped tables; banks whose scrape is older than 3 days are hidden."""
    now = now or datetime.now(UTC)
    summary_rows = (await session.execute(text(
        "SELECT bank, current_rate, next_meeting_date, next_cut_prob, next_hold_prob, next_hike_prob, updated_at "
        "FROM rp_scraped_summary ORDER BY bank"
    ))).mappings().all()
    meeting_rows = (await session.execute(text(
        "SELECT bank, meeting_date, implied_rate, cut_prob, hold_prob, hike_prob, delta_bps, cumulative_moves "
        "FROM rp_scraped_meetings WHERE meeting_date >= :today ORDER BY bank, meeting_date"
    ), {"today": date.today()})).mappings().all()
    summary = {row["bank"]: row for row in summary_rows}
    meetings: dict[str, list[dict[str, Any]]] = {}
    for row in meeting_rows:
        meetings.setdefault(row["bank"], []).append({
            "date": row["meeting_date"].isoformat() if row["meeting_date"] else None,
            "implied_rate": _float(row["implied_rate"]), "cut_prob": _float(row["cut_prob"]),
            "hold_prob": _float(row["hold_prob"]), "hike_prob": _float(row["hike_prob"]),
            "delta_bps": _float(row["delta_bps"]), "cumulative_moves": _float(row["cumulative_moves"]),
        })
    banks = {}
    for bank in BANK_ORDER:
        row = summary.get(bank, {})
        updated_at = row.get("updated_at")
        if updated_at is None or now - updated_at >= MAX_SCRAPED_AGE:
            banks[bank] = {
                "full_name": BANK_FULL_NAMES.get(bank, bank),
                "rate_label": RATE_LABELS.get(bank, "Policy Rate"),
                "available": False,
                "reason": "No rateprobability.com data newer than 3 days",
                "meetings": [],
            }
            continue
        cut, hold, hike = (_float(row.get("next_cut_prob")), _float(row.get("next_hold_prob")),
                           _float(row.get("next_hike_prob")))
        outcomes = [("CUT", cut), ("HOLD", hold), ("HIKE", hike)]
        outcomes = [(name, value) for name, value in outcomes if value is not None]
        dominant, probability = max(outcomes, key=lambda item: item[1]) if outcomes else ("—", None)
        banks[bank] = {
            "full_name": BANK_FULL_NAMES.get(bank, bank),
            "rate_label": RATE_LABELS.get(bank, "Policy Rate"),
            "current_rate": _float(row.get("current_rate")),
            "next_meeting": row["next_meeting_date"].isoformat() if row.get("next_meeting_date") else None,
            "next_cut_prob": cut, "next_hold_prob": hold, "next_hike_prob": hike,
            "dominant": dominant, "dominant_prob": probability,
            "meetings": meetings.get(bank, []), "available": True,
        }
    updated = [row["updated_at"] for row in summary_rows if row["updated_at"]]
    return {"updated_at": max(updated).strftime("%Y-%m-%d %H:%M UTC") if updated else "Never",
            "max_age_days": MAX_SCRAPED_AGE.days, "banks": banks}
