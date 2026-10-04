"""Step 8 Part E: /api/cb/{bank}/* routes."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.routes import cb


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(cb, "get_settings", lambda: SimpleNamespace(auth_enabled=False))

    async def stub(*args, **kwargs):
        return {"ok": True, "args": [str(a) for a in args]}

    for module, names in ((cb.fed_projections, ("get_projections", "get_dots", "get_risk_balance")),
                          (cb.cb_tracking, ("get_tracking", "get_revisions")), (cb.fed_regime, ("get_regime", "get_gap")),
                          (cb.ecb_projections, ("get_projections",)),
                          (cb.ecb_tracking, ("get_tracking", "get_revisions")),
                          (cb.ecb_regime, ("get_regime", "get_gap"))):
        for name in names:
            monkeypatch.setattr(module, name, stub)
    app = FastAPI()
    app.include_router(cb.router)
    return TestClient(app)


def test_all_six_endpoints_for_fed(monkeypatch):
    client = _client(monkeypatch)
    for path in ("projections?round=all", "projections", "dots", "risk-balance?round=2026-09-16", "tracking", "regime", "gap"):
        assert client.get(f"/api/cb/fed/{path}").status_code == 200, path
    assert client.get("/api/cb/FED/projections?round=all").json()["args"] == ["all"]


def test_ecb_endpoints_and_bad_round_422(monkeypatch):
    client = _client(monkeypatch)
    for path in ("projections", "tracking", "regime", "gap"):
        assert client.get(f"/api/cb/ECB/{path}").status_code == 200
    assert client.get("/api/cb/ECB/dots").json()["status"] == "unavailable"
    assert client.get("/api/cb/ECB/risk-balance").json()["status"] == "unavailable"
    assert client.get("/api/cb/BOE/projections").status_code == 404
    assert client.get("/api/cb/FED/dots?round=junk").status_code == 422
    assert client.get("/api/cb/FED/projections?round=junk").status_code == 422


def test_viewer_auth_required_when_enabled(monkeypatch):
    monkeypatch.setattr(cb, "get_settings", lambda: SimpleNamespace(auth_enabled=True))
    with pytest.raises(HTTPException) as exc:
        cb._require_viewer(Request({"type": "http", "method": "GET", "path": "/api/cb/FED/gap"}))
    assert exc.value.status_code == 401
