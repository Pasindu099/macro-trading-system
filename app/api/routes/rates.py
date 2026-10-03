"""Authenticated JSON access to derived rates research."""

from fastapi import APIRouter, Depends, Query, Request

from app.auth import require_role
from app.services.curve_metrics import POLICY_INDICATORS, WINDOW_DAYS, get_curve
from app.services.rates_drivers import get_pair_drivers
from app.services.yield_spreads import get_spread
from app.settings import get_settings


def _require_rates_user(request: Request) -> None:
    if get_settings().auth_enabled:
        require_role("viewer")(request)


router = APIRouter(prefix="/api/rates", tags=["rates"], dependencies=[Depends(_require_rates_user)])


@router.get("/curve/{country}")
async def country_curve(country: str, window: str = Query("1M", pattern="^(1W|1M|3M)$")) -> dict:
    return await get_curve(country.upper(), window)


@router.get("/regimes")
async def all_regimes() -> dict:
    rows = []
    for country in POLICY_INDICATORS:
        for window in WINDOW_DAYS:
            curve = await get_curve(country, window)
            rows.append({"country": country, "window": window,
                         "regime": curve.get("regime", {"status": "unavailable", "reason": curve.get("reason", "No curve")})})
    return {"regimes": rows}


@router.get("/drivers/{pair:path}")
async def pair_drivers(pair: str) -> dict:
    name = pair.upper().replace("/", "")
    normalized = f"{name[:3]}/{name[3:]}" if len(name) == 6 else pair.upper()
    return {"pair": normalized, "drivers": {
        tenor: await get_pair_drivers(normalized, tenor) for tenor in ("2Y", "10Y")
    }}


@router.get("/spreads/{pair:path}")
async def pair_spread(
    pair: str,
    tenor: str = Query("2Y", pattern="^(2Y|10Y|30Y)$"),
    days: int = Query(250, ge=1, le=2000),
) -> dict:
    return await get_spread(pair, tenor, days)
