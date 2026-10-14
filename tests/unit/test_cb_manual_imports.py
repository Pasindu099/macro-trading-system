"""Step 12 decision: manual imports for blocked / PDF-only sources use the loaders' rules."""

from datetime import date
from pathlib import Path

import pytest

from app.services import cb_manual_imports as m
from app.services.source_identity import user_agent

FIX = Path("tests/fixtures/cb_projections")


def test_manual_sources_from_config():
    assert set(m.manual_sources("RBNZ")) == {"projections", "statements", "speeches"}
    assert set(m.manual_sources("RBA")) == {"projections", "statements", "speeches"}
    assert m.manual_sources("BOJ") == {"projections": "Outlook Report forecasts are PDF only"}
    assert m.manual_sources("FED") == {} and not m.is_manual("BOE", "statements")


def test_manual_values_validated_like_loaders():
    rows = m.validate_values("BOJ", date(2026, 7, 31), m.parse_value_lines(
        "cpi_inflation, 2026, 2.8\nreal_gdp, 2027q4, 0.9\n\npolicy_rate, constant, 0.5"))
    assert [(r["horizon"], r["stat"]) for r in rows] == [("2026", "point"), ("2027Q4", "point"), ("constant", "assumption")]
    with pytest.raises(ValueError, match="Implausible"):
        m.validate_values("BOJ", date(2026, 7, 31), [("cpi_inflation", "2026", 40.0)])
    with pytest.raises(ValueError, match="Horizon"):
        m.validate_values("BOJ", date(2026, 7, 31), [("cpi_inflation", "FY2026", 2.0)])
    with pytest.raises(ValueError, match="Unknown variable"):
        m.validate_values("BOJ", date(2026, 7, 31), [("core_core_cpi", "2026", 2.0)])
    with pytest.raises(ValueError, match="Line 2"):
        m.parse_value_lines("cpi_inflation, 2026, 2.8\nreal_gdp 2027 0.9")


def test_uploaded_files_use_existing_parsers():
    rows = m.parse_projection_file("BOE", "mpr.html", (FIX / "boe_mpr_2026_07.html").read_bytes(), date(2026, 7, 30))
    assert any(r["variable"] == "policy_rate" for r in rows)
    with pytest.raises(ValueError, match="enter the values manually"):
        m.parse_projection_file("BOJ", "outlook.pdf", b"%PDF", date(2026, 7, 31))
    with pytest.raises(ValueError, match="too short"):
        m.statement_text("RBA", "decision.txt", b"Cash rate unchanged.")
    body = m.statement_text("RBA", "decision.txt", ("The Board decided to leave the cash rate unchanged. " * 10).encode())
    assert body.startswith("The Board decided")


def test_identifying_user_agent_no_browser_spoof():
    assert user_agent().startswith("ForexCompass/1.0 (research dashboard") and "Mozilla" not in user_agent()


async def test_admin_page_lists_manual_sources_with_last_import(monkeypatch):
    from starlette.requests import Request
    from app.api.routes import cb_imports

    async def last_import(bank, kind):
        return date(2026, 10, 1) if bank == "RBNZ" and kind == "projections" else None

    async def recent():
        return []
    monkeypatch.setattr(cb_imports.imports, "last_import", last_import)
    monkeypatch.setattr(cb_imports.imports, "import_summary", recent)
    from app.main import app
    request = Request({"type": "http", "method": "GET", "path": "/admin/cb-imports", "headers": [],
                       "query_string": b"msg=Rejected", "app": app, "router": app.router})
    response = await cb_imports.cb_imports_page(request, None)
    html = response.body.decode()
    assert response.status_code == 200 and "manual import — last updated 2026-10-01" in html
    assert "BOJ · projections" in html and "never" in html and "Rejected" in html
