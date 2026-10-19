from datetime import UTC, date, datetime

import pytest

from app.services import positioning as p


def _row(ccy, pctl, status, crowding, reducing=False):
    return {"currency": ccy, "leveraged_funds": {"percentile": pctl, "status": status, "crowding": crowding,
                                                  "reducing": reducing}}


def test_summary_picks_squeeze_status_then_extremeness():
    rows = [_row("EUR", 96.0, "trend_confirming", "crowded_long"),
            _row("JPY", 12.0, "high", "crowded_short", reducing=True),
            _row("GBP", 4.0, "medium", "crowded_short", reducing=True),
            _row("USD", 50.0, "none", "neutral")]
    summary = p.summary_sentence(rows)
    assert summary["currency"] == "JPY"
    assert summary["text"] == ("Speculators crowded short JPY (12th percentile) and shorts are being cut: "
                               "squeeze watch high.")


def test_summary_variants_and_no_crowding():
    trend = p.summary_sentence([_row("EUR", 91.0, "trend_confirming", "crowded_long")])
    assert trend["text"].endswith("(91st percentile) and EUR is still moving their way: trend confirming.")
    against = p.summary_sentence([_row("CAD", 13.0, "medium", "crowded_short")])
    assert against["text"].endswith("and CAD is moving against them: squeeze watch medium.")
    none = p.summary_sentence([_row("USD", 50.0, "none", "neutral")])
    assert none["currency"] is None and "No currency is crowded" in none["text"]
    for text in (trend["text"], against["text"], none["text"]):
        assert "expected" not in text and "likely" not in text


def test_ordinals():
    assert [p.ordinal(v) for v in (1, 2, 3, 11, 12, 13, 21, 22, 89.4, 100)] == [
        "1st", "2nd", "3rd", "11th", "12th", "13th", "21st", "22nd", "89th", "100th"]


def test_release_schedule_holiday_shift():
    schedule = {row["release_date"]: row for row in p.release_schedule()}
    juneteenth = schedule[date(2026, 6, 22)]  # Monday release after the Friday holiday
    assert juneteenth["delayed"] and juneteenth["as_of"] == date(2026, 6, 16)
    assert schedule[date(2026, 10, 16)]["as_of"] == date(2026, 10, 13)
    thanksgiving = schedule[date(2026, 11, 30)]
    assert thanksgiving["delayed"] and thanksgiving["as_of"] == date(2026, 11, 24)


def test_release_info_current_and_next():
    info = p.release_info(date(2026, 10, 13), now=datetime(2026, 10, 19, 12, tzinfo=UTC))
    assert (info["as_of"], info["released"], info["released_source"]) == ("2026-10-13", "2026-10-16", "cftc_schedule")
    assert (info["next_release"], info["next_as_of"]) == ("2026-10-23", "2026-10-20")
    # Friday before 15:30 ET: that day's release is still "next"; after it, the following one.
    assert p.release_info(date(2026, 10, 13), now=datetime(2026, 10, 23, 19, 0, tzinfo=UTC))["next_release"] == "2026-10-23"
    assert p.release_info(date(2026, 10, 13), now=datetime(2026, 10, 23, 20, 0, tzinfo=UTC))["next_release"] == "2026-10-30"
    late = p.release_info(date(2026, 11, 24), now=datetime(2026, 11, 26, tzinfo=UTC))
    assert late["next_release"] == "2026-11-30" and late["next_delayed"] is True
    after = p.release_info(date(2026, 12, 22), now=datetime(2027, 1, 2, tzinfo=UTC))
    assert after["next_release"] is None and after["next_release_status"] == "schedule not yet published"


@pytest.mark.parametrize("pctl,change,move,flag", [
    (82.0, -5000, 1.2, "divergence"),    # crowded-ish long, specs selling, currency up
    (20.0, 3000, -0.8, "divergence"),    # short side, specs buying, currency down
    (82.0, 5000, 1.2, "none"),           # agree
    (50.0, -5000, 1.2, "none"),          # disagree but not extreme
    (75.0, -2000, 0.1, "divergence"),    # boundary percentile and boundary size (0.5 x 4000) count
    (82.0, -23, 1.2, "none"),            # disagree and extreme but immaterial change
    (82.0, None, 1.2, "pending"),
])
def test_divergence_rule(pctl, change, move, flag):
    assert p.divergence_flag(pctl, change, move, change_std=4000.0)["flag"] == flag


def test_change_4w_std_uses_lookback_window():
    from datetime import timedelta
    weeks = [p.Week(date(2020, 1, 7) + timedelta(weeks=i), long=(i % 2) * 1000, short=0, open_interest=1)
             for i in range(400)]
    assert p.change_4w_std(weeks, 1) == 0.0  # alternating nets: every 4-week change is zero
    assert p.change_4w_std(weeks[:5], 3) is None


@pytest.mark.parametrize("implied,retail_long,signal", [
    (35.0, 30.0, "opposite sides"),   # COT long-biased, retail 70% short
    (-40.0, 72.0, "opposite sides"),  # COT short-biased, retail 72% long
    (35.0, 65.0, "same side"),
    (-25.0, 35.0, "same side"),
    (35.0, 45.0, "no signal"),        # retail not one-sided
    (10.0, 20.0, "no signal"),        # COT not biased
    (20.0, 30.0, "no signal"),        # +20 is not > +20
    (35.0, None, "no signal"),        # retail missing
])
def test_opposite_sides_rule(implied, retail_long, signal):
    assert p.retail_signal(implied, retail_long) == signal


async def test_institutions_vs_retail_not_configured(monkeypatch):
    from app.services import myfxbook

    async def positions():
        return {}

    async def latest():
        return []

    monkeypatch.setattr(p, "load_positions", positions)
    monkeypatch.setattr(myfxbook, "latest", latest)
    monkeypatch.setattr(myfxbook, "credentials", lambda: None)
    result = await p.get_institutions_vs_retail()
    assert result["retail_state"] == "not_configured"
    assert result["retail_message"] == "Myfxbook credentials not configured"
    assert len(result["pairs"]) == 28 and all(row["signal"] == "no signal" for row in result["pairs"])
