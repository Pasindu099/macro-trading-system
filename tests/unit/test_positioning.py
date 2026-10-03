"""Step 5 Part D: positioning metrics."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services import positioning as pos


def _tuesdays(n: int, start: date = date(2020, 1, 7)) -> list[date]:
    return [start + timedelta(weeks=i) for i in range(n)]


def test_percentile_edges_min_zero_max_hundred() -> None:
    window = [5.0, -3.0, 10.0, 0.0]
    assert pos.percentile_rank(window, -3.0) == 0
    assert pos.percentile_rank(window, 10.0) == 100
    assert pos.percentile_rank([1.0, 1.0, 1.0], 1.0) == 50
    assert pos.percentile_rank([4.0], 4.0) is None


def test_rolling_percentile_respects_lookback_window() -> None:
    dates = _tuesdays(200)
    # An extreme low two years ago drops out of the 1y window but stays in 3y.
    values = [0.0] * 200
    values[50] = -100.0
    values[-1] = -10.0
    one = pos.rolling_percentiles(dates, values, 1)
    three = pos.rolling_percentiles(dates, values, 3)
    assert one[-1] == 0          # lowest within the last year
    assert three[-1] > 0         # -100 still in the 3y window
    assert one[10] is None and three[100] is None  # window not yet filled
    one_year_back = pos.lookback_start(dates[-1], 1)
    assert sum(d > one_year_back for d in dates) in (52, 53)


@pytest.mark.parametrize(
    ("percentile", "nets", "move", "expected"),
    [
        (92, [100, 90, 80], -1.2, "high"),             # long crowd, spot down, net cut twice
        (92, [100, 90, 80], 0.8, "medium"),            # reducing only
        (92, [80, 90, 100], -0.5, "medium"),           # spot against only
        (92, [80, 90, 100], 0.9, "trend_confirming"),  # still moving with the crowd
        (8, [-100, -90, -80], 1.0, "high"),            # short crowd covering while currency rallies
        (8, [-80, -90, -100], -0.4, "trend_confirming"),
        (50, [100, 90, 80], -1.2, "none"),             # not crowded
    ],
)
def test_squeeze_rule(percentile, nets, move, expected) -> None:
    assert pos.squeeze_status(percentile, nets, move)["status"] == expected


def test_crowding_thresholds_inclusive() -> None:
    assert pos.crowding_label(85) == "crowded_long"
    assert pos.crowding_label(15) == "crowded_short"
    assert pos.crowding_label(50) == "neutral"


def test_overlapping_episodes_counted_once() -> None:
    in_band = lambda p: p > 90  # noqa: E731
    # Enters at 1, leaves, re-enters at 4 (within 8 weeks → same episode), then again at 12.
    pctl = [50, 95, 96, 70, 93, 60, 60, 60, 60, 60, 60, 60, 97, None, 99]
    assert pos.find_episodes(pctl, in_band) == [1, 12]


def test_spot_alignment_uses_report_date_not_release() -> None:
    tuesday = date(2026, 9, 29)
    series = pos.SpotSeries([
        (date(2026, 9, 28), 1.10), (tuesday, 1.11), (date(2026, 10, 2), 1.20),  # Friday release-day close
    ])
    assert series.on(tuesday) == 1.11
    # Holiday on the Tuesday: last close on or before it, never the later release-day close.
    holiday = pos.SpotSeries([(date(2026, 9, 28), 1.10), (date(2026, 10, 2), 1.20)])
    assert holiday.on(tuesday) == 1.10
    assert pos.SpotSeries([(date(2026, 9, 1), 1.0)]).on(tuesday) is None  # stale beyond 5 days


def test_extreme_stats_use_report_date_spot_and_reversal() -> None:
    dates = _tuesdays(170)
    nets = list(range(170))           # steadily rising → late weeks enter >90 once
    nets[160:] = [500] * 10
    weeks = [pos.Week(d, n, 0, 1000) for d, n in zip(dates, nets)]
    spot = pos.SpotSeries([(d, 100.0 - i * 0.1) for i, d in enumerate(dates)])  # currency falls
    bands = pos.extreme_band_stats(weeks, spot)
    assert bands[">90"]["episodes"] >= 1
    assert bands[">90"]["avg_move_4w_pct"] < 0
    assert bands[">90"]["pct_reversed_8w"] == 100.0


def test_usd_index_rises_when_majors_fall() -> None:
    d1, d2 = date(2026, 9, 22), date(2026, 9, 29)
    majors = {c: pos.SpotSeries([(d1, 1.0), (d2, 0.9)]) for c in ("EUR", "JPY")}
    usd = pos.usd_index(majors, [d1, d2])
    assert usd.on(d2) > usd.on(d1)


def test_pair_normalization_market_convention() -> None:
    assert pos.normalize_pair("eurusd") == ("EUR", "USD")
    assert pos.normalize_pair("AUD/CAD") == ("AUD", "CAD")
    with pytest.raises(ValueError):
        pos.normalize_pair("USDEUR")
