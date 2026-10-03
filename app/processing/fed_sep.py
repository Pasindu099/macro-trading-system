"""Deterministic parser for the Fed's Summary of Economic Projections (accessible HTML).

No LLM. Tables are located by content, not element ids, because the page layout
changed over the years (2020 pages have no aria labels and no uncertainty/risk
tables or Table 2 — those exist only in the PDF and are skipped).

Parsed parts:
* Table 1 → medians, central tendencies and ranges per variable and horizon
  (the "<previous month> projection" sub-rows are the prior round and are skipped)
* Figure 2 → dot plot (participants per rate per horizon)
* Figure 4 participant tables → uncertainty and risk balance per variable
* Table 2 → average historical projection errors (RMSE) per horizon
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser

VARIABLES = {
    "change in real gdp": "real_gdp",
    "unemployment rate": "unemployment_rate",
    "pce inflation": "pce_inflation",
    "core pce inflation": "core_pce_inflation",
    "federal funds rate": "federal_funds_rate",
}
ERROR_VARIABLES = {
    "change in real gdp": "real_gdp",
    "unemployment rate": "unemployment_rate",
    "total consumer prices": "pce_inflation",
    "short-term interest rates": "federal_funds_rate",
}
# Plausible bounds per variable (percent); a value outside rejects the round.
BOUNDS = {
    "real_gdp": (-15.0, 15.0),
    "unemployment_rate": (2.0, 20.0),
    "pce_inflation": (-3.0, 12.0),
    "core_pce_inflation": (-3.0, 12.0),
    "federal_funds_rate": (-0.5, 12.0),
}
STATS = ("median", "ct_low", "ct_high", "range_low", "range_high")


@dataclass
class SepRound:
    release_date: date
    values: dict[tuple[str, str, str], float] = field(default_factory=dict)   # (variable, horizon, stat) → value
    dots: dict[tuple[str, float], int] = field(default_factory=dict)          # (horizon, rate) → participants
    risk: dict[tuple[str, str], tuple[int, int, int]] = field(default_factory=dict)  # (variable, kind) → counts
    errors: dict[tuple[str, str], float] = field(default_factory=dict)        # (variable, horizon) → rmse
    skipped: list[str] = field(default_factory=list)                          # parts not in the HTML
    declared_missing: dict[str, int] = field(default_factory=dict)            # horizon → participants the Fed says did not submit

    @property
    def horizons(self) -> list[str]:
        return sorted({h for _, h, _ in self.values}, key=lambda h: (h == "longer_run", h))


class SepValidationError(ValueError):
    pass


# ── HTML → tables ─────────────────────────────────────────────────────

class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[dict] = []
        self._table = self._row = None
        self._cell: str | None = None
        self._heading: str | None = None
        self.last_heading = ""
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("h3", "h4", "h5", "h6"):
            self._heading = ""
        elif tag == "table":
            self._table = {"heading": self.last_heading, "rows": []}
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = ""

    def handle_endtag(self, tag):
        if tag in ("h3", "h4", "h5", "h6") and self._heading is not None:
            self.last_heading = _clean(self._heading)
            self._heading = None
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append(_clean(self._cell))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._table["rows"].append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        self.text.append(data)
        if self._cell is not None:
            self._cell += data
        if self._heading is not None:
            self._heading += data


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def decode_html(raw: bytes) -> str:
    """The pages declare UTF-8 but some contain Windows-1252 bytes (en dash, ±)."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _label(text: str) -> str:
    """Row label without footnote markers: 'Core PCE inflation 4' → 'core pce inflation'."""
    return re.sub(r"[\d\s]+$", "", text).strip().lower()


_NUM = r"[-−]?\d+(?:\.\d+)?"
_RANGE = re.compile(rf"^\s*({_NUM})\s*(?:[-–—]|to)\s*({_NUM})\s*$")


def parse_number(text: str) -> float:
    return float(text.replace("−", "-").replace("±", "").replace("+", "").strip())


