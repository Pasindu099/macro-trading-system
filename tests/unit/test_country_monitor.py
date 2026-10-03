"""Step 9 Part B: Eurostat parsing and EZ-relative country comparison."""

from app.services.country_monitor import relative_tone
from app.services import country_monitor
from scripts.ingest_eurostat_deficit import parse_observations
from tests.unit.test_desks import make_client


def test_relative_tone_and_unemployment_inversion():
    assert relative_tone(3.0, 2.0) == "stronger"
    assert relative_tone(1.0, 2.0) == "weaker"
    assert relative_tone(3.0, 2.0, inverted=True) == "weaker"
    assert relative_tone(1.0, 2.0, inverted=True) == "stronger"
    assert relative_tone(2.0, 2.0) == "neutral"
    assert relative_tone(3.0, 2.0, inflation=True) == "hotter"
    assert relative_tone(1.0, 2.0, inflation=True) == "cooler"
    assert relative_tone(None, 2.0) == "unavailable"
    assert relative_tone(2.0, None) == "unavailable"


def test_eurostat_json_stat_dimension_order():
    payload = {
        "id": ["geo", "time", "unit"], "size": [3, 2, 1],
        "dimension": {
            "geo": {"category": {"index": {"DE": 0, "EA20": 1, "FR": 2}}},
            "time": {"category": {"index": {"2024": 0, "2025": 1}}},
            "unit": {"category": {"index": {"PC_GDP": 0}}},
        },
        "value": {"0": -2.0, "1": -3.0, "2": -2.5, "3": -3.2, "4": -5.0, "5": -5.5},
    }
    rows = parse_observations(payload)
    assert len(rows) == 6
    assert {r["country_code"]: r["balance_pct_gdp"] for r in rows if r["year"] == 2025} == {
        "EZ": -3.2, "DE": -3.0, "FR": -5.5,
    }


def test_eur_country_panel_with_data_and_unavailable(monkeypatch):
    async def available(_desk):
        return {"status": "available", "members": ["EZ", "DE", "FR"],
                "indicators": [{"id": "hicp", "label": "HICP YoY", "values": {
                    code: {"value": value, "tone": "neutral"} for code, value in
                    (("EZ", 2.0), ("DE", 2.2), ("FR", None))}}],
                "budget_balance": {}, "oat_bund_10y": {"status": "unavailable", "reason": "No FR yield"}}

    monkeypatch.setattr(country_monitor, "get_country_monitor", available)
    client = make_client()
    response = client.get("/desks/EUR/panels/country")
    assert response.status_code == 200
    assert "HICP YoY" in response.text and "2.20" in response.text
    assert "No FR yield" in response.text

    async def unavailable(_desk):
        return {"status": "unavailable", "reason": "No members"}

    from app.services import desks
    desks.cache_clear()
    monkeypatch.setattr(country_monitor, "get_country_monitor", unavailable)
    response = client.get("/desks/EUR/panels/country")
    assert response.status_code == 200 and "No members" in response.text
