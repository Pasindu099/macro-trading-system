"""Authenticated JSON access to CFTC TFF positioning metrics."""

from collections.abc import Awaitable

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import require_role
from app.services import positioning
from app.services import positioning_step18, positioning_values
from app.settings import get_settings


def _require_viewer(request: Request) -> None:
    if get_settings().auth_enabled:
        require_role("viewer")(request)


router = APIRouter(prefix="/api/positioning", tags=["positioning"], dependencies=[Depends(_require_viewer)])


async def _call(result: Awaitable[dict]) -> dict:
    try:
        return await result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# Fixed paths are declared before /{currency} so they are not captured by it.
@router.get("/crowding")
async def crowding(lookback: str = Query("3y", pattern="^(52w|1y|3y|5y)$")) -> dict:
    return await positioning.get_crowding(lookback)


@router.get("/flows")
async def flows(window: str = Query("1W", pattern="^(1W|4W)$")) -> dict:
    return await positioning.get_flows(window)


@router.get("/squeeze")
async def squeeze(lookback: str = Query("3y", pattern="^(52w|1y|3y|5y)$")) -> dict:
    original = await positioning.get_squeeze(lookback)
    return {**original, "watch_v2": await positioning_step18.squeeze_watch(lookback, original)}


@router.get("/summary")
async def summary(lookback: str = Query("3y", pattern="^(52w|1y|3y|5y)$")) -> dict:
    return await positioning.get_summary(lookback)


@router.get("/divergence")
async def divergence(lookback: str = Query("3y", pattern="^(52w|1y|3y|5y)$")) -> dict:
    return await positioning.get_divergence(lookback)


@router.get("/retail")
async def institutions_vs_retail(lookback: str = Query("3y", pattern="^(52w|1y|3y|5y)$")) -> dict:
    return await positioning.get_institutions_vs_retail(lookback)


@router.get("/extremes/{currency}")
async def extremes(currency: str) -> dict:
    original = await _call(positioning.get_extremes(currency))
    return {**original, "comparison_v2": await _call(positioning_step18.extremes(currency))}


@router.get("/simple")
async def simple(lookback: str = Query("3y", pattern="^(52w|1y|3y|5y)$")):
    return await positioning_step18.big_traders(lookback)


@router.get("/history/{instrument:path}")
async def history(instrument: str, group: str = Query("leveraged_funds", pattern="^(leveraged_funds|asset_manager|legacy_noncommercial)$"),
                  units: str = Query("usd", pattern="^(usd|pct_oi)$"),
                  range_key: str = Query("3Y", pattern="^(1Y|3Y|5Y|2010)$")):
    return await _call(positioning_values.history(instrument, group=group, units=units, range_key=range_key))


@router.get("/pair/{pair:path}")
async def pair(pair: str, weeks: int = Query(156, ge=1, le=1000)) -> dict:
    return await _call(positioning.get_pair_positioning(pair, weeks=weeks))


@router.get("/{currency}")
async def currency_detail(currency: str, weeks: int = Query(52, ge=1, le=1000)) -> dict:
    return await _call(positioning.get_currency_positioning(currency, weeks))
