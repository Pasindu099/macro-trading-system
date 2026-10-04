"""ECB MPD fixture, HICP tracking and qualitative gap rules."""

from datetime import date
from pathlib import Path

import pytest

from app.services.ecb_projections import parse_mpd_csv, round_date
from app.services.ecb_regime import qualitative_gap
from app.services.ecb_tracking import track_hicp
from app.services.fed_regime import classify_regime

FIXTURE = Path("tests/fixtures/ecb/mpd_w24.csv")


def test_mpd_fixture_has_four_point_series_and_plausible_values():
    rows = parse_mpd_csv(FIXTURE.read_text(encoding="utf-8-sig"), "W24")
    assert len(rows) == 12
    assert {row["variable"] for row in rows} == {"hicp_inflation", "core_hicp_inflation",
                                                      "real_gdp", "unemployment_rate"}
    assert all(row["stat"] == "median" and row["release_date"] == date(2024, 3, 1) for row in rows)
    assert next(row["value"] for row in rows if row["variable"] == "hicp_inflation"
                and row["horizon"] == "2025") == 2.0


def test_mpd_parser_rejects_implausible_values():
    payload = FIXTURE.read_text(encoding="utf-8-sig").replace('"2.1"', '"99.0"', 1)
    with pytest.raises(ValueError, match="Implausible"):
        parse_mpd_csv(payload, "W24")


def test_round_marker_and_market_conditioned_gap():
    assert round_date("G25") == date(2025, 6, 1)
    assert qualitative_gap({"2026": 2.2, "2028": 1.8})["direction"] == "dovish"
    assert qualitative_gap({"2028": 2.2})["direction"] == "hawkish"
    assert qualitative_gap({"2028": 2.05})["direction"] == "neutral"
    assert "gap_bp" not in qualitative_gap({"2028": 1.8})


def test_ecb_hicp_tracking_uses_fed_thresholds_and_rate_regime_rules():
    points = [(date(2025, month, 1), 100.0) for month in (10, 11, 12)]
    points += [(date(2026, month, 1), 100.0 + month * 0.1) for month in range(1, 7)]
    entry = track_hicp(points, 2.0, 2026)
    assert entry["required_pace"] is not None and entry["actual_3m_ann"] is not None
    assert entry["status"] in {"running_hot", "on_track", "running_cold"}
    decisions = [(date(2026, 1, 1), 3.0), (date(2026, 6, 1), 2.75)]
    assert classify_regime(decisions, date(2026, 7, 1))["regime"] == "cutting"
