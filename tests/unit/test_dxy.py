from datetime import date

import pytest

from app.services.dxy import DXY_WEIGHTS, compute_dxy


def test_dxy_formula_matches_ice_definition() -> None:
    day = date(2026, 9, 29)
    closes = {"EUR/USD": 1.17, "USD/JPY": 148.0, "GBP/USD": 1.35, "USD/CAD": 1.39, "USD/SEK": 9.4, "USD/CHF": 0.80}
    expected = (50.14348112 * 1.17 ** -0.576 * 148.0 ** 0.136 * 1.35 ** -0.119
                * 1.39 ** 0.091 * 9.4 ** 0.042 * 0.80 ** 0.036)
    result = compute_dxy({pair: {day: value} for pair, value in closes.items()})
    assert result[day] == pytest.approx(expected, rel=1e-6)
    assert 90 < result[day] < 105


def test_dxy_requires_all_six_components() -> None:
    legs = {pair: {date(2026, 9, 29): 1.0} for pair in DXY_WEIGHTS}
    legs["USD/SEK"] = {}
    assert compute_dxy(legs) == {}
