"""Step 5 Part B: TFF parsing, report-date expectations and idempotent upserts."""

from __future__ import annotations

from datetime import date

from sqlalchemy.dialects import postgresql

from app.services import cot_positions as cot

CODES = {"099741": "EUR", "098662": "USD"}


def _row(code: str, yymmdd: str, **overrides) -> dict[str, str]:
    row = {
        "Market_and_Exchange_Names": "EURO FX - CHICAGO MERCANTILE EXCHANGE",
        "As_of_Date_In_Form_YYMMDD": yymmdd,
        "Report_Date_as_YYYY-MM-DD": "ignored",
        "CFTC_Contract_Market_Code": f"{code} ",
        "Open_Interest_All": "700,000",
        "Dealer_Positions_Long_All": "10", "Dealer_Positions_Short_All": "20", "Dealer_Positions_Spread_All": "1",
        "Asset_Mgr_Positions_Long_All": "30", "Asset_Mgr_Positions_Short_All": "5", "Asset_Mgr_Positions_Spread_All": "2",
        "Lev_Money_Positions_Long_All": "40", "Lev_Money_Positions_Short_All": "60", "Lev_Money_Positions_Spread_All": "3",
        "Other_Rept_Positions_Long_All": "7", "Other_Rept_Positions_Short_All": "8", "Other_Rept_Positions_Spread_All": "4",
        "NonRept_Positions_Long_All": "9", "NonRept_Positions_Short_All": "11",
    }
    row.update(overrides)
    return row


def test_parse_uses_contract_code_and_tuesday_position_date() -> None:
    rows = cot.parse_tff_rows(
        [_row("099741", "260929"), _row("999999", "260929"), _row("098662", "100105")],
        CODES, start=date(2010, 1, 1),
    )
    assert {r["currency"] for r in rows} == {"EUR", "USD"}
    eur = {r["category"]: r for r in rows if r["currency"] == "EUR"}
    assert set(eur) == set(cot.CATEGORY_COLUMNS)
    assert eur["leveraged_funds"]["report_date"] == date(2026, 9, 29)
    assert (eur["leveraged_funds"]["long"], eur["leveraged_funds"]["short"]) == (40, 60)
    assert eur["nonreportable"]["spreading"] is None
    assert eur["dealer"]["open_interest"] == 700_000


def test_parse_handles_older_header_case_and_start_filter() -> None:
    old = {k.upper() if k.startswith("Dealer") else k: v for k, v in _row("099741", "091229").items()}
    assert cot.parse_tff_rows([old], CODES, start=date(2010, 1, 1)) == []
    parsed = cot.parse_tff_rows([old], CODES)
    assert next(r for r in parsed if r["category"] == "dealer")["long"] == 10


def test_expected_report_date_friday_release_and_monday_retry() -> None:
    assert cot.expected_report_date(date(2026, 10, 2)) == date(2026, 9, 29)   # Friday → same-week Tuesday
    assert cot.expected_report_date(date(2026, 10, 5)) == date(2026, 9, 29)   # Monday retry → previous Tuesday
    # Holiday week: Tuesday 2025-12-23 report released Monday 2025-12-29.
    assert cot.expected_report_date(date(2025, 12, 29)) == date(2025, 12, 23)


def test_upsert_skips_unchanged_rows() -> None:
    rows = cot.parse_tff_rows([_row("099741", "260929")], CODES)
    sql = str(cot.upsert_statement(rows).compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (report_date, contract_code, category) DO UPDATE" in sql
    assert "IS DISTINCT FROM" in sql
