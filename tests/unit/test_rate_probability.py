from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import rate_probability
from app.services.rate_probability import (
    compute_outcome_distribution,
    compute_step_implied_rates,
    compute_meeting_probabilities,
    interpolate_ois_rate,
    probability_from_delta,
)


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    async def execute(self, statement, params=None):
        sql = str(statement)
        if "MAX(curve_date)" in sql:
            return _ScalarResult(date(2026, 1, 1))
        if "FROM ois_cache" in sql:
            return _RowsResult([
                SimpleNamespace(tenor_days=30, rate=3.75),
                SimpleNamespace(tenor_days=60, rate=4.00),
                SimpleNamespace(tenor_days=90, rate=3.75),
            ])
        return _RowsResult([])


class _NoCurveSession:
    async def execute(self, statement, params=None):
        sql = str(statement)
        if "MAX(curve_date)" in sql:
            return _ScalarResult(None)
        return _RowsResult([])


class _SnapshotSession:
    async def execute(self, statement, params=None):
        sql = str(statement)
        if "MAX(curve_date)" in sql:
            return _ScalarResult(date(2026, 5, 11))
        if "FROM rate_snapshots" in sql:
            return _RowsResult([
                SimpleNamespace(implied_rate=4.25, delta_bps=-25.0, snapshot_date=date(2026, 5, 1))
            ])
        if "FROM ois_cache" in sql:
            return _RowsResult([
                SimpleNamespace(tenor_days=1, rate=4.50),
                SimpleNamespace(tenor_days=30, rate=4.50),
            ])
        return _RowsResult([])


def test_cut_probability_simple():
    """If current rate=4.00, implied=3.75, step=25bps -> cut_prob=100%."""
    cut, hold, hike = probability_from_delta(-25.0, 25.0)

    assert cut == 1.0
    assert hold == 0.0
    assert hike == 0.0


def test_hold_probability():
    """If delta=0 -> hold=100%."""
    cut, hold, hike = probability_from_delta(0.0, 25.0)

    assert cut == 0.0
    assert hold == 1.0
    assert hike == 0.0


def test_partial_probability():
    """If delta=-12.5bps, step=25bps -> cut_prob=50%, hold=50%, hike=0%."""
    cut, hold, hike = probability_from_delta(-12.5, 25.0)

    assert cut == 0.5
    assert hold == 0.5
    assert hike == 0.0


def test_hike_probability():
    """If delta=+25bps -> hike=100%."""
    cut, hold, hike = probability_from_delta(25.0, 25.0)

    assert cut == 0.0
    assert hold == 0.0
    assert hike == 1.0


def test_boj_10bp_step_hike_distribution():
    distribution = compute_outcome_distribution(10.0, 10)
    cut, hold, hike = probability_from_delta(10.0, 10)

    assert distribution == {0: 0.0, 10: 1.0}
    assert cut == 0.0
    assert hold == 0.0
    assert hike == 1.0


def test_fed_partial_multi_step_cut_distribution():
    distribution = compute_outcome_distribution(-37.5, 25)
    cut, hold, hike = probability_from_delta(-37.5, 25)

    assert distribution == {-50: 0.5, -25: 0.5, 0: 0.0}
    assert cut == 1.0
    assert hold == 0.0
    assert hike == 0.0


def test_interpolation_between_tenors():
    """Given OIS at 30d=4.0% and 90d=4.5%, target at 60d returns ~4.25%."""
    curve_date = date(2026, 1, 1)
    result = interpolate_ois_rate(
        curve_date=curve_date,
        curve={30: 4.0, 90: 4.5},
        target_date=curve_date + timedelta(days=60),
        current_rate=4.5,
    )

    assert result == pytest.approx(4.25)


def test_interpolation_anchors_front_tenor_to_current_rate():
    curve_date = date(2026, 1, 1)
    result = interpolate_ois_rate(
        curve_date=curve_date,
        curve={30: 4.0, 90: 4.5},
        target_date=curve_date + timedelta(days=15),
        current_rate=4.5,
    )

    assert result == pytest.approx(4.25)


