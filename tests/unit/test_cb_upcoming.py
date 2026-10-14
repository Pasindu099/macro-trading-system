"""Step 12 Part D: speech calendars, minutes and projection dates."""

from datetime import date
from pathlib import Path

from app.services.cb_upcoming import is_projection_meeting, minutes_release, parse_ecb_weekly, parse_fed_calendar

FIX = Path("tests/fixtures/cb_upcoming")
TODAY = date(2026, 10, 14)


def test_fed_calendar_speeches_and_testimony_in_window_only():
    events = parse_fed_calendar((FIX / "fed_calendar.json").read_text(encoding="utf-8"), TODAY)
    assert [(e["date"], e["speaker"], e["kind"]) for e in events] == [
        (date(2026, 10, 15), "Chairman Kevin Warsh", "Discussion"),
        (date(2026, 10, 20), "Governor Christopher J. Waller", "Testimony")]


def test_ecb_weekly_board_member_or_name_in_event_and_window():
    events = parse_ecb_weekly((FIX / "ecb_weekly.html").read_text(encoding="utf-8"), TODAY, days=7)
    assert [(e["speaker"], e["time"], e["venue"]) for e in events] == [
        ("Lagarde", None, None), ("Isabel Schnabel", "15:30 CET", "Frankfurt")]
    assert events[1]["event"] == "Keynote speech at a conference"


def test_minutes_exact_lag_approx_and_boj_summary_dates():
    assert minutes_release("FED", date(2026, 10, 28), {}) == {"name": "minutes", "date": date(2026, 11, 18), "exact": True}
    boj = minutes_release("BOJ", date(2026, 10, 30), {"summary_of_opinions": {"2026-10-30": "2026-11-10"}})
    assert boj["date"] == date(2026, 11, 10) and boj["exact"] is True
    assert minutes_release("BOC", date(2026, 10, 28), {})["exact"] is False


def test_projection_meetings_by_month_or_explicit_list():
    assert is_projection_meeting("FED", date(2026, 12, 9), {}) is True
    assert is_projection_meeting("BOE", date(2026, 12, 17), {}) is False
    rbnz = {"projection_meetings": ["2026-12-09"]}
    assert is_projection_meeting("RBNZ", date(2026, 12, 9), rbnz) and not is_projection_meeting("RBNZ", date(2026, 10, 28), rbnz)
