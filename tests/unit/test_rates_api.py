from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.routes import rates


def test_four_rates_routes_are_registered():
    paths = {route.path for route in rates.router.routes}
    assert {
        "/api/rates/curve/{country}", "/api/rates/spreads/{pair:path}",
        "/api/rates/regimes", "/api/rates/drivers/{pair:path}",
    } <= paths


def test_rates_routes_require_viewer_when_auth_enabled(monkeypatch):
    monkeypatch.setattr(rates, "get_settings", lambda: SimpleNamespace(auth_enabled=True))
    request = Request({"type": "http", "method": "GET", "path": "/api/rates/regimes"})
    with pytest.raises(HTTPException) as exc:
        rates._require_rates_user(request)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_curve_and_drivers_delegate_to_services(monkeypatch):
    async def fake_curve(country, window):
        return {"country": country, "window": window}

    async def fake_drivers(pair, tenor):
        return {"pair": pair, "tenor": tenor}

    monkeypatch.setattr(rates, "get_curve", fake_curve)
    monkeypatch.setattr(rates, "get_pair_drivers", fake_drivers)
    curve = await rates.country_curve("us", "1M")
    drivers = await rates.pair_drivers("EURUSD")
    assert curve == {"country": "US", "window": "1M"}
    assert drivers["drivers"]["2Y"] == {"pair": "EUR/USD", "tenor": "2Y"}
    assert drivers["drivers"]["10Y"] == {"pair": "EUR/USD", "tenor": "10Y"}
