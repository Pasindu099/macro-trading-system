"""Deterministic projection parsers for BoE, BoC, RBNZ and SNB (Step 12 Part B; no LLM).

Each parser returns cb_projection_values rows. Horizons are years ("2026") or
quarters ("2026Q4"); single-point projections use stat="point".
"""

from __future__ import annotations

import csv
import re
from datetime import date, timedelta
from io import StringIO
from typing import Any

from bs4 import BeautifulSoup

BOUNDS = {"cpi_inflation": (-5, 25), "core_cpi_inflation": (-5, 25), "core_core_cpi_inflation": (-5, 25), "real_gdp": (-25, 25),
          "unemployment_rate": (0, 30), "policy_rate": (-2, 20)}
NUMBER = re.compile(r"^\s*(-?\d+(?:\.\d+)?)")
QUARTER = re.compile(r"^(20\d\d)\s*Q([1-4])$")


class SkipRound(ValueError):
    """The round is published but has no parsable central projection (reported, not loaded)."""


def _row(bank: str, release: date, variable: str, horizon: str, value: float, stat: str = "point") -> dict[str, Any]:
    lower, upper = BOUNDS.get(variable, (-1e6, 1e6))
    if not lower <= value <= upper:
        raise ValueError(f"Implausible {bank} {variable} {horizon} on {release}: {value}")
    return {"bank": bank, "release_date": release, "variable": variable, "horizon": horizon,
            "stat": stat, "value": round(value, 3)}


def _number(cell: str | None) -> float | None:
    """First number in a cell; '0.6 (0.9)' -> 0.6 (bracketed previous-round value ignored)."""
    match = NUMBER.match((cell or "").replace("−", "-").replace("–", "-"))
    return float(match.group(1)) if match else None


def _label(cell: str) -> str:
    return re.sub(r"\(\s*[a-z]\s*\)|\*", "", cell.replace("\xa0", " ")).strip().lower()


def _grid(table) -> list[list[str]]:
    """Table cells as text, colspans expanded (rowspans are not used in these tables)."""
    rows = []
    for tr in table.find_all("tr"):
        cells = []
        for cell in tr.find_all(["th", "td"]):
            cells.extend([cell.get_text(" ", strip=True)] * int(cell.get("colspan", 1) or 1))
        rows.append(cells)
    return rows


def _require(bank: str, release: date, rows: list[dict], needed: set[str]) -> list[dict]:
    missing = needed - {r["variable"] for r in rows}
    if missing:
        raise ValueError(f"Incomplete {bank} round {release}: missing {sorted(missing)}")
    return rows


