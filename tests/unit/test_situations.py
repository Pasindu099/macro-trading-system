"""Step 10 Part A: source features and missing-data behaviour."""

from datetime import date, timedelta

import pytest

from app.services.situations import Series, features


def history():
    day = date(2026, 6, 30)
    daily = [day - timedelta(days=i) for i in range(100, -1, -1)]
    return day, {
        "fred": {
            "DCOILBRENTEU": Series({d: 100 if i < 30 else 125 for i, d in enumerate(daily)}),
            "VIXCLS": Series({d: 18 if i < 96 else 27 for i, d in enumerate(daily)}),
            "SP500": Series({d: 100 if i < 91 else 94 for i, d in enumerate(daily)}),
        },
        "indicators": {
            ("EU", "cpi_headline_yoy"): Series({day: 3.0}),
            ("EU", "core_cpi_yoy"): Series({day: 2.5}),
            ("US", "unemployment_rate"): Series({day - timedelta(days=30 * i): 4.0 if i < 3 else 3.0
                                                  for i in range(12)}),
        },
        "yields": {
            ("DE", "2Y"): Series({d: 2.0 + i * .01 for i, d in enumerate(daily)}),
            ("DE", "10Y"): Series({d: 2.5 + i * .005 for i, d in enumerate(daily)}),
            ("DE", "30Y"): Series({d: 3.0 + i * .02 for i, d in enumerate(daily)}),
        },
        "fx": {"USD/JPY": Series({d: 159.0 for d in daily})},
        "spreads": {("FR-DE", "10Y"): Series({d: 70 + i * .2 for i, d in enumerate(daily)})},
    }


def test_source_features_and_unavailable_eer():
    day, source = history()
    assert features("energy_shock", "EUR", day, source)["headline_core_gap_pp"] == 0.5
    assert features("risk_off", "GLOBAL", day, source)["sp500_10d_pct"] == pytest.approx(-6)
    assert features("fr_fiscal_stress", "EUR", day, source)["oat_bund_20d_change_bp"] == pytest.approx(4)
    assert features("labor_deterioration", "USD:US", day, source)["unemployment_gap_pp"] > 0.5
    assert features("bear_steepening", "EUR:DE", day, source)["two_y_change_less_than_curve"] == 1
    assert features("intervention_risk_jpy", "USD/JPY", day, source)["intervention_zone_distance_pct"] == 0
    assert features("yields_up_currency_down", "EUR", day, source)["currency_index_10d_pct"] is None
