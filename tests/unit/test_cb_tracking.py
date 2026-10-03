"""Step 8 Part C: required-pace maths, status thresholds, revisions flag."""

import pytest

from app.services import cb_tracking as ct
from app.services.fred import parse_observations


def test_required_monthly_pace_hits_q4_average_target():
    # Last year's Q4 average 100; projection 3% → Q4 average 103. Data through June at 101.
    monthly = {m: 100 + m / 6 for m in range(1, 7)}  # June = 101
    pace = ct.required_monthly_pace(monthly, 100.0, 3.0)
    g = (1 + pace / 100) ** (1 / 12) - 1
    q4 = sum(101 * (1 + g) ** (k - 6) for k in (10, 11, 12)) / 3
    assert q4 == pytest.approx(103.0, abs=1e-6)


def test_required_pace_uses_published_q4_months():
    # October already printed: only Nov and Dec are solved for.
    monthly = {m: 100.0 for m in range(1, 10)} | {10: 102.0}
    pace = ct.required_monthly_pace(monthly, 100.0, 2.0)
    g = (1 + pace / 100) ** (1 / 12) - 1
    assert (102 + 102 * (1 + g) + 102 * (1 + g) ** 2) / 3 == pytest.approx(102.0, abs=1e-6)
    assert ct.required_monthly_pace({m: 1.0 for m in range(1, 13)}, 1.0, 2.0) is None  # year complete


def test_quarterly_pace_and_3m_annualised():
    # Q4 base 100, projection 2% → 102; Q2 at 101 → two quarters left.
    required = ct.required_quarterly_pace({1: 100.5, 2: 101.0}, 100.0, 2.0)
    q = (102 / 101) ** 0.5 - 1
    assert required == pytest.approx(((1 + q) ** 4 - 1) * 100)
    assert ct.annualised_3m([100.0, 100.5, 101.0, 101.0 * 1.0]) == pytest.approx(((101 / 100) ** 4 - 1) * 100)


@pytest.mark.parametrize(("actual", "required", "band", "expected"), [
    (3.6, 3.0, 0.5, "running_hot"), (3.5, 3.0, 0.5, "on_track"), (2.4, 3.0, 0.5, "running_cold"),
    (4.1, 3.0, 1.0, "running_hot"), (2.0, 3.0, 1.0, "on_track"), (None, 3.0, 0.5, None),
])
def test_status_thresholds(actual, required, band, expected):
    assert ct.status_vs_required(actual, required, band) == expected


def test_unemployment_status_direction():
    assert ct.unemployment_status(3.8, 4.1) == "running_hot"    # tighter than projected
    assert ct.unemployment_status(4.4, 4.1) == "running_cold"
    assert ct.unemployment_status(4.25, 4.1) == "on_track"


@pytest.mark.parametrize(("infl", "funds", "flag"), [
    (0.2, 0.0, "tolerance"), (0.3, 0.25, "response"), (0.1, 0.0, "none"), (0.4, -0.25, "none"), (None, 0.0, "none"),
])
def test_reaction_function_flag(infl, funds, flag):
    assert ct.reaction_flag(infl, funds) == flag


def test_fred_missing_values_skipped():
    rows = parse_observations({"observations": [
        {"date": "2026-08-01", "value": "126.1", "realtime_start": "2026-09-26"},
        {"date": "2026-09-01", "value": ".", "realtime_start": "2026-10-01"}]})
    assert [(r["observation_date"].isoformat(), r["value"]) for r in rows] == [("2026-08-01", 126.1)]