def parse_boj_highlights(html: str, release: date) -> list[dict[str, Any]]:
    """Read only explicitly labelled forecast values from the BoJ's accessible HTML image text."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for image in soup.find_all("img", alt=True):
        alt = " ".join(image["alt"].split())
        if "Forecasts are" not in alt:
            continue
        if "real gdp" in alt.lower():
            variable = "real_gdp"
        elif "all items less fresh food" in alt and "energy" not in alt:
            variable = "core_cpi_inflation"
        elif "all items less fresh food and energy" in alt:
            variable = "core_core_cpi_inflation"
        else:
            continue
        forecast = alt.split("Forecasts are", 1)[1]
        for value, year in re.findall(r"([+-]?\d+(?:\.\d+)?)%\s+for fiscal\s+(20\d\d)", forecast):
            rows.append(_row("BOJ", release, variable, year, float(value), stat="median"))
    return _require("BOJ", release, rows, {"real_gdp", "core_cpi_inflation"})


BOJ_RANGE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s+to\s+([+-]?\d+(?:\.\d+)?)\s*\[\s*([+-]?\d+(?:\.\d+)?)\s*\]")
BOJ_PLAIN_RANGE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s+to\s+([+-]?\d+(?:\.\d+)?)")


def parse_boj_outlook_pdf(page_text: str, release: date) -> list[dict[str, Any]]:
    """Parse the policy-board median/range table extracted by the installed pypdf package."""
    heading = re.search(r"Forecasts of (?:the Majority of )?(?:the )?Policy Board Members", page_text)
    if not heading:
        raise ValueError("BoJ forecast table missing from PDF text")
    table = page_text[heading.start():]
    table = table[:table.find("Notes:")] if "Notes:" in table else table
    first = re.search(r"Fiscal\s+20\d\d", table)
    if not first:
        raise ValueError("BoJ fiscal-year rows missing")
    header = " ".join(table[:first.start()].lower().split())
    has_core_core = "less fresh food and energy" in header
    variables = ["real_gdp", "core_cpi_inflation"] + (["core_core_cpi_inflation"] if has_core_core else [])
    ranges_only = not BOJ_RANGE.search(table) and "made their forecasts as a range" in page_text
    rows = []
    for match in re.finditer(r"Fiscal\s+(20\d\d)(.*?)(?=Forecasts made|Fiscal\s+20\d\d|Notes:|$)", table, re.S):
        year, content = match.groups()
        if ranges_only:
            found_ranges = BOJ_PLAIN_RANGE.findall(content)
            if len(found_ranges) < 2:
                if int(year) < release.year:
                    continue
                raise ValueError(f"BoJ {year}: incomplete range-only forecast row")
            for variable, (low, high) in zip(variables, found_ranges):
                lo, hi = float(low), float(high)
                if lo > hi:
                    raise ValueError(f"BoJ {year}: inverted forecast range")
                rows.extend((_row("BOJ", release, variable, year, lo, stat="range_low"),
                             _row("BOJ", release, variable, year, hi, stat="range_high")))
            continue
        found = BOJ_RANGE.findall(content)
        if len(found) < len(variables):
            if int(year) < release.year:
                continue
            raise ValueError(f"BoJ {year}: incomplete PDF forecast row")
        for variable, (low, high, median) in zip(variables, found):
            lo, hi, mid = float(low), float(high), float(median)
            if not lo <= mid <= hi:
                raise ValueError(f"BoJ {year}: median outside published range")
            for stat, value in (("range_low", lo), ("range_high", hi), ("median", mid)):
                rows.append(_row("BOJ", release, variable, year, value, stat=stat))
    return _require("BOJ", release, rows, {"real_gdp", "core_cpi_inflation"})


# ── Bank of England: Monetary Policy Report HTML ──────────────────────

BOE_ROWS = {"cpi inflation": "cpi_inflation", "modal cpi inflation": "cpi_inflation", "gdp": "real_gdp",
            "unemployment rate": "unemployment_rate", "lfs unemployment rate": "unemployment_rate",
            "bank rate": "policy_rate"}


def parse_boe_mpr(html: str, release: date) -> list[dict[str, Any]]:
    """Central (market-path conditioned) projection table, plus the annex CPI Q4 row when present."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[tuple[str, str], dict] = {}
    main = next((t for t in soup.find_all("table")
                 if "Bank Rate" in t.get_text() and "CPI inflation" in t.get_text()
                 and re.search(r"20\d\d\s*Q\d", t.get_text())), None)
    if main is None:
        if any("scenario" in t.get_text().lower() and "CPI inflation" in t.get_text() for t in soup.find_all("table")):
            raise SkipRound(f"BOE {release}: scenarios only, no central projection")
        raise SkipRound(f"BOE {release}: no projection table in HTML (PDF only)")
    grid = _grid(main)
    text_ = main.get_text(" ", strip=True).lower()
    if "scenario" in text_ and "central projection" not in text_:
        raise SkipRound(f"BOE {release}: scenarios only, no central projection")
    columns: list[str | None] = []
    in_central = True
    for cells in grid:
        if not cells:
            continue
        quarters = [QUARTER.match(c.strip()) for c in cells[1:]]
        if any(quarters):
            columns = [f"{m.group(1)}Q{m.group(2)}" if m else None for m in quarters]
            continue
        label = _label(cells[0])
        if len({c.strip() for c in cells if c.strip()}) == 1:          # section heading row (may span columns)
            in_central = in_central and ("scenario" not in label or "central" in label)
            continue
        variable = BOE_ROWS.get(label)
        if not in_central or variable is None or not columns:
            continue
        for horizon, cell in zip(columns, cells[1:]):
            value = _number(cell)
            if horizon and value is not None:
                out[(variable, horizon)] = _row("BOE", release, variable, horizon, value)
    for table in soup.find_all("table"):
        grid = _grid(table)
        header = next((r for r in grid if sum(bool(QUARTER.match(c.strip())) for c in r) >= 2), None)
        if header is None or table is main:
            continue
        for cells in grid:
            if cells and _label(cells[0]) == "cpi":
                for col, cell in zip(header[1:], cells[1:]):
                    m, value = QUARTER.match(col.strip()), _number(cell)
                    if m and m.group(2) == "4" and int(m.group(1)) >= release.year and value is not None:
                        out.setdefault(("cpi_inflation", f"{m.group(1)}Q4"),
                                       _row("BOE", release, "cpi_inflation", f"{m.group(1)}Q4", value))
    return _require("BOE", release, list(out.values()),
                    {"cpi_inflation", "real_gdp", "unemployment_rate", "policy_rate"})


