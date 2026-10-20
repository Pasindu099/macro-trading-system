from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.auth as auth
from app.main import app
from app.services import desks, pairs_service


def test_pairs_page_and_pending_route(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(auth_enabled=False))
    async def screener():
        return {"rows": [{"pair": "EUR/USD", "href": "/pairs/EURUSD", "pip_size": .0001,
                          "last": 1.1234, "week_change": 1.5, "month_change": -2.0,
                          "overall_score": 1.2, "partial": True, "policy_gap": None,
                          "data_implied": None, "priced": None, "gap_tooltip": "EUR: hawkish",
                          "spread_2y": 20, "spread_change_20d": 5, "driver": "diverging",
                          "driver_as_of": None, "cot_implied": 30,
                          "retail_long_pct": 35, "retail_long_positions": 10,
                          "retail_short_positions": 20, "opposite_sides": True,
                          "warnings": [{"name": "Correlation break", "severity": "high"}]}],
                "freshness": {}}
    monkeypatch.setattr(pairs_service, "screener", screener)
    desks.cache_clear()
    client = TestClient(app)
    response = client.get("/pairs")
    assert response.status_code == 200
    for label in ("EUR/USD", "1.1234", "partial", "pending", "diverging", "opposite sides",
                  "Correlation break", "/pairs/EURUSD", "Matrix"):
        assert label in response.text
    pending = client.get("/pairs/EURUSD")
    assert pending.status_code == 200
    assert "Pair page pending" in pending.text and "/desks/USD" in pending.text
    assert client.get("/pairs/INVALID").status_code == 404
    desks.cache_clear()
