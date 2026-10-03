"""Step 8 Part B: deterministic SEP parsing and validation."""

from __future__ import annotations

import copy
from datetime import date
from pathlib import Path

import pytest

from app.processing import fed_sep
from app.services.fed_projections import discover_rounds

FIXTURE = Path("tests/fixtures/sep/fomcprojtabl20260318.htm")


@pytest.fixture(scope="module")
def march_2026():
    return fed_sep.parse_sep(fed_sep.decode_html(FIXTURE.read_bytes()), date(2026, 3, 18))


def test_march_2026_medians_match_published_values(march_2026):
    medians = {v: march_2026.values[(v, "2026", "median")] for v in fed_sep.VARIABLES.values()}
    assert medians == {"real_gdp": 2.4, "unemployment_rate": 4.4, "pce_inflation": 2.7,
                       "core_pce_inflation": 2.7, "federal_funds_rate": 3.4}
    assert march_2026.horizons == ["2026", "2027", "2028", "longer_run"]
    assert ("core_pce_inflation", "longer_run", "median") not in march_2026.values  # not projected


def test_march_2026_ranges_dots_risk_and_errors(march_2026):
    assert (march_2026.values[("real_gdp", "2026", "ct_low")], march_2026.values[("real_gdp", "2026", "ct_high")]) == (2.2, 2.5)
    assert sum(n for (h, _), n in march_2026.dots.items() if h == "2026") == 19
    assert march_2026.dots[("2026", 3.625)] == 7
    assert march_2026.risk[("real_gdp", "uncertainty")] == (0, 4, 15)
    assert march_2026.risk[("real_gdp", "risk")] == (14, 5, 0)
    assert march_2026.errors[("real_gdp", "2026")] == 1.5
    assert march_2026.errors[("federal_funds_rate", "2028")] == 2.5
    assert fed_sep.validate(march_2026) == []


def test_validation_rejects_corrupted_round(march_2026):
    bad = copy.deepcopy(march_2026)
    bad.values[("pce_inflation", "2026", "median")] = 9.9          # outside its range
    bad.dots[("2027", 3.125)] += 1                                  # 20 dots for 19 participants
    bad.values[("unemployment_rate", "2027", "ct_high")] = 9.0      # central tendency outside range
    problems = fed_sep.validate(bad)
    assert any("pce_inflation 2026: median" in p for p in problems)
    assert any("dots 2027" in p for p in problems)
    assert any("unemployment_rate 2027: central tendency" in p for p in problems)


def test_implausible_value_rejected(march_2026):
    bad = copy.deepcopy(march_2026)
    for stat in fed_sep.STATS:
        bad.values[("federal_funds_rate", "2026", stat)] = 25.0
    assert any("plausible bounds" in p for p in fed_sep.validate(bad))


@pytest.mark.parametrize(("text", "expected"), [
    ("2.2–2.5", (2.2, 2.5)), ("-7.6--5.5", (-7.6, -5.5)), ("4.5-6.0", (4.5, 6.0)),
    ("−1.0–7.0", (-1.0, 7.0)), ("0.1", (0.1, 0.1)),
])
def test_range_parsing_handles_negative_and_single_values(text, expected):
    assert fed_sep.parse_range(text) == expected


def test_declared_non_submitters_scoped_to_this_round():
    text = ("Eighteen participants submitted information in conjunction with the June 16–17, 2026, meeting; "
            "one of these 18 participants did not submit projections for 2028. "
            "Eighteen participants submitted information in conjunction with the September 15–16, 2026, meeting; "
            "one of these 18 participants did not submit projections for 2028 and 2029.")
    assert fed_sep.declared_missing(text, date(2026, 9, 16)) == {"2028": 1, "2029": 1}
    assert fed_sep.declared_missing(text, date(2026, 6, 17)) == {"2028": 1}


def test_round_discovery_includes_misspelled_march_2022_and_2020_rounds():
    html = ('<a href="/monetarypolicy/fomcprojtable20220316.htm"></a>'
            '<a href="/monetarypolicy/fomcprojtabl20260318.htm"></a>')
    rounds = discover_rounds(html)
    assert rounds[date(2022, 3, 16)].endswith("fomcprojtable20220316.htm")
    assert date(2020, 6, 10) in rounds and date(2026, 3, 18) in rounds