def parse_range(text: str) -> tuple[float, float]:
    """'2.2–2.5', '-7.6--5.5', '0.1' (single value) → (low, high)."""
    text = text.replace("−", "-").strip()
    match = _RANGE.match(text)
    if match:
        return parse_number(match.group(1)), parse_number(match.group(2))
    value = parse_number(text)
    return value, value


def _horizon(header: str) -> str:
    header = header.strip().lower()
    return "longer_run" if header.startswith("longer") else header[:4]


# ── Parsers per part ──────────────────────────────────────────────────

def _parse_table1(table: dict, sep: SepRound) -> None:
    rows = table["rows"]

    def is_horizon(cell: str) -> bool:
        return bool(re.fullmatch(r"\d{4}", cell)) or cell.lower().startswith("longer")

    # Header repeats the horizons for median, central tendency and range: 4 per block in
    # March/June (3 years + longer run), 5 in September/December (current year + 3 + longer run).
    years_row = next(r for r in rows if sum(is_horizon(c) for c in r) >= 6)
    all_horizons = [_horizon(c) for c in years_row if is_horizon(c)]
    n = len(all_horizons) // 3
    horizons = all_horizons[:n]
    for row in rows:
        if len(row) < 1 + 3 * n:
            continue
        variable = VARIABLES.get(_label(row[0]))
        if variable is None:
            continue  # includes the "<month> projection" rows of the previous round
        cells = row[1:1 + 3 * n]
        for i, horizon in enumerate(horizons):
            median, ct, rng = cells[i], cells[n + i], cells[2 * n + i]
            if not median:
                continue  # e.g. core PCE has no longer-run projection
            sep.values[(variable, horizon, "median")] = parse_number(median)
            sep.values[(variable, horizon, "ct_low")], sep.values[(variable, horizon, "ct_high")] = parse_range(ct)
            sep.values[(variable, horizon, "range_low")], sep.values[(variable, horizon, "range_high")] = parse_range(rng)


def _parse_dots(table: dict, sep: SepRound) -> None:
    header, *rows = table["rows"]
    horizons = [_horizon(c) for c in header[1:]]
    for row in rows:
        if not row or not re.fullmatch(_NUM, row[0]):
            continue
        rate = parse_number(row[0])
        for horizon, cell in zip(horizons, row[1:]):
            if cell:
                sep.dots[(horizon, rate)] = int(cell)


def _risk_variable(heading: str) -> str | None:
    text = heading.lower()
    if "core pce" in text:
        return "core_pce_inflation"
    if "pce" in text:
        return "pce_inflation"
    if "gdp" in text:
        return "real_gdp"
    if "unemployment" in text:
        return "unemployment_rate"
    return None


def _parse_risk(table: dict, sep: SepRound) -> None:
    header = " ".join(table["rows"][0]).lower()
    kind = "uncertainty" if "broadly similar" in header else "risk"
    variable = _risk_variable(table["heading"])
    current = table["rows"][1]  # first data row is this round; the next is the previous round
    if variable:
        sep.risk[(variable, kind)] = tuple(int(c) for c in current[1:4])


def _parse_errors(table: dict, sep: SepRound) -> None:
    header, *rows = table["rows"]
    horizons = [_horizon(c) for c in header[1:]]
    for row in rows:
        variable = ERROR_VARIABLES.get(_label(row[0]))
        if variable is None:
            continue
        for horizon, cell in zip(horizons, row[1:]):
            if cell:
                sep.errors[(variable, horizon)] = abs(parse_number(cell))


_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_DID_NOT_SUBMIT = re.compile(
    r"(one|two|three|four|five|six|\d+)(?: of these \d+)? participants? did not submit "
    r"(?:projections for ((?:\d{4}|the longer run)(?: and (?:\d{4}|the longer run))*)|longer[- ]run projections)", re.I)


