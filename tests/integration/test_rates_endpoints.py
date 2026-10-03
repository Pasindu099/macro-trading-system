"""Rates API smoke checks against the compose database."""

import os

from fastapi.testclient import TestClient

os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["AUTH_ENABLED"] = "false"

from app.settings import get_settings  # noqa: E402
get_settings.cache_clear()
from app.main import app  # noqa: E402


def test_rates_endpoints_return_service_payloads():
    with TestClient(app) as client:
        curve = client.get("/api/rates/curve/US?window=1M")
        spread = client.get("/api/rates/spreads/USDJPY?tenor=2Y&days=10")
        regimes = client.get("/api/rates/regimes")
        drivers = client.get("/api/rates/drivers/AUDCAD")
    assert curve.status_code == spread.status_code == regimes.status_code == drivers.status_code == 200
    assert curve.json()["regime_tenors"] == ["2Y", "10Y"]
    assert spread.json()["pair"] == "USD/JPY"
    assert spread.json()["tenor"] == "2Y"
    assert len(regimes.json()["regimes"]) == 24
    assert drivers.json()["drivers"]["2Y"]["status"] in {"weak_link", "diverging", "aligned"}
