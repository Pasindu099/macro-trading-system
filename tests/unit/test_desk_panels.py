"""Step 6 Part B/C: every panel returns 200 with data and 200 with a clear state without it."""

from __future__ import annotations

import re

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import desk_panels as dp
from app.services import desks
from app.services.curve_metrics import calculate_curve
from tests.unit.test_desks import make_client

TODAY = date.today()
DAYS = [TODAY - timedelta(days=i) for i in range(400, -1, -1)]
PENDING = {"situations", "fedpath", "gap", "scenarios"}


def _stub(value):
    async def fn(*args, **kwargs):
        return value
    return fn


def _data_sources(monkeypatch):
    curves = {
        "2Y": {d: 3.5 + i * 0.001 for i, d in enumerate(DAYS)},
        "10Y": {d: 4.0 + i * 0.0005 for i, d in enumerate(DAYS)},
        "30Y": {d: 4.6 + i * 0.0004 for i, d in enumerate(DAYS)},
    }
    curve = calculate_curve("US", curves, window="1M", policy_rate=3.75, policy_date=TODAY - timedelta(days=20))
    fx = {pair: [(d, 1.1 + i * 0.0001) for i, d in enumerate(DAYS)] for pair in
          ["USD/DXY", "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "NZD/USD", "USD/CAD", "USD/CHF"]}
    monthly = [date(TODAY.year - 1, m, 1) for m in range(1, 13)] + [date(TODAY.year, 1, 1)]
    monkeypatch.setattr(dp, "get_curve", _stub(curve))
    monkeypatch.setattr(dp, "fx_series", _stub(fx))
    monkeypatch.setattr(dp, "yield_series", _stub(curves))
    monkeypatch.setattr(dp, "policy_history", _stub([(TODAY - timedelta(days=500), 4.0), (TODAY - timedelta(days=20), 3.75)]))
    monkeypatch.setattr(dp, "get_macro_state_board", _stub({"source": "cb_preferred_score", "rows": [
        {"currency": "USD", "inflation_score": 1.2, "labor_score": -0.4, "growth_score": 0.3,
         "overall_score": 0.5, "date": TODAY, "confidence": "high"}]}))
    monkeypatch.setattr(dp, "build_event_innovation_feed", _stub({"rows": [
        {"category": "Inflation", "initial": 0.8, "current": 0.6},
        {"category": "Labor", "initial": -0.5, "current": -0.4}]}))
    monkeypatch.setattr(dp, "indicator_history", _stub({
        "cpi_headline_yoy": [(d, 3.0 + i * 0.05) for i, d in enumerate(monthly)],
        "ism_manufacturing_pmi": [(d, 49 + i * 0.2) for i, d in enumerate(monthly)],
        "nfp": [(d, 150 - i) for i, d in enumerate(monthly)]}))
    monkeypatch.setattr(dp, "get_cb_policy_data", _stub({"banks": [{"bank": "FED", "reports": [
        {"date": TODAY, "doc_type": "statement", "tone_score": 0.4, "tone_change": "more hawkish",
         "inflation_outlook": "Elevated", "growth_outlook": "Solid", "labor_outlook": "Balanced"}]}]}))
    meeting = {"meeting_at": datetime.now(UTC) + timedelta(days=20), "cut_prob": 0.1, "hold_prob": 0.7,
               "hike_prob": 0.2, "implied_rate": 3.8, "market_data_available": True, "data_state": "live"}
    monkeypatch.setattr(dp, "get_rate_probability_view", _stub({
        "meetings": [meeting] * 3, "market_data": {"source": "stub"}, "current_rate": 3.75}))
    lf = {"percentile": 90.0, "crowding": "crowded_long", "net": 100, "report_date": "2026-09-29"}
    monkeypatch.setattr(dp.positioning_service, "get_crowding", _stub({"currencies": [
        {"currency": "USD", "leveraged_funds": lf, "asset_manager": {**lf, "percentile": 50.0, "crowding": "neutral"}}]}))
    monkeypatch.setattr(dp.positioning_service, "get_squeeze", _stub({"currencies": [
        {"currency": "USD", "leveraged_funds": {"status": "medium"}, "asset_manager": {"status": "none"}}]}))
    monkeypatch.setattr(dp, "upcoming_events", _stub([SimpleNamespace(
        released_at=datetime.now(UTC) + timedelta(days=3), display_name="CPI", period="Sep", importance=3)]))
    monkeypatch.setattr(dp, "news_alerts", _stub([
        {"headline": "Fed speaker says X", "url": "https://example.com", "source": "s", "detected_at": datetime.now(UTC),
         "implied_tier": "CB_POLICY_DIVERGENCE", "severity": "HIGH", "alert_text": "context"}]))


def _empty_sources(monkeypatch):
    monkeypatch.setattr(dp, "get_curve", _stub({"country": "US", "status": "unavailable", "reason": "No yields"}))
    monkeypatch.setattr(dp, "fx_series", _stub({}))
    monkeypatch.setattr(dp, "yield_series", _stub({}))
    monkeypatch.setattr(dp, "policy_history", _stub([]))
    monkeypatch.setattr(dp, "get_macro_state_board", _stub({"source": "currency_stance", "rows": []}))
    monkeypatch.setattr(dp, "build_event_innovation_feed", _stub({"rows": []}))
    monkeypatch.setattr(dp, "indicator_history", _stub({}))
    monkeypatch.setattr(dp, "get_cb_policy_data", _stub({"banks": []}))
    monkeypatch.setattr(dp, "get_rate_probability_view", _stub({"meetings": [], "market_data": {}}))
    monkeypatch.setattr(dp.positioning_service, "get_crowding", _stub({"currencies": []}))
    monkeypatch.setattr(dp.positioning_service, "get_squeeze", _stub({"currencies": []}))
    monkeypatch.setattr(dp, "upcoming_events", _stub([]))
    monkeypatch.setattr(dp, "news_alerts", _stub([]))


@pytest.fixture(autouse=True)
def _clear_cache():
    desks.cache_clear()
    yield
    desks.cache_clear()


@pytest.mark.parametrize("panel_id", [p.id for p in dp.PANELS])
def test_panel_200_with_data(monkeypatch, panel_id):
    _data_sources(monkeypatch)
    resp = make_client().get(f"/desks/USD/panels/{panel_id}")
    assert resp.status_code == 200
    assert "state-error" not in resp.text
    if panel_id in PENDING:
        assert "Available after step" in resp.text
    elif panel_id not in {"verdict"}:
        # Data panels render content, not a missing-data state (key data keeps GDP unavailable by design).
        assert "state-empty" not in resp.text
        if panel_id != "keydata":
            assert "state-unavailable" not in resp.text


@pytest.mark.parametrize("panel_id", [p.id for p in dp.PANELS])
def test_panel_200_with_unavailable_state_when_service_returns_nothing(monkeypatch, panel_id):
    _empty_sources(monkeypatch)
    resp = make_client().get(f"/desks/USD/panels/{panel_id}")
    assert resp.status_code == 200
    assert "state-error" not in resp.text
    assert any(s in resp.text for s in ("state-empty", "state-unavailable", "state-pending")), panel_id


def test_pending_panels_carry_no_numbers(monkeypatch):
    _data_sources(monkeypatch)
    client = make_client()
    for panel_id in PENDING:
        body = client.get(f"/desks/USD/panels/{panel_id}").text
        # The pending state box (and anything after it) must hold no numbers.
        visible = re.sub(r"<[^>]+>", " ", body.split('class="state state-pending"', 1)[-1])
        visible = re.sub(r"(?i)step \d+", "", visible)
        assert not re.search(r"\d", visible), (panel_id, visible)


def test_keydata_marks_missing_series_unavailable_without_substitution(monkeypatch):
    _data_sources(monkeypatch)
    body = make_client().get("/desks/USD/panels/keydata").text
    assert "Not in DB (real_gdp_qoq_annualised)" in body
    assert "Not in DB (ism_manufacturing_production)" in body
    assert "Inflation driver (oil) · Available after step 10" in body


def test_interactions_return_pressed_state(monkeypatch):
    _data_sources(monkeypatch)
    client = make_client()
    assert 'aria-pressed="true"' in client.get("/desks/USD/panels/price?range=1M").text
    curve = client.get("/desks/USD/panels/curve?window=1W").text
    assert 'aria-pressed="true"' in curve and "(1W · 2Y vs 10Y)" in curve
    news = client.get("/desks/USD/panels/news?type=CB_POLICY_DIVERGENCE").text
    assert "Fed speaker says X" in news


def test_usd_change_sign_convention():
    pts_up = [(date(2026, 9, 1), 1.10), (date(2026, 9, 29), 1.21)]  # EUR/USD up 10% → USD weaker
    assert dp.usd_change(pts_up, 7, -1) == pytest.approx((1.10 / 1.21 - 1) * 100)
    pts_jpy = [(date(2026, 9, 1), 100.0), (date(2026, 9, 29), 110.0)]  # USD/JPY up → USD stronger
    assert dp.usd_change(pts_jpy, 7, 1) == pytest.approx(10.0)