def test_step_implied_rates_apply_basis_before_delta():
    meetings = [datetime(2026, 1, 31, tzinfo=UTC)]
    result = compute_step_implied_rates(
        meetings,
        {30: 3.67, 60: 3.67},
        current_policy_rate=3.75,
        step_bps=25,
        rate_basis_adj=0.08,
        curve_date=date(2026, 1, 1),
    )

    assert result[0].implied_rate == pytest.approx(3.75)
    assert result[0].delta_bps == pytest.approx(-8.0)


def test_low_liquidity_config_for_jpy_chf():
    assert rate_probability._bank_config("BOJ")["low_liquidity_curve"] is True
    assert rate_probability._bank_config("SNB")["low_liquidity_curve"] is True
    assert rate_probability._bank_config("BOJ")["step_bps"] == 10


def _next_override_meeting(bank: str, now: datetime) -> tuple[datetime, dict]:
    """First override row after `now`, read from config (no hard-coded meeting date)."""
    payload = rate_probability._load_override_payload(bank)
    row = next(r for r in payload["current"] if rate_probability._parse_dt(r["meeting_dt"]).astimezone(UTC) >= now)
    return rate_probability._parse_dt(row["meeting_dt"]), row


def test_terminal_reference_override_for_fed():
    config = {"current_rate": 3.75, "step_bps": 25, "rate_basis_adj": 0.08, "low_liquidity_curve": False}
    as_of = date.fromisoformat(str(rate_probability._load_override_payload("FED")["as_of_date"]))
    now = datetime(as_of.year, as_of.month, as_of.day, 12, tzinfo=UTC) + timedelta(days=1)
    expected_dt, expected = _next_override_meeting("FED", now)
    rows = rate_probability._load_probability_overrides("FED", config, 2, now=now)

    assert rows[0].meeting_dt == expected_dt
    assert rows[0].cumulative_delta_bps == pytest.approx(float(expected["cumulative_delta_bps"]))
    assert rows[0].hold_prob == pytest.approx(float(expected["hold_prob"]))
    assert rows[0].hike_prob == pytest.approx(float(expected["hike_prob"]))


def test_override_older_than_14_days_is_not_served():
    config = {"current_rate": 3.75, "step_bps": 25, "rate_basis_adj": 0.08, "low_liquidity_curve": False}
    as_of = date.fromisoformat(str(rate_probability._load_override_payload("FED")["as_of_date"]))
    fresh = datetime(as_of.year, as_of.month, as_of.day, tzinfo=UTC) + timedelta(days=14)
    stale = fresh + timedelta(days=1)
    assert rate_probability._load_probability_overrides("FED", config, 2, now=fresh)
    assert rate_probability._load_probability_overrides("FED", config, 2, now=stale) == []


@pytest.mark.asyncio
async def test_stale_override_marks_view_stale_source(monkeypatch):
    meetings = [{"bank": "FED", "meeting_dt": datetime(2026, 10, 28, 18, tzinfo=UTC).isoformat(), "is_official": True}]

    async def fake_meetings(bank, n, db_session):
        return meetings[:n]

    monkeypatch.setattr(rate_probability, "get_upcoming_meetings", fake_meetings)
    monkeypatch.setattr(rate_probability, "_load_override_payload",
                        lambda bank: {"as_of_date": "2026-06-26", "current": [
                            {"meeting_dt": "2026-10-28T14:00:00-04:00", "cumulative_delta_bps": 12.0}]})
    probabilities = await compute_meeting_probabilities("FED", n_meetings=1, db_session=_NoCurveSession())
    assert probabilities[0].data_state == "stale_source"
    assert probabilities[0].hold_prob is None and "older than 14 days" in probabilities[0].data_state_message


# -- ZQ de-averaging (audit #6) and step path (audit #7) --

def _strip(curve_date: date, averages: dict[date, float]) -> dict[int, float]:
    """Cache layout written by fed_fetcher: days to the 1st of each contract month (current month = 0)."""
    return {max(0, (month - curve_date).days): avg for month, avg in averages.items()}


