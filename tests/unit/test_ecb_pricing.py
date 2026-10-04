from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.services import ecb_pricing
from app.services.rate_fetchers import ecb_fetcher


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)


class _Session:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, *_args, **_kwargs):
        return _Rows(self.rows)


@pytest.mark.asyncio
async def test_german_yield_horizons_and_repricing(monkeypatch):
    today = date.today()
    rows = [SimpleNamespace(maturity="2Y", day=today - timedelta(days=days), rate=rate)
            for days, rate in ((0, 2.0), (7, 1.9), (30, 1.8))]

    async def rate(*_args):
        return {"rate": 2.5}

    monkeypatch.setattr(ecb_pricing, "resolve_current_rate", rate)
    result = await ecb_pricing.get_ecb_yield_approximation(_Session(rows))
    assert [h["yield_tenor"] for h in result["horizons"]] == ["2Y"] * 3
    assert result["horizons"][-1]["implied_move_bp"] == -50
    assert result["main_change_1w_bp"] == 10
    assert result["main_change_1m_bp"] == 20
    assert result["meeting_probabilities"] == "not priced"
    assert "not €STR OIS" in result["label"]


@pytest.mark.asyncio
async def test_no_yields_and_disabled_invalid_ecb_request():
    assert ecb_pricing.choose_tenor({"3M", "2Y"}, 12) == "3M"
    assert await ecb_fetcher.fetch_estr_ois_curve(None) == {}
