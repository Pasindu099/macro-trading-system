"""Fed Funds futures fetcher backed by Yahoo Finance."""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rate_fetchers.cache import load_cached_curve, upsert_ois_curve

logger = logging.getLogger(__name__)

MONTH_CODES = {
    1: "F",
    2: "G",
    3: "H",
    4: "J",
    5: "K",
    6: "M",
    7: "N",
    8: "Q",
    9: "U",
    10: "V",
    11: "X",
    12: "Z",
}


def get_fed_futures_prices(months_ahead: int = 14) -> dict[date, float]:
    """Return {contract_expiry_date: implied_rate_pct} for next N months."""
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance is not installed; Fed futures fetch skipped.")
        return {}

    today = date.today()
    prices: dict[date, float] = {}
    for offset in range(max(1, months_ahead)):
        contract_month = _add_months(today, offset)
        ticker = _ticker_for_month(contract_month)
        try:
            history = yf.Ticker(ticker).history(period="5d", interval="1d")
        except Exception as exc:  # noqa: BLE001 - yfinance raises mixed exception types.
            logger.warning("Fed futures fetch failed for %s: %s", ticker, exc)
            continue
        if history.empty or "Close" not in history:
            continue
        close = history["Close"].dropna()
        if close.empty:
            continue
        price = float(close.iloc[-1])
        prices[contract_month] = round(100.0 - price, 4)
    return prices


async def fetch_and_cache(db_session: AsyncSession) -> str:
    """Fetch latest Fed futures prices and upsert into ois_cache."""
    values = await asyncio.to_thread(get_fed_futures_prices)
    if not values:
        cached = await load_cached_curve(db_session, bank="FED", source="yfinance_ZQ")
        status = "cached" if cached else "failed"
        logger.warning("Fed futures fetch returned no rows; status=%s", status)
        return status

    today = date.today()
    curve = {
        max(1, (contract_date - today).days): rate
        for contract_date, rate in values.items()
    }
    await upsert_ois_curve(
        db_session,
        bank="FED",
        curve_date=today,
        values=curve,
        source="yfinance_ZQ",
    )
    logger.info("Cached %d Fed Funds futures points.", len(curve))
    return "ok"


def _ticker_for_month(value: date) -> str:
    return f"ZQ{MONTH_CODES[value.month]}{value.year % 100:02d}.CBT"


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)
