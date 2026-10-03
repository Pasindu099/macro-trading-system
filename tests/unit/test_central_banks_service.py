from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import central_banks


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def scalars(self):
        return self


class _Session:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    async def execute(self, statement):
        self.calls += 1
        return _Result(self.results.pop(0))


@pytest.mark.asyncio
async def test_macro_monitor_keeps_current_previous_and_history(monkeypatch):
    monkeypatch.setattr(central_banks, "_MM_WATCHLIST", [{
        "id": "us", "bank": "FED", "country_code": "US", "currency": "USD",
        "inflation_target": 2.0, "rate_indicator": "policy_rate",
        "metrics": [{"key": "cpi", "canonical": "cpi_headline_yoy"}],
    }])
    ids = [SimpleNamespace(id=1, canonical_name="cpi_headline_yoy"),
           SimpleNamespace(id=2, canonical_name="policy_rate")]
    releases = [
        SimpleNamespace(indicator_id=1, actual=3.0, released_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        SimpleNamespace(indicator_id=1, actual=2.8, released_at=datetime(2026, 4, 1, tzinfo=timezone.utc)),
        SimpleNamespace(indicator_id=2, actual=4.0, released_at=datetime(2026, 4, 1, tzinfo=timezone.utc)),
    ]
    session = _Session(ids, releases)
    rows = await central_banks.get_macro_monitor_data(session)
    assert rows[0]["metrics_data"]["cpi"]["current"] == 2.8
    assert rows[0]["metrics_data"]["cpi"]["previous"] == 3.0
    assert rows[0]["rate"] == 4.0
    assert "flag" not in rows[0]
    assert session.calls == 2


@pytest.mark.asyncio
async def test_policy_data_returns_raw_tone_and_date():
    doc = SimpleNamespace(id=11, bank="FED", doc_date=date(2026, 9, 1),
                          analyzed_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
                          doc_type="statement", tone_score=1.5, tone_change_vs_prior="hawkish",
                          inflation_outlook="rising", growth_outlook="stable", labor_outlook="softening")
    data = await central_banks.get_cb_policy_data(_Session([doc]))
    assert data["banks"][0]["latest_tone_score"] == 1.5
    assert data["banks"][0]["reports"][0]["date"] == date(2026, 9, 1)
    assert "tone_class" not in data["banks"][0]


@pytest.mark.asyncio
async def test_projections_empty_result():
    data = await central_banks.get_projections_data(_Session([]))
    assert data == {"latest_path_by_bank": {}, "projection_comparison": [], "has_projections": False}
