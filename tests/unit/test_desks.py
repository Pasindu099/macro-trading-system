"""Step 6 Part A/C: desk config, routing, indicator validation and panel cache."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app.api.routes import desks as routes
from app.services import desk_panels, desks


def make_client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_cache():
    desks.cache_clear()
    yield
    desks.cache_clear()


def test_usd_and_eur_desks_enabled():
    assert desks.get_desk("usd")["cb"] == "FED"
    eur = desks.get_desk("EUR")
    assert eur["cb"] == "ECB" and eur["curve_country"] == "DE"
    assert [member["code"] for member in eur["members"]] == ["EZ", "DE", "FR"]
    assert next(item for item in eur["key_data"] if item["id"] == "gdp")["label"] == "Real GDP QoQ (not annualised)"
    assert desks.get_desk("XXX") is None
    nav = desks.desk_nav("USD")
    assert [d["code"] for d in nav] == desks.DESK_ORDER
    assert [d["code"] for d in nav if d["enabled"]] == ["USD", "EUR"]


def test_usd_and_eur_desks_render_and_disabled_desks_404():
    client = make_client()
    page = client.get("/desks/USD")
    assert page.status_code == 200
    # Every panel is a lazy HTMX partial in mockup order, with a loading state.
    order = [page.text.index(f'hx-get="/desks/USD/panels/{p.id}"') for p in routes._panels(desks.get_desk("USD"))]
    assert order == sorted(order)
    assert page.text.count("state-loading") == len(routes._panels(desks.get_desk("USD")))
    assert "htmx" in page.text and "desk.js" in page.text
    assert client.get("/desks/EUR").status_code == 200
    assert 'hx-get="/desks/EUR/panels/country"' in client.get("/desks/EUR").text
    assert client.get("/desks/USD/panels/country").status_code == 404
    assert client.get("/desks/GBP").status_code == 404
    assert client.get("/desks/USD/panels/nope").status_code == 404


def test_indicator_validation_reports_unknown_names_without_crashing():
    desk = {"charts": [
        {"id": "infl", "series": [{"name": "CPI", "canonical": "cpi_headline_yoy"},
                                  {"name": "Mystery", "canonical": "not_a_real_series"}]},
        {"id": "gdp", "series": [{"name": "GDP", "canonical": None}]},
    ]}
    report = desks.validate_indicator_mapping(desk, {"cpi_headline_yoy"})
    assert report["checked"] == 2
    assert [m["series"] for m in report["missing"]] == ["Mystery", "GDP"]


def test_usd_config_maps_every_requested_indicator():
    names = {s["canonical"] for c in desks.get_desk("USD")["charts"] for s in c["series"]}
    assert {"cpi_headline_yoy", "core_cpi_yoy", "core_pce_price_index_yoy", "unemployment_rate",
            "real_gdp_qoq_annualised", "ism_manufacturing_pmi", "ism_services_pmi", "nfp"} <= names


def test_panel_cached_for_60_seconds_and_errors_not_cached(monkeypatch):
    calls = {"n": 0}

    async def builder(desk, params):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("source down")
        return desk_panels.pending(10, "Scenarios")

    panel = desk_panels.PANELS_BY_ID["scenarios"]
    monkeypatch.setitem(desk_panels.PANELS_BY_ID, "scenarios",
                        desk_panels.Panel(panel.id, panel.anchor, panel.n, panel.label, panel.wide, builder))
    client = make_client()
    first = client.get("/desks/USD/panels/scenarios")
    assert first.status_code == 200 and "state-error" in first.text and "Retry" in first.text
    second = client.get("/desks/USD/panels/scenarios")
    third = client.get("/desks/USD/panels/scenarios")
    assert "Available after step 10" in second.text and second.text == third.text
    assert calls["n"] == 2
    assert desks.PANEL_CACHE_SECONDS == 60


def test_cache_expires(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(desks.time, "monotonic", lambda: clock["t"])
    desks.cache_set(("USD", "x", ()), "<p>hi</p>")
    clock["t"] += 59
    assert desks.cache_get(("USD", "x", ())) == "<p>hi</p>"
    clock["t"] += 2
    assert desks.cache_get(("USD", "x", ())) is None