def test_deaveraging_mid_month_meeting_recovers_post_meeting_rate():
    # Jan clean at 4.50; Feb has a meeting on the 11th moving the rate to 4.25.
    feb_avg = (4.50 * 11 + 4.25 * 17) / 28
    months = rate_probability.zq_monthly_averages(
        _strip(date(2026, 1, 5), {date(2026, 1, 1): 4.50, date(2026, 2, 1): feb_avg, date(2026, 3, 1): 4.25}),
        date(2026, 1, 5),
    )
    path = rate_probability.deaverage_meeting_rates(months, [date(2026, 2, 11)], fallback_start_rate=9.99)
    pre, post = path[date(2026, 2, 11)]
    assert pre == pytest.approx(4.50)
    assert post == pytest.approx(4.25)
    # r_post = (avg x days - r_pre x days_before) / days_after, independent of the next contract
    assert post == pytest.approx((feb_avg * 28 - 4.50 * 11) / 17)


def test_deaveraging_end_of_month_meeting_uses_next_contract():
    # Meeting on 28 Oct: only 3 post-meeting days. A noisy October average would swing
    # the de-averaged rate, so the November contract supplies r_post.
    oct_avg_noisy = (3.875 * 28 + 4.125 * 3) / 31 + 0.004
    months = {date(2026, 9, 1): 3.875, date(2026, 10, 1): oct_avg_noisy, date(2026, 11, 1): 4.125}
    pre, post = rate_probability.deaverage_meeting_rates(months, [date(2026, 10, 28)], 9.99)[date(2026, 10, 28)]
    assert post == pytest.approx(4.125)
    assert pre == pytest.approx(3.875)
    naive = (oct_avg_noisy * 31 - 3.875 * 28) / 3
    assert abs(naive - 4.125) > 0.03  # the in-month formula would have been distorted


def test_deaveraging_backs_out_pre_rate_when_current_month_has_late_meeting():
    # Curve starts in a meeting month: r_pre comes from the curve, not the configured rate.
    oct_avg = (3.88 * 28 + 4.13 * 3) / 31
    months = {date(2026, 10, 1): oct_avg, date(2026, 11, 1): 4.13}
    pre, post = rate_probability.deaverage_meeting_rates(months, [date(2026, 10, 28)], 3.75)[date(2026, 10, 28)]
    assert (pre, post) == (pytest.approx(3.88), pytest.approx(4.13))


def test_step_path_is_piecewise_constant_between_meetings():
    meetings = [(date(2026, 2, 11), 4.25), (date(2026, 3, 18), 4.00)]
    assert rate_probability.step_path_rate(meetings, 4.50, date(2026, 2, 11)) == 4.50   # effective day after
    assert rate_probability.step_path_rate(meetings, 4.50, date(2026, 2, 12)) == 4.25
    assert rate_probability.step_path_rate(meetings, 4.50, date(2026, 3, 1)) == 4.25    # flat, not interpolated
    assert rate_probability.step_path_rate(meetings, 4.50, date(2026, 3, 18)) == 4.25
    assert rate_probability.step_path_rate(meetings, 4.50, date(2026, 6, 1)) == 4.00


class _StripSession:
    def __init__(self, curve_date: date, curve: dict[int, float]):
        self.curve_date, self.curve = curve_date, curve

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "MAX(curve_date)" in sql:
            return _ScalarResult(self.curve_date)
        if "FROM ois_cache" in sql:
            return _RowsResult([SimpleNamespace(tenor_days=t, rate=r) for t, r in sorted(self.curve.items())])
        return _RowsResult([])