# ── Bank of Canada: Monetary Policy Report projections page ───────────

BOC_ANNUAL = {"gdp": "real_gdp", "cpi inflation": "cpi_inflation"}
BOC_QUARTERLY = {"cpi inflation": "cpi_inflation", "core inflation": "core_cpi_inflation", "real gdp": "real_gdp"}


def parse_boc_mpr(html: str, release: date) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    out: dict[tuple[str, str], dict] = {}
    for table in soup.find_all("table"):
        caption = (table.find("caption").get_text(" ", strip=True) if table.find("caption") else "").lower()
        grid = _grid(table)
        if "average annual real gdp growth" in caption:
            years = next((r for r in grid if sum(bool(re.fullmatch(r"20\d\d", c.strip())) for c in r) >= 2), [])
            for cells in grid:
                variable = BOC_ANNUAL.get(_label(cells[0])) if cells else None
                for year, cell in zip(years[1:], cells[1:]):
                    value = _number(cell)
                    if variable and re.fullmatch(r"20\d\d", year.strip()) and value is not None:
                        out[(variable, year.strip())] = _row("BOC", release, variable, year.strip(), value)
        elif "summary of the quarterly projection" in caption:
            years = next((r for r in grid if any(re.fullmatch(r"20\d\d", c.strip()) for c in r)), [])
            quarters = next((r for r in grid if any(re.fullmatch(r"Q[1-4]", c.strip()) for c in r)), [])
            # The year row lacks the label column of the quarter row when both start with an empty cell.
            offset = len(quarters) - len(years)
            year_at = []
            current = None
            for i in range(len(quarters)):
                j = i - offset
                cell = years[j].strip() if 0 <= j < len(years) else ""
                current = cell if re.fullmatch(r"20\d\d", cell) else (current if cell == "" and current else None)
                year_at.append(current)
            for cells in grid:
                if not cells:
                    continue
                label = _label(cells[0])
                variable = next((v for k, v in BOC_QUARTERLY.items()
                                 if label.startswith(k) and "quarter-over-quarter" not in label), None)
                if variable is None:
                    continue
                for i, cell in enumerate(cells[1:], start=1):
                    value = _number(cell)
                    q = quarters[i].strip() if i < len(quarters) else ""
                    if value is not None and q == "Q4" and i < len(year_at) and year_at[i]:
                        horizon = f"{year_at[i]}Q4"
                        out.setdefault((variable, horizon), _row("BOC", release, variable, horizon, value))
    years = [int(r["horizon"]) for r in out.values() if r["horizon"].isdigit()]
    if years and not release.year - 2 <= min(years) <= release.year:   # January reports show two history years
        raise ValueError(f"BOC {release}: page horizons start {min(years)}, not this round")
    return _require("BOC", release, list(out.values()), {"cpi_inflation", "real_gdp"})


