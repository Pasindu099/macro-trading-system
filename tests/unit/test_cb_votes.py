from datetime import date

import pytest

from app.services.cb_votes import parse_boe, parse_boj, parse_fed, source_urls


def test_boe_named_split_and_dissent():
    html = """Published on 06 November 2025. The Chair invited the Committee to vote on the proposition that:
    Five members (Andrew Bailey, Megan Greene, Clare Lombardelli, Catherine L Mann and Huw Pill)
    voted in favour of the proposition. Four members (Sarah Breeden, Swati Dhingra, Dave Ramsden and Alan Taylor)
    voted against this proposition, preferring to reduce Bank Rate by 0.25 percentage points, to 3.75%."""
    stamp, rows = parse_boe(html, date(2025, 11, 5))
    assert stamp.date() == date(2025, 11, 6)
    assert sum(row["vote"] == "for" for row in rows) == 5
    assert {row["preferred_change_bp"] for row in rows if row["vote"] == "against"} == {-25.0}


def test_boj_policy_action_skips_other_votes():
    html = """The Bank will encourage the uncollateralized overnight call rate to remain at around 0.5 percent.
    Voting for the action: UEDA Kazuo, HIMINO Ryozo, UCHIDA Shinichi, NOGUCHI Asahi,
    NAKAGAWA Junko, KOEDA Junko, and MASU Kazuyuki. Voting against the action:
    TAKATA Hajime and TAMURA Naoki. They proposed that the Bank set the guideline for money market operations
    as follows: the Bank would encourage the uncollateralized overnight call rate to remain at around 0.75 percent."""
    _, rows = parse_boj(html, date(2025, 10, 30))
    assert len(rows) == 9
    assert rows[6]["member"] == "MASU Kazuyuki"
    assert {row["preferred_change_bp"] for row in rows if row["vote"] == "against"} == {25.0}


def test_fed_named_dissents_with_different_preferences():
    html = """Voting for the monetary policy action were Jerome H. Powell, Chair; John C. Williams, Vice Chair;
    Michael S. Barr; Michelle W. Bowman; Susan M. Collins; Lisa D. Cook; Philip N. Jefferson;
    Alberto G. Musalem; and Christopher J. Waller. Voting against this action were Stephen I. Miran,
    who preferred to lower the target range for the federal funds rate by 1/2 percentage point at this meeting;
    and Austan D. Goolsbee and Jeffrey R. Schmid, who preferred no change to the target range.
    For media inquiries"""
    _, rows = parse_fed(html, date(2025, 12, 10))
    assert len(rows) == 12
    assert rows[9]["preferred_change_bp"] == -50.0
    assert rows[10]["preferred_change_bp"] == rows[11]["preferred_change_bp"] == 0.0


def test_missing_named_vote_rejected():
    with pytest.raises(ValueError):
        parse_boj("The Policy Board decided unanimously.", date(2025, 1, 1))
    assert source_urls("BOJ", date(2025, 10, 30))[0].endswith("k251030a.htm")
