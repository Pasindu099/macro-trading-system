"""Rate probability market-data fetcher registry."""

from __future__ import annotations

import importlib
import logging
from datetime import date

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


async def fetch_all(db_session: AsyncSession) -> dict[str, str]:
    """Run all fetchers. Returns {bank: 'ok'|'cached'|'failed'}."""
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
    return statuses


async def should_fetch_on_startup(db_session: AsyncSession) -> bool:
    """Return true when the currently supported market-data banks were not fetched today."""
    fetched_banks = await count_banks_fetched_on_date(
        db_session,
        date.today(),
        STARTUP_REQUIRED_BANKS,
    )
    return fetched_banks < len(STARTUP_REQUIRED_BANKS)
