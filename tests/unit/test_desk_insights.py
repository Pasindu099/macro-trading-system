"""Step 10 desk evidence visibility and JSON routes."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import desk_insights
from app.services import situations
from tests.unit.test_desks import make_client


def _stub(value):
    async def run(*args, **kwargs):
        return value
    return run


def test_situation_evidence_panels_only_when_active(monkeypatch):
    monkeypatch.setattr(situations, "get_situation_episodes", _stub([]))
    quiet = make_client().get("/desks/EUR/panels/situations")
    assert quiet.status_code == 200 and "No active situations" in quiet.text
    assert 'id="french_fiscal"' not in quiet.text

    from app.services import desks
    desks.cache_clear()
    monkeypatch.setattr(situations, "get_situation_episodes", _stub([{
        "situation_id": "fr_fiscal_stress", "scope": "currency", "scope_key": "EUR",
        "name": "French fiscal stress", "severity": "high",
        "evidence": {"current": {"oat_bund_10y_bp": 92.0, "oat_bund_20d_change_bp": 12.0}},
    }]))
    active = make_client().get("/desks/EUR/panels/situations")
    assert active.status_code == 200 and 'id="french_fiscal"' in active.text
    assert "Shown because" in active.text and "92.0bp" in active.text
    desks.cache_clear()


def test_insight_routes_return_service_outputs(monkeypatch):
    monkeypatch.setattr(desk_insights, "get_settings", lambda: SimpleNamespace(auth_enabled=False))
    monkeypatch.setattr(desk_insights, "get_situation_episodes", _stub([{"situation_id": "risk_off"}]))
    monkeypatch.setattr(desk_insights, "get_verdict", _stub({"status": "available", "bias": "bullish"}))
    monkeypatch.setattr(desk_insights, "get_scenarios", _stub({"status": "available", "scenarios": []}))
    app = FastAPI()
    app.include_router(desk_insights.router)
    client = TestClient(app)
    assert client.get("/api/situations?active=true").json()["episodes"][0]["situation_id"] == "risk_off"
    assert client.get("/api/situations/history").status_code == 200
    assert client.get("/api/verdict/USD").json()["bias"] == "bullish"
    assert client.get("/api/scenarios/EUR").status_code == 200
