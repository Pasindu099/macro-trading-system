"""Step 8 Part D: regime classification, Sahm rule, gap date alignment."""

from datetime import date

import pytest

from app.services import fed_regime as fr

TODAY = date(2026, 10, 3)


@pytest.mark.parametrize(("decisions", "regime"), [
    ([(date(2026, 1, 28), 3.75), (date(2026, 9, 16), 4.00)], "hiking"),        # up 2.5 months ago
    ([(date(2026, 1, 28), 4.25), (date(2026, 7, 29), 4.00)], "cutting"),       # down within 6 months
    ([(date(2025, 6, 18), 3.75), (date(2025, 9, 17), 4.00), (date(2026, 7, 29), 4.00)], "holding"),  # last move > 6 months ago
    ([(date(2020, 3, 15), 0.25), (date(2026, 7, 29), 0.25)], "near_zero"),
])
def test_regime_classification(decisions, regime):
    assert fr.classify_regime(decisions, TODAY)["regime"] == regime


def test_regime_reports_last_move_and_qe_unavailable():
    out = fr.classify_regime([(date(2026, 7, 29), 3.75), (date(2026, 9, 16), 4.00)], TODAY)
    assert (out["last_move_date"], out["last_move_bp"]) == (date(2026, 9, 16), 25)
    assert out["qe"]["status"] == "unavailable"
    assert fr.classify_regime([], TODAY)["regime"] == "unavailable"


def _monthly(values):
    return [(date(2025 + (i // 12), i % 12 + 1, 1), v) for i, v in enumerate(values)]


def test_sahm_rule_triggers_at_half_point_rise():
    flat = _monthly([4.0] * 12 + [4.2, 4.5, 4.8])   # 3m avg 4.5 vs prior low 4.0
    rising = fr.sahm_rule(flat)
    assert rising["triggered"] is True and rising["value"] == pytest.approx(0.5)
    calm = fr.sahm_rule(_monthly([4.0] * 12 + [4.1, 4.1, 4.2]))
    assert calm["triggered"] is False
    assert fr.sahm_rule(_monthly([4.0] * 10)) is None


def test_gap_uses_identical_year_end_dates_for_sep_and_market():
    meetings = [(date(2026, 10, 28), 4.13), (date(2026, 12, 9), 4.38), (date(2027, 1, 27), 4.50)]
    rows = fr.compute_gap({"2026": 4.10, "2027": 4.40, "longer_run": 3.0}, meetings, 3.88, uncovered_meetings=[])
    r2026 = rows[0]
    assert r2026["date"] == date(2026, 12, 31)
    assert r2026["market"] == fr.market_rate_at(meetings, 3.88, date(2026, 12, 31), [])
    assert r2026["market"] == 4.38 and r2026["gap_bp"] == -28
    assert [r["horizon"] for r in rows] == ["2026", "2027"]  # longer run has no date


def test_gap_unavailable_beyond_futures_strip():
    rows = fr.compute_gap({"2027": 4.0}, [(date(2026, 12, 9), 4.25)], 4.0, uncovered_meetings=[date(2027, 12, 8)])
    assert rows[0]["market"] is None and rows[0]["gap_bp"] is None and "strip" in rows[0]["reason"]
