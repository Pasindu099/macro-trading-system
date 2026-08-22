from __future__ import annotations

from datetime import date

from app.api.routes.pages import (
    _repricing_anchors,
    _repricing_beta,
    _repricing_curve,
    _repricing_regime,
)


def _weekdays(start: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor = date.fromordinal(cursor.toordinal() + 1)
    return days


class TestRepricingCurve:
    def test_maps_dates_to_closes(self) -> None:
        curve = _repricing_curve([
            {"date": "2026-08-20", "close": 4.19},
            {"date": "2026-08-21", "close": 4.25},
        ])
        assert curve == {date(2026, 8, 20): 4.19, date(2026, 8, 21): 4.25}

    def test_skips_rows_without_a_usable_close(self) -> None:
        curve = _repricing_curve([
            {"date": "2026-08-20", "close": None},
            {"date": None, "close": 4.25},
            "not-a-row",
            {"date": "2026-08-21", "close": 4.25},
        ])
        assert curve == {date(2026, 8, 21): 4.25}


class TestRepricingAnchors:
    def test_prior_is_anchored_by_calendar_not_row_offset(self) -> None:
        """A market that skipped days must still measure a 7-day window.

        Counting a fixed number of rows back would span a longer calendar
        window for a gappy series, making its change incomparable to a
        complete one.
        """
        dates = [
            date(2026, 8, 3),
            date(2026, 8, 14),  # gap: this market was closed for over a week
            date(2026, 8, 20),
            date(2026, 8, 21),
        ]
        latest, prior = _repricing_anchors(dates)
        assert latest == date(2026, 8, 21)
        assert prior == date(2026, 8, 14)
        assert (latest - prior).days >= 7

    def test_complete_and_gappy_series_get_the_same_window(self) -> None:
        complete = _weekdays(date(2026, 8, 3), 15)
        gappy = [value for value in complete if value != complete[-2]]

        complete_latest, complete_prior = _repricing_anchors(complete)
        gappy_latest, gappy_prior = _repricing_anchors(gappy)

        assert complete_latest == gappy_latest
        assert complete_prior == gappy_prior

    def test_falls_back_to_oldest_when_history_is_short(self) -> None:
        dates = [date(2026, 8, 20), date(2026, 8, 21)]
        assert _repricing_anchors(dates) == (date(2026, 8, 21), date(2026, 8, 20))

    def test_returns_none_without_two_points(self) -> None:
        assert _repricing_anchors([]) is None
        assert _repricing_anchors([date(2026, 8, 21)]) is None


class TestCrossDateContamination:
    def test_spread_uses_only_dates_both_legs_printed(self) -> None:
        """The regression this guards: spreading each leg's own latest close.

        USD prints on the 21st, CAD only through the 20th. Spreading the two
        latest closes crosses dates and reports a move that never happened.
        """
        usd = {
            date(2026, 8, 14): 4.00,
            date(2026, 8, 20): 4.10,
            date(2026, 8, 21): 4.50,  # USD moves hard on a day CAD has no print
        }
        cad = {
            date(2026, 8, 14): 3.00,
            date(2026, 8, 20): 3.10,
        }

        shared = sorted(set(usd) & set(cad))
        anchors = _repricing_anchors(shared)
        assert anchors is not None
        latest, prior = anchors

        assert latest == date(2026, 8, 20)
        spread_now = (usd[latest] - cad[latest]) * 100
        spread_then = (usd[prior] - cad[prior]) * 100

        assert round(spread_now, 6) == 100.0
        assert round(spread_now - spread_then, 6) == 0.0

        # Spreading each leg's own latest close instead would report +140bp,
        # inventing a 40bp move out of a date mismatch.
        naive = (usd[date(2026, 8, 21)] - cad[date(2026, 8, 20)]) * 100
        assert round(naive, 6) == 140.0


class TestRepricingBeta:
    def test_recovers_a_known_beta(self) -> None:
        """FX moves 0.02% per bp of spread change, so beta must come back 0.02."""
        curve_dates = _weekdays(date(2026, 1, 5), 60)
        # Daily spread changes must actually vary: a constant step has zero
        # variance and the fit is correctly undefined.
        steps = [((index * 7) % 11) - 5 for index in range(len(curve_dates))]

        spread: dict[date, float] = {}
        fx: dict[date, float] = {}
        level, price = 0.0, 100.0
        for value, step in zip(curve_dates, steps, strict=True):
            # Apply the step to both legs before recording, so the same step
            # drives this date's spread change and FX return.
            level += step
            price *= 1 + (step * 0.02) / 100
            spread[value] = level
            fx[value] = price

        fit = _repricing_beta(spread, fx)
        assert fit is not None
        beta, sigma = fit
        assert round(beta, 4) == 0.02
        assert sigma >= 0.0

    def test_returns_none_below_the_sample_floor(self) -> None:
        curve_dates = _weekdays(date(2026, 1, 5), 10)
        spread = {value: float(index) for index, value in enumerate(curve_dates)}
        fx = {value: 100.0 + index for index, value in enumerate(curve_dates)}
        assert _repricing_beta(spread, fx) is None

    def test_returns_none_when_the_spread_never_moves(self) -> None:
        curve_dates = _weekdays(date(2026, 1, 5), 60)
        spread = dict.fromkeys(curve_dates, 5.0)
        fx = {value: 100.0 + index for index, value in enumerate(curve_dates)}
        assert _repricing_beta(spread, fx) is None


class TestRepricingRegime:
    def _curves(
        self,
        front_change: float,
        long_change: float,
    ) -> tuple[dict[date, float], dict[date, float]]:
        prior, latest = date(2026, 8, 14), date(2026, 8, 21)
        return (
            {prior: 4.00, latest: 4.00 + front_change},
            {prior: 4.50, latest: 4.50 + long_change},
        )

    def test_bear_flattening_when_front_leads_the_selloff(self) -> None:
        front, long = self._curves(front_change=0.20, long_change=0.05)
        regime = _repricing_regime(front, long)
        assert regime["label"] == "BEAR FLATTENING"
        assert "hawkish" in regime["detail"]

    def test_bear_steepening_when_the_long_end_leads(self) -> None:
        front, long = self._curves(front_change=0.02, long_change=0.20)
        assert _repricing_regime(front, long)["label"] == "BEAR STEEPENING"

    def test_bull_steepening_when_the_front_end_rallies_hardest(self) -> None:
        front, long = self._curves(front_change=-0.25, long_change=-0.05)
        assert _repricing_regime(front, long)["label"] == "BULL STEEPENING"

    def test_bull_flattening_when_the_long_end_rallies_hardest(self) -> None:
        front, long = self._curves(front_change=-0.05, long_change=-0.25)
        assert _repricing_regime(front, long)["label"] == "BULL FLATTENING"

    def test_rangebound_suppresses_a_label_for_noise(self) -> None:
        front, long = self._curves(front_change=0.005, long_change=0.005)
        assert _repricing_regime(front, long)["label"] == "RANGEBOUND"

    def test_empty_curves_yield_no_label(self) -> None:
        assert _repricing_regime({}, {})["label"] == ""
