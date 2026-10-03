"""Step 10 Part A: thresholds and persistence are deterministic."""

from datetime import date, timedelta

import pytest

from app.services.situation_rules import build_episodes, condition, current_state, load_config

SPECS = load_config()["situations"]
START = date(2026, 1, 1)


@pytest.mark.parametrize("situation,evidence,exit_evidence", [
    ("energy_shock", {"brent_3m_pct": 21, "headline_core_gap_pp": 0.3}, {"brent_3m_pct": 9}),
    ("fr_fiscal_stress", {"oat_bund_10y_bp": 81, "oat_bund_20d_change_bp": 11}, {"oat_bund_10y_bp": 69}),
    ("labor_deterioration", {"unemployment_gap_pp": 0.5}, {"unemployment_gap_pp": 0.29}),
    ("risk_off", {"vix": 26, "vix_5d_change_pts": 1, "sp500_10d_pct": -6}, {"vix": 19}),
    ("bear_steepening", {"ten_s_thirty_s_1m_change_bp": 16, "two_y_change_less_than_curve": 1},
     {"ten_s_thirty_s_1m_change_bp": 9}),
    ("yields_up_currency_down", {"two_y_10d_change_bp": 16, "currency_index_10d_pct": -1.1},
     {"two_y_10d_change_bp": 4, "currency_index_10d_pct": -1.1}),
    ("correlation_break", {"rates_drivers_diverging": 1}, {"rates_drivers_diverging": 0}),
    ("intervention_risk_jpy", {"intervention_zone_distance_pct": 2.9},
     {"intervention_zone_distance_pct": 5.1}),
])
def test_each_trigger_exit_and_no_flapping(situation, evidence, exit_evidence):
    spec = SPECS[situation]
    assert condition(spec["trigger"], evidence) is True
    assert condition(spec["exit"], exit_evidence) is True
    days = []
    for offset in range(spec["min_persistence_days"]):
        days.append((START + timedelta(days=offset), evidence))
    for offset in range(spec["exit_persistence_days"] - 1):
        days.append((START + timedelta(days=len(days)), exit_evidence))
    days.append((START + timedelta(days=len(days)), evidence))
    episodes = build_episodes(spec, days)
    assert len(episodes) == 1 and episodes[0]["ended_at"] is None
    for offset in range(spec["exit_persistence_days"]):
        days.append((START + timedelta(days=len(days)), exit_evidence))
    assert build_episodes(spec, days)[0]["ended_at"] == days[-spec["exit_persistence_days"]][0]


def test_missing_data_and_labor_watch():
    spec = SPECS["labor_deterioration"]
    assert condition(spec["trigger"], {}) is None
    assert current_state(spec, [], {"unemployment_gap_pp": 0.35}) == "watch"
    assert build_episodes(spec, [(START, {"unemployment_gap_pp": 0.5}),
                                 (START + timedelta(days=2), {"unemployment_gap_pp": 0.5})]) == []
