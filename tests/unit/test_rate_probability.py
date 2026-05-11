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


@pytest.mark.asyncio
async def test_cumulative_vs_per_meeting(monkeypatch):
    """Third meeting cumulative_delta is vs today, not vs second meeting."""
    curve_date = date(2026, 1, 1)
    meetings = [
        {
            "bank": "FED",
            "meeting_dt": datetime(2026, 1, 31, tzinfo=UTC).isoformat(),
            "is_official": True,
        },
        {
            "bank": "FED",
            "meeting_dt": datetime(2026, 3, 2, tzinfo=UTC).isoformat(),
            "is_official": True,
        },
        {
            "bank": "FED",
            "meeting_dt": datetime(2026, 4, 1, tzinfo=UTC).isoformat(),
            "is_official": True,
        },
    ]

    async def fake_meetings(bank, n, db_session):
        return meetings[:n]

    monkeypatch.setattr(rate_probability, "get_upcoming_meetings", fake_meetings)
    monkeypatch.setattr(
        rate_probability,
        "_bank_config",
        lambda bank: {"current_rate": 4.50, "step_bps": 25, "rate_basis_adj": 0.0, "low_liquidity_curve": False},
    )

    probabilities = await compute_meeting_probabilities(
        "FED",
        curve_date=curve_date,
        n_meetings=3,
        db_session=_FakeSession(),
    )

    assert probabilities[0].delta_bps == pytest.approx(-73.3333, abs=0.01)
    assert probabilities[2].delta_bps == pytest.approx(-23.3333, abs=0.01)
    assert probabilities[2].cumulative_delta_bps == pytest.approx(-75.0, abs=0.01)


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
