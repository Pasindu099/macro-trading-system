from datetime import date

from app.services.currency_specific import (SIGHT_LABEL, correlation, parse_sight_deposits,
                                            shock_type)


def test_cad_shock_classification():
    assert shock_type(1, 2, 0.1, -1) == "demand-driven"
    assert shock_type(1, -2, 0.1, -1) == "supply-driven"
    assert shock_type(1, 2, 0.1, 1) == "supply-driven"
    assert shock_type(1, 2, -0.1, -1) == "unclassified"
    assert shock_type(None, 2, 0.1, -1) == "unclassified"


def test_snb_weekly_release_and_caveat():
    payload = ('\ufeff"CubeId";"snbgwdchfsgw"\n"PublishingDate";"2026-10-19 10:00"\n'
               '\n"Date";"D0";"Value"\n"2026-10-09";"GI";"432594"\n'
               '"2026-10-16";"GI";"435355"\n"2026-10-17";"GI";\n')
    result = parse_sight_deposits(payload, date(2026, 10, 20))
    assert result["value_chf_millions"] == 435355
    assert result["date"] == date(2026, 10, 16)
    assert result["label"] == SIGHT_LABEL
    assert "not confirmed intervention" in result["label"]
    assert parse_sight_deposits(payload, date(2026, 10, 18))["state"] == "unavailable"


def test_correlation_requires_paired_history():
    days = [date(2026, 10, n) for n in range(1, 16)]
    left = {d: n * n for n, d in enumerate(days)}
    right = {d: -2 * n * n for n, d in enumerate(days)}
    assert correlation(left, right) == -1
    assert correlation(left, {days[0]: 0}) is None
