"""Swiss National Bank SARON futures proxy fetcher."""

from __future__ import annotations

import logging
import re
from datetime import date

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rate_fetchers.cache import load_cached_curve, upsert_ois_curve

logger = logging.getLogger(__name__)

TRADINGVIEW_SARON_CONTRACTS_URL = (
    "https://r.jina.ai/http://r.jina.ai/http://"
    "https://www.tradingview.com/symbols/ICEEUR-SA31!/contracts/"
)
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
    ),
    "Accept": "text/markdown,text/plain,*/*",
}


async def fetch_snb_saron_curve(
    db_session: AsyncSession,
    as_of_date: date | None = None,
) -> dict[int, float]:
    """Fetch three-month SARON futures as an SNB expectations proxy."""
    target_date = as_of_date or date.today()
    try:
        async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
            response = await client.get(TRADINGVIEW_SARON_CONTRACTS_URL, headers=REQUEST_HEADERS)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("SNB SARON futures fetch failed: %s", exc)
        return await load_cached_curve(db_session, bank="SNB", source="tradingview_SARON_proxy")

    curve = _parse_tradingview_rate_futures(response.text, target_date)
    if not curve:
        logger.warning("SNB SARON futures response contained no usable contracts.")
        return await load_cached_curve(db_session, bank="SNB", source="tradingview_SARON_proxy")

    await upsert_ois_curve(
        db_session,
        bank="SNB",
        curve_date=target_date,
        values=curve,
        source="tradingview_SARON_proxy",
    )
    return curve


def _parse_tradingview_rate_futures(markdown: str, curve_date: date) -> dict[int, float]:
    curve: dict[int, float] = {}
    pattern = re.compile(
        r"\|\s+.*?\[(?:SA3)[A-Z]\d{4}\].*?\|\s+"
        r"(\d{4}-\d{2}-\d{2})\s+\|\s+([0-9]+(?:\.[0-9]+)?)\s+\|"
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
        curve[tenor_days] = round(100.0 - price, 6)
    return dict(sorted(curve.items()))
