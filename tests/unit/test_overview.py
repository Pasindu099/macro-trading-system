from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.auth as auth
from app.main import app
from app.services import overview


def _client(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(auth_enabled=False))
    return TestClient(app)


def test_each_overview_panel_renders_data_and_pending(monkeypatch):
    client = _client(monkeypatch)
    data = {
        "hero": {"state": "ok", "driver": "data", "theme": "Data drives FX.", "hierarchy": overview.HIERARCHY},
        "situations": {"state": "ok", "rows": [{"href": "/desks/EUR", "name": "Fiscal stress", "scope_key": "EUR", "severity": "high", "started_at": __import__("datetime").datetime(2026, 1, 1)}]},
        "news": {"state": "ok", "rows": [], "ai_paused": True},
        "ranking": {"state": "ok", "rows": [{"currency": "USD", "macro": 0.5, "date": None, "cot": None, "curve": None, "verdict": None, "enabled": False}]},
        "central-banks": {"state": "ok", "rows": []},
        "pairs": {"state": "ok", "rows": [], "concentration": None},
        "events": {"state": "ok", "rows": []},
    }
    for panel_id, ctx in data.items():
        async def builder(result=ctx):
            return result
        monkeypatch.setitem(overview.PANELS, panel_id, builder)
        response = client.get(f"/overview/panels/{panel_id}")
        assert response.status_code == 200
        assert "<h2>" in response.text or "<h1>G10 Overview</h1>" in response.text
        async def pending():
            return {"state": "pending", "message": "Desk pending"}
        monkeypatch.setitem(overview.PANELS, panel_id, pending)
        response = client.get(f"/overview/panels/{panel_id}")
        assert response.status_code == 200
        assert "Desk pending" in response.text
        assert "0.5" not in response.text


def test_pair_score_and_policy_lens_are_distinct():
    rows = overview._pair_rows({"EUR": 1.2, "USD": 0.2, "JPY": -0.4}, [
        {"pair": "EUR/USD", "base": "EUR", "quote": "USD"},
        {"pair": "USD/JPY", "base": "USD", "quote": "JPY"},
    ])
    assert rows[0] == {"pair": "EUR/USD", "overall": 1.0, "policy": "available"}
    assert rows[1]["policy"] == "pending"


def test_ranking_sorts_by_macro_composite(monkeypatch):
    async def board():
        return {"rows": [{"currency": "EUR", "overall_score": -0.2, "date": None},
                         {"currency": "USD", "overall_score": 0.6, "date": None}]}
    async def crowding():
        return {"currencies": []}
    async def verdict_for(_currency):
        return {"status": "unavailable"}
    async def curve(_country, _window):
        return {"regime": {"status": "unavailable"}}
    async def regime():
        return {"regime": "unavailable"}
    monkeypatch.setattr(overview, "_board", board)
    monkeypatch.setattr(overview.positioning, "get_crowding", crowding)
    monkeypatch.setattr(overview.verdict, "get_verdict", verdict_for)
    monkeypatch.setattr(overview, "get_curve", curve)
    monkeypatch.setattr(overview.fed_regime, "get_regime", regime)
    monkeypatch.setattr(overview.ecb_regime, "get_regime", regime)
    rows = __import__("asyncio").run(overview.currency_ranking())["rows"]
    assert [row["currency"] for row in rows[:2]] == ["USD", "EUR"]
    assert rows[2]["macro"] is None
