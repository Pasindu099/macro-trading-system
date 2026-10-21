from datetime import date

import httpx
import pytest

from app.services import cb_other_tracking, cb_price_series


def test_official_source_shapes_map_to_correct_monthly_index():
    fixtures = {
        "BOE": b'{"months":[{"date":"2026 SEP","value":"144.1"}]}',
        "BOC": b'[{"object":{"vectorDataPoint":[{"refPer":"2026-09-01","value":170.0}]}}]',
        "SNB": b'{"timeseries":[{"metadata":{"key":"EPB@SNB.plkopr{LD2010100}"},"values":[{"date":"2026-09","value":101.4737}]},{"metadata":{"key":"EPB@SNB.plkopr{VVP}"},"values":[{"date":"2026-09","value":0.99}]}]}',
        "BOJ": "date,All items,All items less fresh food\n202608,102.2,102.0\n".encode("cp932"),
    }
    expected = {"BOE": 144.1, "BOC": 170.0, "SNB": 101.4737, "BOJ": 102.0}
    for bank, content in fixtures.items():
        response = httpx.Response(200, content=content, request=httpx.Request("GET", "https://example.org"))
        assert cb_price_series.parse_levels(bank, response) == [(date(2026, 9 if bank != "BOJ" else 8, 1), expected[bank])]


def test_q4_tracking_uses_same_half_point_threshold_and_missing_month_is_unavailable():
    points = [(date(year, month, 1), 100 * (1.002 ** ((year - 2025) * 12 + month)))
              for year in (2025, 2026) for month in range(1, 13 if year == 2025 else 10)]
    result = cb_other_tracking.track("BOE", points, 2.4, 2026)
    assert result["status"] == "on_track"
    assert result["required_pace"] is not None and result["actual_3m_ann"] is not None
    assert cb_other_tracking.track("BOE", points[:-2] + points[-1:], 2.4, 2026)["status"] == "unavailable"


def test_boj_fiscal_average_uses_12_months_and_core_index():
    points = [(date(year, month, 1), 100 * (1.002 ** ((year - 2025) * 12 + month)))
              for year in (2025, 2026) for month in range(1, 13 if year == 2025 else 9)]
    result = cb_other_tracking.track("BOJ", points, 2.4, 2026)
    assert result["horizon"] == "FY2026 average"
    assert result["series"] == "CB_JP_CORE_CPI"
    assert result["status"] == "on_track"


@pytest.mark.asyncio
async def test_projection_measure_and_horizon_are_bank_specific(monkeypatch):
    async def dates(_): return [date(2026, 8, 1)]
    async def values(bank, _):
        variable = "core_cpi_inflation" if bank == "BOJ" else "cpi_inflation"
        horizon = "2026" if bank == "BOJ" else "2026Q4"
        return {(variable, horizon, "median" if bank == "BOJ" else "point"): 2.4}
    async def levels(_, __): return []
    monkeypatch.setattr(cb_other_tracking.cb_projection_loaders, "rounds", dates)
    monkeypatch.setattr(cb_other_tracking.cb_projection_loaders, "round_values", values)
    monkeypatch.setattr(cb_other_tracking.cb_price_series, "load_levels", levels)
    for bank in cb_other_tracking.BANKS:
        result = await cb_other_tracking.get_tracking(bank)
        variable = "core_cpi_inflation" if bank == "BOJ" else "cpi_inflation"
        assert result["variables"][variable]["reason"] == "No matching official CPI index levels stored"
