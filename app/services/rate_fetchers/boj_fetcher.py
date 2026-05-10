"""Bank of Japan TONA futures proxy fetcher."""

from __future__ import annotations

import calendar
import logging
import re
from datetime import date, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rate_fetchers.cache import load_cached_curve, upsert_ois_curve

logger = logging.getLogger(__name__)

TFX_HOME_URL = "https://r.jina.ai/http://r.jina.ai/http://https://www.tfx.co.jp/en/"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
    ),
    "Accept": "text/markdown,text/plain,*/*",
}


async def fetch_boj_tona_curve(
    db_session: AsyncSession,
    as_of_date: date | None = None,
) -> dict[int, float]:
    """Fetch TFX three-month TONA futures as a BoJ expectations proxy."""
    target_date = as_of_date or date.today()
    try:
        async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
            response = await client.get(TFX_HOME_URL, headers=REQUEST_HEADERS)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("BoJ TONA futures fetch failed: %s", exc)
        return await load_cached_curve(db_session, bank="BOJ", source="tfx_TONA_proxy")

    curve = _parse_tfx_tona_markdown(response.text, target_date)
    if not curve:
        logger.warning("BoJ TONA futures response contained no usable contracts.")
        return await load_cached_curve(db_session, bank="BOJ", source="tfx_TONA_proxy")

    await upsert_ois_curve(
        db_session,
        bank="BOJ",
        curve_date=target_date,
        values=curve,
        source="tfx_TONA_proxy",
    )
    return curve


def _parse_tfx_tona_markdown(markdown: str, curve_date: date) -> dict[int, float]:
    curve: dict[int, float] = {}
    table_pattern = re.compile(
        r"\|\s+(\d{2})\.(\d{2})\s+\|\s+([^|]+)\|\s+[^|]*\|\s+([^|]+)\|\s+"
        r"[^|]*\|\s+([^|]+)\|",
    )
    for year_raw, month_raw, bid_raw, ask_raw, last_raw in table_pattern.findall(markdown):
        price = _best_price(bid_raw, ask_raw, last_raw)
        if price is None:
            continue
        _add_contract_point(curve, curve_date, year_raw, month_raw, price)

    settle_pattern = re.compile(
        r"(?:^|\n)(\d{2})\.(\d{2})\s*\n\s*\n([0-9]{2}\.[0-9]{3})"
        r"\s*\n\s*\n[\d,]+\s*\n\s*\n[\d,]+",
    )
    for year_raw, month_raw, settle_raw in settle_pattern.findall(markdown):
        try:
            price = float(settle_raw)
        except ValueError:
            continue
        _add_contract_point(curve, curve_date, year_raw, month_raw, price)
    return dict(sorted(curve.items()))


def _best_price(bid_raw: str, ask_raw: str, last_raw: str) -> float | None:
    bid = _float_or_none(bid_raw)
    ask = _float_or_none(ask_raw)
    last = _float_or_none(last_raw)
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    return last if last is not None else bid if bid is not None else ask


def _float_or_none(value: str) -> float | None:
    cleaned = value.strip()
    if not cleaned or "-" in cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _add_contract_point(
    curve: dict[int, float],
    curve_date: date,
    year_raw: str,
    month_raw: str,
    price: float,
) -> None:
    expiry = _third_wednesday(_add_months(date(2000 + int(year_raw), int(month_raw), 1), 3))
    tenor_days = (expiry - curve_date).days
    if tenor_days <= 0:
        return
    curve[tenor_days] = round(100.0 - price, 6)


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _third_wednesday(value: date) -> date:
    current = value.replace(day=1)
    seen = 0
    while current.month == value.month:
        if current.weekday() == 2:
            seen += 1
            if seen == 3:
                return current
        current += timedelta(days=1)
    raise ValueError(f"No third Wednesday found for {value:%Y-%m}")