# ── RBNZ: Monetary Policy Statement data workbook ─────────────────────

RBNZ_SERIES = {"papc": "cpi_inflation", "gdpapc": "real_gdp", "urate": "unemployment_rate", "ocr": "policy_rate"}


def excel_date(serial: str | float) -> date:
    return date(1899, 12, 30) + timedelta(days=int(float(serial)))


def quarter_of(day: date) -> str:
    return f"{day.year}Q{(day.month - 1) // 3 + 1}"


def parse_rbnz_projections(rows: list[list[str | None]], release: date) -> list[dict[str, Any]]:
    """'Projections' sheet: quarters from the release quarter onwards (earlier rows are history)."""
    ids = next((r for r in rows if r and (r[0] or "").strip() == "Identifier"), None)
    if ids is None:
        raise ValueError(f"RBNZ {release}: no Identifier row")
    index = {code: i for i, code in enumerate(ids) if code in RBNZ_SERIES}
    first = quarter_of(release)
    out = []
    for cells in rows:
        if not cells or not re.fullmatch(r"\d+(\.0+)?", (cells[0] or "").strip()):
            continue
        horizon = quarter_of(excel_date(cells[0]))
        if horizon < first:
            continue
        for code, i in index.items():
            value = _number(cells[i]) if i < len(cells) else None
            if value is not None:
                out.append(_row("RBNZ", release, RBNZ_SERIES[code], horizon, value))
    return _require("RBNZ", release, out, set(RBNZ_SERIES.values()))


# ── SNB: conditional inflation forecast cube (data.snb.ch snbiprogq) ──

SNB_ROUND = re.compile(r"^([MJSD])(\d{4})P")
SNB_MONTH = {"M": 3, "J": 6, "S": 9, "D": 12}
SNB_RATE = re.compile(r"at (-?\d+(?:\.\d+)?)%")


def snb_round_month(code: str) -> tuple[int, int] | None:
    match = SNB_ROUND.match(code)
    return (int(match.group(2)), SNB_MONTH[match.group(1)]) if match else None


def parse_snb_cube(payload: str, labels: dict[str, str], releases: dict[str, date],
                   since: date = date(2020, 1, 1)) -> dict[str, list[dict[str, Any]]]:
    """Forecast ('P') rows per round code; the label's policy-rate assumption is stored as stat='assumption'."""
    lines = payload.lstrip("﻿").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith('"Date"'))
    rounds: dict[str, list[dict]] = {}
    for record in csv.DictReader(StringIO("\n".join(lines[start:])), delimiter=";"):
        code, kind, value = record["D0"], record["D1"], record["Value"]
        ym = snb_round_month(code)
        if kind != "P" or not value or ym is None or date(ym[0], ym[1], 1) < since:
            continue
        release = releases.get(code) or date(ym[0], ym[1], 1)
        horizon = record["Date"].replace("-", "")
        rounds.setdefault(code, []).append(_row("SNB", release, "cpi_inflation", horizon, float(value)))
    for code, rows in rounds.items():
        match = SNB_RATE.search(labels.get(code, ""))
        if match:
            rows.append(_row("SNB", rows[0]["release_date"], "policy_rate", "constant", float(match.group(1)),
                             stat="assumption"))
    return rounds


def parse_snb_labels(dimensions: dict) -> dict[str, str]:
    labels: dict[str, str] = {}

    def walk(node):
        if isinstance(node, dict):
            if "id" in node and "name" in node:
                labels[node["id"]] = node["name"]
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(dimensions)
    return labels
