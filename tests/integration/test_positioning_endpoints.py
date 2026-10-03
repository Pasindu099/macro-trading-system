"""Positioning API smoke checks against the compose database."""

import os

from fastapi.testclient import TestClient

os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["AUTH_ENABLED"] = "false"

from app.settings import get_settings  # noqa: E402
get_settings.cache_clear()
from app.main import app  # noqa: E402


def test_positioning_endpoints_return_service_payloads():
    with TestClient(app) as client:
        crowding = client.get("/api/positioning/crowding?lookback=3y")
        detail = client.get("/api/positioning/EUR?weeks=52")
        flows = client.get("/api/positioning/flows?window=4W")
        squeeze = client.get("/api/positioning/squeeze")
        extremes = client.get("/api/positioning/extremes/JPY")
        pair = client.get("/api/positioning/pair/AUDJPY")
        bad = client.get("/api/positioning/flows?window=2W")
    assert {r.status_code for r in (crowding, detail, flows, squeeze, extremes, pair)} == {200}
    assert bad.status_code == 422
    assert len(crowding.json()["currencies"]) == 8
    assert len(detail.json()["categories"]["leveraged_funds"]["history"]) == 52
    assert set(extremes.json()["bands"]) == {">90", "85-90", "10-15", "<10"}
    assert pair.json()["pair"] == "AUD/JPY"
