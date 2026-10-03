"""Rate probability market-data fetcher registry."""

from __future__ import annotations

import importlib
import logging
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rate_fetchers.cache import count_banks_fetched_on_date
from app.services.rate_fetchers.generic_fetcher import POLICY_RATE_SERIES

logger = logging.getLogger(__name__)

FETCHER_MAP = {
    "FED": ("fed_fetcher", "fetch_and_cache"),
    "ECB": ("ecb_fetcher", "fetch_estr_ois_curve"),
    "BOE": ("boe_fetcher", "fetch_sonia_ois_curve"),
    "RBA": ("rba_fetcher", "fetch_rba_ois_curve"),
    "BOC": ("boc_fetcher", "fetch_boc_corra_curve"),
    "BOJ": ("boj_fetcher", "fetch_boj_tona_curve"),
    "SNB": ("snb_fetcher", "fetch_snb_saron_curve"),
    "RBNZ": ("rbnz_fetcher", "fetch_rbnz_wholesale_curve"),
}

STARTUP_REQUIRED_BANKS = ("FED", "ECB", "BOE", "BOC", "BOJ", "RBA", "RBNZ", "SNB")
# A fetcher that falls back to a cached curve older than this reports "stale", not "ok".
STALE_AFTER = timedelta(days=3)


def apply_freshness(statuses: dict[str, str], latest: dict[str, date | None], today: date) -> dict[str, str]:
    """Downgrade ok/cached to 'stale' when the newest stored curve is older than STALE_AFTER."""
    out = dict(statuses)
    for bank, status in statuses.items():
        if status in {"ok", "cached"}:
            newest = latest.get(bank)
            if newest is None or today - newest > STALE_AFTER:
                out[bank] = "stale"
    return out


async def latest_curve_dates(db_session: AsyncSession) -> dict[str, date | None]:
    rows = await db_session.execute(text("SELECT bank, max(curve_date) AS d FROM ois_cache GROUP BY bank"))
    return {row.bank: row.d for row in rows}


async def fetch_all(db_session: AsyncSession) -> dict[str, str]:
    """Run all fetchers. Returns {bank: 'ok'|'cached'|'stale'|'failed'}."""
    statuses: dict[str, str] = {}
    for bank, (module_name, function_name) in FETCHER_MAP.items():
        try:
            module = importlib.import_module(f"app.services.rate_fetchers.{module_name}")
            fetcher = getattr(module, function_name)
            if module_name == "generic_fetcher":
                await fetcher(db_session, POLICY_RATE_SERIES[bank], bank)
                statuses[bank] = "ok"
                continue
            result = await fetcher(db_session)
            if isinstance(result, str):
                statuses[bank] = result
            else:
                statuses[bank] = "ok" if result else "cached"
        except Exception as exc:  # noqa: BLE001 - scheduler must continue across providers.
            logger.warning("%s rate fetch failed: %s", bank, exc)
            statuses[bank] = "failed"
    return apply_freshness(statuses, await latest_curve_dates(db_session), date.today())


async def should_fetch_on_startup(db_session: AsyncSession) -> bool:
    """Return true when the currently supported market-data banks were not fetched today."""
    fetched_banks = await count_banks_fetched_on_date(
        db_session,
        date.today(),
        STARTUP_REQUIRED_BANKS,
    )
    return fetched_banks < len(STARTUP_REQUIRED_BANKS)
