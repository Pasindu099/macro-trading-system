from datetime import date, timedelta

import pytest

from app.services.curve_metrics import calculate_curve, classify_regime, latest_uninversion


@pytest.mark.parametrize(("d2", "d10", "expected"), [
    (18, 4, "bear_flattener"), (5, 15, "bear_steepener"),
    (-20, -5, "bull_steepener"), (-3, -12, "bull_flattener"),
    (-4, 3, "twist_steepener"), (40, -2, "twist_flattener"),
])
def test_regime_uses_2y_and_10y(d2, d10, expected):
    assert classify_regime(d2, d10) == expected


def test_curve_unavailable_long_end_and_stale_policy():
    start = date(2026, 8, 3)
    end = date(2026, 9, 3)
    result = calculate_curve("CH", {
        "2Y": {start: 1.0, end: 1.2},
        "10Y": {start: 1.5, end: 1.6},
    }, policy_rate=0.25, policy_date=date(2025, 3, 20))
    assert result["two_ten"]["value_bp"] == pytest.approx(40)
    assert result["ten_thirty"] == {"status": "unavailable", "reason": "30Y government yield is absent", "tenors": ["10Y", "30Y"]}
    assert result["two_policy"]["status"] == "unavailable"
    assert result["regime"]["label"] == "bear_flattener"
    assert result["regime"]["tenors"] == ["2Y", "10Y"]


def test_uninversion_requires_ninety_calendar_days():
    start = date(2026, 1, 1)
    slopes = {start + timedelta(days=day): -1.0 for day in range(90)}
    slopes[start + timedelta(days=90)] = 0.2
    assert latest_uninversion(slopes) == start + timedelta(days=90)
    slopes.pop(start + timedelta(days=90))
    slopes[start + timedelta(days=89)] = 0.2
    assert latest_uninversion(slopes) is None
