"""Step 12 Part B: projection parsers on saved official fixtures, revisions and reaction flags."""

import csv
import io
import zipfile
from datetime import date
from pathlib import Path

import pytest

from app.processing import cb_projection_parsers as p
from app.processing.xlsx_reader import read_sheet, sheet_names
from app.services.cb_projection_loaders import compute_revisions, rbnz_rounds_from_files

FIX = Path("tests/fixtures/cb_projections")


def _values(rows):
    return {(r["variable"], r["horizon"]): r["value"] for r in rows}


def test_boe_central_projection_not_scenarios_plus_annex_q4_cpi():
    rows = p.parse_boe_mpr((FIX / "boe_mpr_2026_07.html").read_text(encoding="utf-8"), date(2026, 7, 30))
    v = _values(rows)
    assert v[("cpi_inflation", "2026Q3")] == 2.9 and v[("cpi_inflation", "2027Q3")] == 2.6   # not adverse 3.1/4.1
    assert v[("unemployment_rate", "2027Q3")] == 5.3 and v[("policy_rate", "2027Q3")] == 4.2
    assert v[("cpi_inflation", "2026Q4")] == 3.2 and ("cpi_inflation", "2025Q4") not in v    # annex, past quarter dropped
    assert {r["stat"] for r in rows} == {"point"}


def test_boe_older_format_ignores_bracketed_previous_round_and_mean_cpi():
    v = _values(p.parse_boe_mpr((FIX / "boe_mpr_2023_11.html").read_text(encoding="utf-8"), date(2023, 11, 2)))
    assert v[("cpi_inflation", "2024Q4")] == 3.1 and v[("real_gdp", "2023Q4")] == 0.6 and v[("policy_rate", "2025Q4")] == 4.5


def test_boe_scenario_only_round_is_skipped():
    with pytest.raises(p.SkipRound, match="scenarios only"):
        p.parse_boe_mpr((FIX / "boe_mpr_2026_04.html").read_text(encoding="utf-8"), date(2026, 4, 30))


def test_boc_annual_and_q4_horizons():
    v = _values(p.parse_boc_mpr((FIX / "boc_mpr_2026_07.html").read_text(encoding="utf-8"), date(2026, 7, 15)))
    assert v[("real_gdp", "2026")] == 0.7 and v[("cpi_inflation", "2026")] == 2.5
    assert v[("cpi_inflation", "2026Q4")] == 2.4 and v[("core_cpi_inflation", "2027Q4")] == 2.2 and v[("real_gdp", "2026Q4")] == 1.4
    assert ("cpi_inflation", "2026Q2") not in v                      # near-term quarters are not Q4 horizons


def test_boc_rejects_page_from_another_round():
    with pytest.raises(ValueError, match="not this round"):
        p.parse_boc_mpr((FIX / "boc_mpr_2026_07.html").read_text(encoding="utf-8"), date(2021, 1, 20))


def _rbnz_rows():
    with open(FIX / "rbnz_projections_2026_09.csv", encoding="utf-8") as handle:
        return [[c or None for c in row] for row in csv.reader(handle)]


def test_rbnz_own_ocr_track_from_release_quarter():
    rows = p.parse_rbnz_projections(_rbnz_rows(), date(2026, 9, 2))
    v = _values(rows)
    assert min(r["horizon"] for r in rows) == "2026Q3"
    assert v[("policy_rate", "2026Q4")] == 2.81 and v[("cpi_inflation", "2026Q4")] == 3.9


def test_snb_cube_forecast_rows_and_constant_rate_assumption():
    labels = {"S2026POFFP": "September 2026 (Forecast with SNB policy rate at 0.00%)",
              "J2026POFFP": "June 2026 (Forecast with SNB policy rate at 0.00%)"}
    rounds = p.parse_snb_cube((FIX / "snb_snbiprogq.csv").read_text(encoding="utf-8"), labels,
                              {"S2026POFFP": date(2026, 9, 24)})
    sep = _values(rounds["S2026POFFP"])
    assert sep[("cpi_inflation", "2026Q4")] == 1.2 and sep[("policy_rate", "constant")] == 0.0
    assert ("cpi_inflation", "2026Q2") not in sep                    # observed ('BI') quarters excluded
    assert rounds["J2026POFFP"][0]["release_date"] == date(2026, 6, 1)   # no stored decision: first of month


def test_bounds_rejected():
    with pytest.raises(ValueError, match="Implausible"):
        p._row("RBNZ", date(2020, 5, 1), "policy_rate", "2020Q4", -2.31)


def _tiny_xlsx():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   '<sheet name="Projections " sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                   'relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<si><t>Identifier</t></si><si><t>ocr</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/'
                   'main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c></row>'
                   '<row r="3"><c r="A3"><v>46295</v></c><c r="C3"><v>2.5</v></c></row></sheetData></worksheet>')
    return buffer.getvalue()


def test_xlsx_reader_shared_strings_gaps_and_sheet_name_variants():
    content = _tiny_xlsx()
    assert sheet_names(content) == ["Projections "]
    rows = read_sheet(content, "Projections ")
    assert rows[0] == ["Identifier", None, "ocr"] and rows[1] == [] and rows[2] == ["46295", None, "2.5"]


def test_rbnz_files_fall_back_to_first_of_month_and_report_bad_rounds():
    rounds, skipped = rbnz_rounds_from_files({"mpsmar26-data.xlsx": _tiny_xlsx()}, [])
    assert rounds == [] and "missing" in skipped["mpsmar26-data.xlsx"]


def test_revisions_and_reaction_flags():
    before = {("cpi_inflation", "2026Q4", "point"): 2.0, ("policy_rate", "2026Q4", "point"): 3.0}
    now = {("cpi_inflation", "2026Q4", "point"): 2.3, ("policy_rate", "2026Q4", "point"): 3.0}
    result = compute_revisions("RBNZ", now, before, date(2026, 9, 2))
    assert result["reaction_function"]["flag"] == "tolerance" and result["revisions"][0]["change"] == 0.3
    now[("policy_rate", "2026Q4", "point")] = 3.25
    assert compute_revisions("RBNZ", now, before, date(2026, 9, 2))["reaction_function"]["flag"] == "response"
    snb = compute_revisions("SNB", {("cpi_inflation", "2026Q4", "point"): 1.2}, {("cpi_inflation", "2026Q4", "point"): 0.8},
                            date(2026, 9, 24))
    assert snb["reaction_function"]["flag"] == "unavailable"
    boe = compute_revisions("BOE", {("cpi_inflation", "2026Q4", "point"): 3.2, ("policy_rate", "2026Q3", "point"): 3.8},
                            {("cpi_inflation", "2026Q4", "point"): 2.0, ("policy_rate", "2026Q1", "point"): 3.7},
                            date(2026, 7, 30))
    assert boe["reaction_function"]["flag"] == "unavailable"
