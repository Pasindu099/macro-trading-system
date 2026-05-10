"""ECB euro short-term rate OIS forward curve fetcher."""

from __future__ import annotations

import csv
import logging
from datetime import date
from io import StringIO

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rate_fetchers.cache import load_cached_curve, upsert_ois_curve

logger = logging.getLogger(__name__)

ECB_API_BASE = "https://data-api.ecb.europa.eu/service/data"
ECB_ESTR_OIS_SERIES = "EON/D.ESTR.OIFW.D.SPOT.ON.EUR.T.I"
ECB_YC_SERIES_PREFIX = "YC/B.U2.EUR.4F.G_N_C.SV_C_YM"
TENOR_DAYS = (7, 30, 90, 180, 270, 365, 548, 730)
YC_PROXY_TENORS = {
    90: "IF_3M",
    180: "IF_6M",
    365: "IF_1Y",
    730: "IF_2Y",
}
TENOR_ALIASES = {
    "1W": 7,
    "1M": 30,
    "3M": 90,
    "6M": 180,
    "9M": 270,
    "12M": 365,
    "1Y": 365,
    "18M": 548,
    "24M": 730,
    "2Y": 730,
}


async def fetch_estr_ois_curve(
    db_session: AsyncSession,
    as_of_date: date | None = None,
) -> dict[int, float]:
    """Return {tenor_days: rate_pct} for the most recent available ECB curve."""
    target_date = as_of_date or date.today()
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(
                f"{ECB_API_BASE}/{ECB_ESTR_OIS_SERIES}",
                params={
                    "format": "csvdata",
                    "detail": "dataonly",
                    "endPeriod": target_date.isoformat(),
                    "lastNObservations": 16,
                },
                headers={"Accept": "text/csv"},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("ECB ESTR OIS fetch failed: %s; trying ECB YC proxy.", exc)
        return await fetch_ecb_yield_curve_proxy(db_session, as_of_date=as_of_date)

    curve = _parse_ecb_csv(response.text, target_date)
    if not curve:
        logger.warning("ECB ESTR OIS response contained no curve points; trying ECB YC proxy.")
        return await fetch_ecb_yield_curve_proxy(db_session, as_of_date=as_of_date)

    await upsert_ois_curve(
        db_session,
        bank="ECB",
        curve_date=target_date,
        values=curve,
        source="ecb_estr_ois",
    )
    return curve


async def fetch_ecb_yield_curve_proxy(
    db_session: AsyncSession,
    as_of_date: date | None = None,
) -> dict[int, float]:
    """Fetch ECB official instantaneous-forward yield-curve points as a fallback proxy."""
    target_date = as_of_date or date.today()
    curve: dict[int, float] = {}
    curve_date: date | None = None

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for tenor_days, data_type in YC_PROXY_TENORS.items():
            series_key = f"{ECB_YC_SERIES_PREFIX}.{data_type}"
            try:
                response = await client.get(
                    f"{ECB_API_BASE}/{series_key}",
                    params={
                        "format": "csvdata",
                        "detail": "dataonly",
                        "endPeriod": target_date.isoformat(),
                        "lastNObservations": 1,
                    },
                    headers={"Accept": "text/csv"},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("ECB YC proxy fetch failed for %s: %s", data_type, exc)
                continue

            point_date, value = _parse_single_ecb_observation(response.text)
            if value is None:
                continue
            curve[tenor_days] = value
            if point_date is not None:
                curve_date = max(curve_date, point_date) if curve_date else point_date

    if not curve:
        return await load_cached_curve(db_session, bank="ECB")

    # Anchor the very short end to the deposit facility so the first meeting is not flat
    # only because the official yield curve starts at three months.
    curve.setdefault(7, 2.0)
    curve.setdefault(30, 2.0)
    resolved_curve_date = curve_date or target_date
    await upsert_ois_curve(
        db_session,
        bank="ECB",
        curve_date=resolved_curve_date,
        values=curve,
        source="ecb_yc_proxy",
    )
    return curve


async def get_implied_rate_at_date(
    db_session: AsyncSession,
    target_date: date,
) -> float:
    """Linear interpolation of cached/fetched OIS curve to a target date."""
    curve = await fetch_estr_ois_curve(db_session)
    if not curve:
        raise ValueError("No ECB OIS curve available")
    tenor_days = max(1, (target_date - date.today()).days)
    return _interpolate(curve, tenor_days)


def _parse_ecb_csv(raw_csv: str, as_of_date: date) -> dict[int, float]:
    rows = list(csv.DictReader(StringIO(raw_csv)))
    if not rows:
        return {}

    latest_period = _latest_period(rows, as_of_date)
    points: dict[int, float] = {}
    positional_index = 0
    for row in rows:
        if _row_date(row) != latest_period:
            continue
        tenor = _row_tenor_days(row)
        if tenor is None and positional_index < len(TENOR_DAYS):
            tenor = TENOR_DAYS[positional_index]
            positional_index += 1
        value = _row_value(row)
        if tenor is not None and value is not None:
            points[tenor] = value
    return points


def _latest_period(rows: list[dict[str, str]], as_of_date: date) -> date | None:
    dates = [
        row_date
        for row in rows
        if (row_date := _row_date(row)) is not None and row_date <= as_of_date
    ]
    return max(dates, default=None)


def _row_date(row: dict[str, str]) -> date | None:
    for key in ("TIME_PERIOD", "time_period", "DATE", "date"):
        value = row.get(key)
        if not value:
            continue
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            continue
    return None


def _row_tenor_days(row: dict[str, str]) -> int | None:
    for value in row.values():
        normalized = str(value or "").strip().upper()
        if normalized in TENOR_ALIASES:
            return TENOR_ALIASES[normalized]
    return None


def _row_value(row: dict[str, str]) -> float | None:
    for key in ("OBS_VALUE", "obs_value", "VALUE", "value"):
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except ValueError:
            continue
    return None


def _parse_single_ecb_observation(raw_csv: str) -> tuple[date | None, float | None]:
    rows = list(csv.DictReader(StringIO(raw_csv)))
    for row in reversed(rows):
        value = _row_value(row)
        if value is not None:
            return _row_date(row), value
    return None, None


def _interpolate(curve: dict[int, float], tenor_days: int) -> float:
    points = sorted(curve.items())
    if tenor_days <= points[0][0]:
        return points[0][1]
    if tenor_days >= points[-1][0]:
        return points[-1][1]
    for (left_days, left_rate), (right_days, right_rate) in zip(points, points[1:], strict=True):
        if left_days <= tenor_days <= right_days:
            weight = (tenor_days - left_days) / (right_days - left_days)
            return left_rate + ((right_rate - left_rate) * weight)
    return points[-1][1]
