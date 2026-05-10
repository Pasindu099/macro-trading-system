"""Fallback fetchers for banks without free forward-curve APIs."""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

POLICY_RATE_SERIES = {
    "BOC": "IRSTCB01CAM156N",
    "BOJ": "IRSTCB01JPM156N",
    "SNB": "IRSTCB01CHM156N",
    "RBNZ": "IRSTCB01NZM156N",
}


async def fetch_from_fred(
    db_session: AsyncSession,
    series_id: str,
    bank: str,
    as_of_date: date | None = None,
) -> dict[int, float]:
    """Return an empty curve for policy-rate-only FRED proxies.

    These FRED series are historical policy-rate levels, not OIS or futures
    forward curves, so the probability calculator should fall back to 100%
    hold for future meetings.
    """
    _ = (db_session, as_of_date)
    logger.info(
        "%s FRED series %s is policy history only; no forward curve cached.",
        bank.upper(),
        series_id,
    )
    return {}