def declared_missing(page_text: str, release_date: date) -> dict[str, int]:
    """Footnotes like 'one of these 18 participants did not submit projections for 2028 and 2029'.

    Only the sentence about this round's meeting counts; pages repeat earlier rounds' notes.
    """
    marker = f"{release_date:%B}"
    out: dict[str, int] = {}
    for sentence in re.split(r"(?<=\.)\s+", _clean(page_text)):
        if marker not in sentence or str(release_date.year) not in sentence:
            continue
        for count, horizons in _DID_NOT_SUBMIT.findall(sentence):
            n = _WORDS.get(count.lower()) or int(count)
            keys = re.findall(r"\d{4}|longer run", horizons) if horizons else ["longer run"]
            for key in keys:
                key = "longer_run" if key == "longer run" else key
                out[key] = max(out.get(key, 0), n)
    return out


def parse_sep(html: str, release_date: date) -> SepRound:
    parser = _Tables()
    parser.feed(html)
    sep = SepRound(release_date=release_date, declared_missing=declared_missing(" ".join(parser.text), release_date))
    table1 = dots = errors = None
    for table in parser.tables:
        rows = table["rows"]
        if not rows:
            continue
        first = rows[0][0].lower() if rows[0] else ""
        labels = {_label(r[0]) for r in rows if r}
        if table1 is None and {"change in real gdp", "federal funds rate"} <= labels:
            table1 = table
        elif dots is None and first.startswith("midpoint of target range"):
            dots = table
        elif {"lower", "higher"} <= {c.lower() for c in rows[0]} or "weighted to downside" in " ".join(rows[0]).lower():
            _parse_risk(table, sep)
        elif errors is None and "total consumer prices" in labels:
            errors = table
    if table1 is None:
        raise SepValidationError("Table 1 not found")
    _parse_table1(table1, sep)
    if dots is not None:
        _parse_dots(dots, sep)
    else:
        sep.skipped.append("dot plot")
    if errors is not None:
        _parse_errors(errors, sep)
    else:
        sep.skipped.append("Table 2 (projection errors): PDF only")
    if not sep.risk:
        sep.skipped.append("uncertainty and risk balance: PDF only")
    return sep


# ── Validation ────────────────────────────────────────────────────────

def participant_count(sep: SepRound) -> int | None:
    """Participants from the uncertainty tables (each answers once), else the first dot-plot year."""
    for counts in sep.risk.values():
        return sum(counts)
    years = sorted(h for h, _ in sep.dots if h != "longer_run")
    if years:
        return sum(n for (h, _), n in sep.dots.items() if h == years[0])
    return None


def validate(sep: SepRound) -> list[str]:
    problems: list[str] = []
    for variable in VARIABLES.values():
        for horizon in sep.horizons:
            vals = {stat: sep.values.get((variable, horizon, stat)) for stat in STATS}
            if vals["median"] is None:
                continue
            if any(v is None for v in vals.values()):
                problems.append(f"{variable} {horizon}: incomplete stats")
                continue
            lo, hi = BOUNDS[variable]
            if not all(lo <= v <= hi for v in vals.values()):
                problems.append(f"{variable} {horizon}: value outside plausible bounds {lo}..{hi}")
            if not vals["range_low"] <= vals["median"] <= vals["range_high"]:
                problems.append(f"{variable} {horizon}: median {vals['median']} outside range")
            if not (vals["range_low"] <= vals["ct_low"] <= vals["ct_high"] <= vals["range_high"]):
                problems.append(f"{variable} {horizon}: central tendency outside range")
    participants = participant_count(sep)
    for horizon in sorted({h for h, _ in sep.dots}):
        total = sum(n for (h, _), n in sep.dots.items() if h == horizon)
        # Every participant gives a dot for each year unless the Fed declares otherwise; some omit the longer run.
        expected = None if participants is None else participants - sep.declared_missing.get(horizon, 0)
        if horizon != "longer_run" and expected is not None and total != expected:
            problems.append(f"dots {horizon}: {total} dots, expected {expected} ({participants} participants)")
        if horizon == "longer_run" and participants is not None and total > participants:
            problems.append(f"dots longer_run: {total} dots for {participants} participants")
    return problems
