"""Reserve Bank of New Zealand wholesale-rate proxy fetcher."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rate_fetchers.cache import load_cached_curve, upsert_ois_curve

logger = logging.getLogger(__name__)

RBNZ_WHOLESALE_RATES_URL = (
    "https://r.jina.ai/http://r.jina.ai/http://"
    "https://www.rbnz.govt.nz/statistics/series/exchange-and-interest-rates/"
    "wholesale-interest-rates"
)
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
    ),
    "Accept": "text/markdown,text/plain,*/*",
}


async def fetch_rbnz_wholesale_curve(
    db_session: AsyncSession,
    as_of_date: date | None = None,
) -> dict[int, float]:
    """Fetch RBNZ wholesale bill/swap rates as an OCR expectations proxy."""
    target_date = as_of_date or date.today()
    try:
        async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
            response = await client.get(RBNZ_WHOLESALE_RATES_URL, headers=REQUEST_HEADERS)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("RBNZ wholesale-rates fetch failed: %s", exc)
        return await load_cached_curve(db_session, bank="RBNZ", source="rbnz_wholesale_proxy")

    curve_date, curve = _parse_rbnz_wholesale_markdown(response.text, target_date)
    if curve_date is None or not curve:
        logger.warning("RBNZ wholesale-rates response contained no usable points.")
        return await load_cached_curve(db_session, bank="RBNZ", source="rbnz_wholesale_proxy")

    await upsert_ois_curve(
        db_session,
        bank="RBNZ",
        curve_date=curve_date,
        values=curve,
        source="rbnz_wholesale_proxy",
    )
    return curve


def _parse_rbnz_wholesale_markdown(
    markdown: str,
    as_of_date: date,
) -> tuple[date | None, dict[int, float]]:
    rows: list[tuple[date, dict[int, float]]] = []
    for line in markdown.splitlines():
        parsed = _parse_data_line(line)
        if parsed is None:
            continue
        row_date, curve = parsed
        if row_date <= as_of_date:
            rows.append((row_date, curve))
    if not rows:
        return None, {}
    return max(rows, key=lambda item: item[0])


def _parse_data_line(line: str) -> tuple[date, dict[int, float]] | None:
    line = line.strip().strip("|").strip()
    if "|" in line:
        parts = [part.strip() for part in line.split("|")]
        if len(parts) >= 12:
            try:
                row_date = datetime.strptime(parts[0], "%d %b %Y").date()
            except ValueError:
                return None
            values = [_float_or_none(part) for part in parts[1:12]]
            curve = {
                30: values[4],
                60: values[5],
                90: values[6],
                365: values[7],
                730: values[8],
            }
            cleaned = {tenor: value for tenor, value in curve.items() if value is not None}
            return (row_date, cleaned) if cleaned else None

    match = re.match(
        r"^(\d{2}\s+[A-Z][a-z]{2}\s+\d{4})\s+"
        r"([0-9.\-]+)\s+([0-9.\-]+)\s+([0-9.\-]+)\s+([0-9.\-]+)\s+"
        r"([0-9.\-]+)\s+([0-9.\-]+)\s+([0-9.\-]+)\s+"
        r"([0-9.\-]+)\s+([0-9.\-]+)",
        line.strip(),
    )
    if not match:
        return None
    try:
        row_date = datetime.strptime(match.group(1), "%d %b %Y").date()
    except ValueError:
        return None

    values = [_float_or_none(group) for group in match.groups()[1:]]
    if len(values) < 9:
        return None

    # Columns after date: OCR, deposit, reverse repo, overnight cash, 30d, 60d,
    # 90d bank bills, then 1y and 2y swap/government-rate area in the table.
    curve = {
        30: values[4],
        60: values[5],
        90: values[6],
        365: values[7],
        730: values[8],
    }
    cleaned = {tenor: value for tenor, value in curve.items() if value is not None}
    return (row_date, cleaned) if cleaned else None


def _float_or_none(value: str | None) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except ValueError:
        return None
