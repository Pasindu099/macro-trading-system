"""Authenticated JSON access to CFTC TFF positioning metrics."""

from collections.abc import Awaitable

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import require_role
from app.services import positioning
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
async def crowding(lookback: str = Query("3y", pattern="^(1y|3y|5y)$")) -> dict:
    return await positioning.get_crowding(lookback)


@router.get("/flows")
async def flows(window: str = Query("1W", pattern="^(1W|4W)$")) -> dict:
    return await positioning.get_flows(window)


@router.get("/squeeze")
async def squeeze() -> dict:
    return await positioning.get_squeeze()


@router.get("/extremes/{currency}")
async def extremes(currency: str) -> dict:
    return await _call(positioning.get_extremes(currency))


@router.get("/pair/{pair:path}")
async def pair(pair: str) -> dict:
    return await _call(positioning.get_pair_positioning(pair))


@router.get("/{currency}")
async def currency_detail(currency: str, weeks: int = Query(52, ge=1, le=1000)) -> dict:
    return await _call(positioning.get_currency_positioning(currency, weeks))
