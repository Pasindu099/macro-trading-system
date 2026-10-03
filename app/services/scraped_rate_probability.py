"""Scraped rate probability records for the existing JSON API."""

from __future__ import annotations

from datetime import date
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


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def get_scraped_rate_probability_data(session: AsyncSession) -> dict[str, Any]:
    """Load both scraped tables and preserve the JSON response shape."""
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
            "meetings": meetings.get(bank, []),
        }
    updated = [row["updated_at"] for row in summary_rows if row["updated_at"]]
    return {"updated_at": max(updated).strftime("%Y-%m-%d %H:%M UTC") if updated else "Never",
            "banks": banks}