@pytest.mark.asyncio
async def test_cumulative_vs_per_meeting(monkeypatch):
    """Third meeting's cumulative delta is measured from the starting rate, not the second meeting."""
    curve_date = date(2026, 1, 5)
    rates = {date(2026, 1, 1): 4.50, date(2026, 2, 1): (4.50 * 11 + 4.25 * 17) / 28,
             date(2026, 3, 1): (4.25 * 18 + 4.00 * 13) / 31, date(2026, 4, 1): 4.00,
             date(2026, 5, 1): (4.00 * 6 + 3.75 * 25) / 31, date(2026, 6, 1): 3.75}
    meetings = [{"bank": "FED", "meeting_dt": datetime(2026, m, d, 19, tzinfo=UTC).isoformat(), "is_official": True}
                for m, d in ((2, 11), (3, 18), (5, 6))]

    async def fake_meetings(bank, n, db_session):
        return meetings[:n]

    monkeypatch.setattr(rate_probability, "get_upcoming_meetings", fake_meetings)
    monkeypatch.setattr(
        rate_probability,
        "_bank_config",
        lambda bank: {"current_rate": 4.75, "step_bps": 25, "rate_basis_adj": 0.0, "low_liquidity_curve": False},
    )

    probabilities = await compute_meeting_probabilities(
        "FED", curve_date=curve_date, n_meetings=3, db_session=_StripSession(curve_date, _strip(curve_date, rates)),
    )

    assert [p.delta_bps for p in probabilities] == [pytest.approx(-25.0, abs=0.01)] * 3
    assert [p.cut_prob for p in probabilities] == [pytest.approx(1.0)] * 3
    # Measured from the curve's pre-meeting 4.50, not the configured 4.75.
    assert probabilities[2].cumulative_delta_bps == pytest.approx(-75.0, abs=0.01)
    assert probabilities[2].implied_rate == pytest.approx(3.75)


@pytest.mark.asyncio
async def test_no_curve_returns_explicit_no_data(monkeypatch):
    meetings = [
        {
            "bank": "FED",
            "meeting_dt": datetime(2026, 1, 31, tzinfo=UTC).isoformat(),
            "is_official": True,
        },
    ]

    async def fake_meetings(bank, n, db_session):
        return meetings[:n]

    monkeypatch.setattr(rate_probability, "get_upcoming_meetings", fake_meetings)
    monkeypatch.setattr(rate_probability, "RATE_PROBABILITY_OVERRIDES_PATH", rate_probability.Path("missing-overrides.yaml"))
    monkeypatch.setattr(
        rate_probability,
        "_bank_config",
        lambda bank: {"current_rate": 4.50, "step_bps": 25, "rate_basis_adj": 0.0, "low_liquidity_curve": False},
    )

    probabilities = await compute_meeting_probabilities(
        "FED",
        n_meetings=1,
        db_session=_NoCurveSession(),
    )

    assert probabilities[0].data_state == "no_curve"
    assert probabilities[0].cut_prob is None
    assert probabilities[0].hold_prob is None
    assert probabilities[0].hike_prob is None
    assert probabilities[0].outcome_distribution is None


@pytest.mark.asyncio
async def test_near_meeting_uses_proximity_snapshot(monkeypatch):
    class FakeDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 5, 11)

    meeting_dt = datetime(2026, 5, 14, tzinfo=UTC)
    meetings = [
        {
            "bank": "FED",
            "meeting_dt": meeting_dt.isoformat(),
            "is_official": True,
        },
    ]

    async def fake_meetings(bank, n, db_session):
        return meetings[:n]

    monkeypatch.setattr(rate_probability, "date", FakeDate)
    monkeypatch.setattr(rate_probability, "get_upcoming_meetings", fake_meetings)
    monkeypatch.setattr(rate_probability, "RATE_PROBABILITY_OVERRIDES_PATH", rate_probability.Path("missing-overrides.yaml"))
    monkeypatch.setattr(
        rate_probability,
        "_bank_config",
        lambda bank: {"current_rate": 4.50, "step_bps": 25, "rate_basis_adj": 0.0, "low_liquidity_curve": False},
    )

    probabilities = await compute_meeting_probabilities(
        "FED",
        n_meetings=1,
        db_session=_SnapshotSession(),
    )

    assert probabilities[0].proximity_lock is True
    assert probabilities[0].delta_bps == pytest.approx(-25.0)
    assert probabilities[0].cut_prob == 1.0
