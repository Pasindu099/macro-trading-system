"""Step 12 Part A: blackout windows and summary helpers."""

from datetime import date

from app.services.cb_summary import blackout_status, blackout_window, priced_move, summarize_decisions


def test_fed_blackout_second_saturday_before_first_day_through_day_after():
    window = blackout_window("FED", date(2026, 10, 28))           # meeting Tue 27 - Wed 28 Oct
    assert window["start"] == date(2026, 10, 17) and window["end"] == date(2026, 10, 29)
    assert window["verified"] is True


def test_ecb_and_boe_calendar_days_before_decision():
    assert blackout_window("ECB", date(2026, 10, 29))["start"] == date(2026, 10, 22)
    boe = blackout_window("BOE", date(2026, 11, 5))
    assert boe["start"] == date(2026, 10, 28) and boe["approx"] is True


def test_boc_longer_blackout_in_mpr_months():
    assert blackout_window("BOC", date(2026, 10, 28))["start"] == date(2026, 10, 20)   # MPR: Tue 8 days prior
    assert blackout_window("BOC", date(2026, 12, 9))["start"] == date(2026, 12, 2)     # Wed 7 days prior


def test_rba_wednesday_before_and_boj_business_days_unverified():
    assert blackout_window("RBA", date(2026, 11, 3))["start"] == date(2026, 10, 28)    # meeting Mon 2 - Tue 3 Nov
    boj = blackout_window("BOJ", date(2026, 10, 30))                                  # meeting Thu 29 - Fri 30
    assert boj["start"] == date(2026, 10, 27) and boj["verified"] is False


def test_blackout_status_states():
    assert blackout_status("FED", [date(2026, 10, 28)], date(2026, 10, 14))["status"] == "open"
    assert blackout_status("FED", [date(2026, 10, 28)], date(2026, 10, 29))["status"] == "in_blackout"
    assert blackout_status("FED", [date(2026, 9, 16), date(2026, 10, 28)], date(2026, 10, 20))["decision"] == date(2026, 10, 28)
    assert blackout_status("SNB", [date(2026, 12, 10)], date(2026, 12, 9))["status"] == "unknown"


def test_summarize_decisions_last_move_and_stale_value():
    rows = [(date(2026, 6, 1), 3.75), (date(2026, 7, 1), 3.75), (date(2026, 9, 1), 4.0)]
    result = summarize_decisions(rows)
    assert result["status"] == "available" and result["last_decision"]["change_bp"] == 25
    stale = summarize_decisions(rows + [(date(2026, 10, 1), None)])
    assert stale["status"] == "stale" and stale["latest_decision"] == date(2026, 10, 1)
    assert summarize_decisions([])["status"] == "unavailable"


def test_priced_move_hidden_when_large_or_stale():
    today = date(2026, 10, 14)
    assert priced_move(12.0, today, today, True, "m")["status"] == "approximate"
    assert priced_move(-8.0, today, today, False, "m")["status"] == "available"
    assert priced_move(60.0, today, today, True, "m")["status"] == "hidden"
    assert priced_move(10.0, date(2026, 10, 10), today, True, "m")["status"] == "hidden"
    assert priced_move(None, None, today, True, "m")["status"] == "unavailable"
