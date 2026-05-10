"""Reserve Bank of Australia cash-rate expectations fetcher."""

from __future__ import annotations

import csv
import logging
import re
from datetime import date, datetime
from io import StringIO

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rate_fetchers.cache import load_cached_curve, upsert_ois_curve

logger = logging.getLogger(__name__)

RBA_F17_URL = "https://www.rba.gov.au/statistics/tables/csv/f17-forward-rates.csv"
TRADINGVIEW_IB_CONTRACTS_URL = (
    "https://r.jina.ai/http://r.jina.ai/http://"
    "https://www.tradingview.com/symbols/ASX24-IB1!/contracts/"
)
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
    ),
    "Accept": "text/csv,*/*",
}
TENOR_ALIASES = {
    "1 month": 30,
    "3 month": 90,
    "6 month": 180,
    "1 year": 365,
    "2 year": 730,
}


async def fetch_rba_ois_curve(
    db_session: AsyncSession,
    as_of_date: date | None = None,
) -> dict[int, float]:
    """Parse RBA F17 money-market table and return OIS-like tenors."""
    target_date = as_of_date or date.today()
    curve = await fetch_asx_cash_rate_futures_curve(db_session, as_of_date=as_of_date)
    if curve:
        return curve

    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            response = await client.get(RBA_F17_URL, headers=REQUEST_HEADERS)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("RBA F17 fetch failed: %s", exc)
        return await load_cached_curve(db_session, bank="RBA", source="rba_f17")

    curve = _parse_rba_csv(response.text, target_date)
    if not curve:
        logger.warning("RBA F17 response contained no usable OIS points.")
        return await load_cached_curve(db_session, bank="RBA", source="rba_f17")

    await upsert_ois_curve(
        db_session,
        bank="RBA",
        curve_date=target_date,
        values=curve,
        source="rba_f17",
    )
    return curve


async def fetch_asx_cash_rate_futures_curve(
    db_session: AsyncSession,
    as_of_date: date | None = None,
) -> dict[int, float]:
    """Fetch ASX 30-day interbank cash-rate futures from TradingView's contract table."""
    target_date = as_of_date or date.today()
    try:
        async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
            response = await client.get(TRADINGVIEW_IB_CONTRACTS_URL, headers=REQUEST_HEADERS)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("RBA ASX cash-rate futures fetch failed: %s", exc)
        return await load_cached_curve(db_session, bank="RBA", source="tradingview_IB")

    curve = _parse_tradingview_cash_rate_futures(response.text, target_date)
    if not curve:
        logger.warning("RBA ASX cash-rate futures response contained no usable contracts.")
        return await load_cached_curve(db_session, bank="RBA", source="tradingview_IB")

    await upsert_ois_curve(
        db_session,
        bank="RBA",
        curve_date=target_date,
        values=curve,
        source="tradingview_IB",
    )
    return curve


def _parse_rba_csv(raw_csv: str, as_of_date: date) -> dict[int, float]:
    lines = [line for line in raw_csv.splitlines() if line.strip()]
    header_index = _find_header_index(lines)
    if header_index is None:
        return {}

    rows = list(csv.DictReader(StringIO("\n".join(lines[header_index:]))))
    dated_rows = [
        (row_date, row)
        for row in rows
        if (row_date := _row_date(row)) is not None and row_date <= as_of_date
    ]
    if not dated_rows:
        return {}

    _, latest = max(dated_rows, key=lambda item: item[0])
    curve: dict[int, float] = {}
    for key, value in latest.items():
        tenor = _tenor_from_header(key)
        parsed = _float_value(value)
        if tenor is not None and parsed is not None:
            curve[tenor] = parsed
    return curve


def _find_header_index(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if "date" in line.lower():
            return index
    return None


def _row_date(row: dict[str, str]) -> date | None:
    for key, value in row.items():
        if "date" not in key.lower() or not value:
            continue
        for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(value[:11], fmt).date()
            except ValueError:
                continue
    return None


def _tenor_from_header(header: str | None) -> int | None:
    normalized = str(header or "").lower()
    if "ois" not in normalized and "overnight indexed" not in normalized:
        return None
    for label, tenor in TENOR_ALIASES.items():
        if label in normalized:
            return tenor
    match = re.search(r"(\d+)\s*m", normalized)
    if match:
        return int(match.group(1)) * 30
    match = re.search(r"(\d+)\s*y", normalized)
    if match:
        return int(match.group(1)) * 365
    return None


def _float_value(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _parse_tradingview_cash_rate_futures(markdown: str, curve_date: date) -> dict[int, float]:
    curve: dict[int, float] = {}
    pattern = re.compile(
        r"\|\s+I\[IB[A-Z]\d{4}\].*?\|\s+(\d{4}-\d{2}-\d{2})\s+\|\s+([0-9]+(?:\.[0-9]+)?)\s+\|"
    )
    for expiry_raw, price_raw in pattern.findall(markdown):
        try:
            expiry = date.fromisoformat(expiry_raw)
            price = float(price_raw)
        except ValueError:
            continue
        tenor_days = (expiry - curve_date).days
        if tenor_days <= 0:
            continue
        implied_rate = 100.0 - price
        curve[tenor_days] = round(implied_rate, 6)
    return dict(sorted(curve.items()))
