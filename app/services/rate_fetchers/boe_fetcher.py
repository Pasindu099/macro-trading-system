"""Bank of England SONIA OIS curve fetcher."""

from __future__ import annotations

import csv
import logging
import re
import zipfile
from datetime import date, timedelta
from io import BytesIO, StringIO
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rate_fetchers.cache import load_cached_curve, upsert_ois_curve

logger = logging.getLogger(__name__)
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
    ),
    "Accept": "text/csv,*/*",
}

BOE_SONIA_URL = (
    "https://www.bankofengland.co.uk/boeapps/database/fromshowcolumns.asp"
    "?Travel=NIxAZxSUx&FromSeries=1&ToSeries=50&DAT=RNG&FD=1&FM=Jan&FY=2024"
    "&TD=31&TM=Dec&TY=2027&VFD=Y&html.x=66&html.y=26"
    "&C=BLC&C=BLD&C=BLE&C=BLF&C=BLG&C=BLH&C=BLI&C=BLJ&Filter=N"
)
BOE_YIELD_CURVE_PAGE = "https://www.bankofengland.co.uk/statistics/yield-curves"
BOE_LATEST_YIELD_CURVE_ZIP = (
    "https://www.bankofengland.co.uk/-/media/boe/files/statistics/yield-curves/"
    "latest-yield-curve-data.zip"
)
SERIES_TENORS = {
    "BLC": 30,
    "BLD": 90,
    "BLE": 180,
    "BLF": 365,
    "BLG": 730,
}
OIS_XLSX_NAME = "OIS daily data current month.xlsx"
XLSX_NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
BOE_OIS_TENORS = (30, 90, 180, 365, 730)


async def fetch_sonia_ois_curve(
    db_session: AsyncSession,
    as_of_date: date | None = None,
) -> dict[int, float]:
    """Return {tenor_days: rate_pct} SONIA OIS forward rates."""
    target_date = as_of_date or date.today()
    curve = await fetch_boe_ois_zip_curve(db_session, as_of_date=as_of_date)
    if curve:
        return curve

    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            response = await client.get(BOE_SONIA_URL, headers=REQUEST_HEADERS)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("BoE SONIA fetch failed: %s", exc)
        return await load_cached_curve(db_session, bank="BOE", source="boe_sonia")

    curve = _parse_boe_csv(response.text, target_date)
    if not curve:
        logger.warning("BoE SONIA response contained no usable curve points.")
        return await load_cached_curve(db_session, bank="BOE", source="boe_sonia")

    await upsert_ois_curve(
        db_session,
        bank="BOE",
        curve_date=target_date,
        values=curve,
        source="boe_sonia",
    )
    return curve


async def fetch_boe_ois_zip_curve(
    db_session: AsyncSession,
    as_of_date: date | None = None,
) -> dict[int, float]:
    """Fetch the official BoE latest yield-curve ZIP and parse SONIA OIS forwards."""
    target_date = as_of_date or date.today()
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(
                BOE_LATEST_YIELD_CURVE_ZIP,
                headers={**REQUEST_HEADERS, "Referer": BOE_YIELD_CURVE_PAGE},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("BoE OIS yield-curve ZIP fetch failed: %s", exc)
        return await load_cached_curve(db_session, bank="BOE", source="boe_sonia")

    try:
        with zipfile.ZipFile(BytesIO(response.content)) as outer_zip:
            workbook_bytes = outer_zip.read(OIS_XLSX_NAME)
        curve_date, curve = _parse_boe_ois_workbook(workbook_bytes, target_date)
    except (KeyError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
        logger.warning("BoE OIS yield-curve workbook parse failed: %s", exc)
        return await load_cached_curve(db_session, bank="BOE", source="boe_sonia")

    if not curve or curve_date is None:
        logger.warning("BoE OIS yield-curve workbook contained no usable curve points.")
        return await load_cached_curve(db_session, bank="BOE", source="boe_sonia")

    await upsert_ois_curve(
        db_session,
        bank="BOE",
        curve_date=curve_date,
        values=curve,
        source="boe_sonia",
    )
    return curve


def _parse_boe_csv(raw_csv: str, as_of_date: date) -> dict[int, float]:
    rows = [
        row
        for row in csv.DictReader(StringIO(raw_csv))
        if any(str(value or "").strip() for value in row.values())
    ]
    dated_rows = [
        (row_date, row)
        for row in rows
        if (row_date := _row_date(row)) is not None and row_date <= as_of_date
    ]
    if not dated_rows:
        return {}
    _, latest = max(dated_rows, key=lambda item: item[0])
    curve: dict[int, float] = {}
    for series_code, tenor_days in SERIES_TENORS.items():
        value = _series_value(latest, series_code)
        if value is not None:
            curve[tenor_days] = value
    return curve


def _row_date(row: dict[str, str]) -> date | None:
    for key, value in row.items():
        if "date" not in str(key or "").lower():
            continue
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            continue
    return None


def _series_value(row: dict[str, str], series_code: str) -> float | None:
    for key, value in row.items():
        if series_code.lower() not in str(key or "").lower() or value in (None, ""):
            continue
        try:
            return float(str(value).replace(",", ""))
        except ValueError:
            continue
    return None


def _parse_boe_ois_workbook(workbook_bytes: bytes, as_of_date: date) -> tuple[date | None, dict[int, float]]:
    with zipfile.ZipFile(BytesIO(workbook_bytes)) as workbook:
        shared_strings = _xlsx_shared_strings(workbook)
        short_rows = _xlsx_rows(workbook, "xl/worksheets/sheet2.xml", shared_strings)
        curve_rows = _xlsx_rows(workbook, "xl/worksheets/sheet3.xml", shared_strings)

    short_headers = _numeric_headers(short_rows, header_label="months:")
    curve_headers = _numeric_headers(curve_rows, header_label="years:")

    dated_short_rows = _dated_xlsx_rows(short_rows, as_of_date)
    dated_curve_rows = _dated_xlsx_rows(curve_rows, as_of_date)
    if not dated_short_rows and not dated_curve_rows:
        return None, {}

    latest_date = max(
        [row_date for row_date, _ in dated_short_rows + dated_curve_rows],
        default=None,
    )
    short_values = _latest_values_for_date(dated_short_rows, latest_date)
    curve_values = _latest_values_for_date(dated_curve_rows, latest_date)

    curve: dict[int, float] = {}
    curve.update(_points_from_headers(short_headers, short_values, unit="months"))
    curve.update(_points_from_headers(curve_headers, curve_values, unit="years"))

    return latest_date, {
        tenor: value
        for tenor, value in curve.items()
        if tenor in BOE_OIS_TENORS and value is not None
    }


def _xlsx_shared_strings(workbook: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    strings = []
    for item in root.findall("a:si", XLSX_NS):
        strings.append("".join(text.text or "" for text in item.findall(".//a:t", XLSX_NS)))
    return strings


def _xlsx_rows(
    workbook: zipfile.ZipFile,
    sheet_path: str,
    shared_strings: list[str],
) -> list[list[str]]:
    root = ET.fromstring(workbook.read(sheet_path))
    rows: list[list[str]] = []
    for row in root.findall(".//a:row", XLSX_NS):
        values: dict[int, str] = {}
        for cell in row.findall("a:c", XLSX_NS):
            ref = cell.attrib.get("r", "")
            col_index = _xlsx_col_index(ref)
            value_node = cell.find("a:v", XLSX_NS)
            raw = value_node.text if value_node is not None else ""
            if cell.attrib.get("t") == "s" and raw:
                raw = shared_strings[int(raw)]
            values[col_index] = raw or ""
        if values:
            max_col = max(values)
            rows.append([values.get(col, "") for col in range(max_col + 1)])
    return rows


def _xlsx_col_index(cell_ref: str) -> int:
    letters_match = re.match(r"([A-Z]+)", cell_ref)
    if not letters_match:
        return 0
    index = 0
    for char in letters_match.group(1):
        index = (index * 26) + (ord(char) - ord("A") + 1)
    return index - 1


def _numeric_headers(rows: list[list[str]], *, header_label: str) -> dict[int, float]:
    for row in rows:
        if row and str(row[0]).strip().lower() == header_label:
            headers: dict[int, float] = {}
            for index, value in enumerate(row[1:], start=1):
                parsed = _float_or_none(value)
                if parsed is not None:
                    headers[index] = parsed
            return headers
    return {}


def _dated_xlsx_rows(rows: list[list[str]], as_of_date: date) -> list[tuple[date, list[str]]]:
    dated_rows = []
    for row in rows:
        if not row:
            continue
        row_date = _excel_serial_date(row[0])
        if row_date is not None and row_date <= as_of_date:
            dated_rows.append((row_date, row))
    return dated_rows


def _latest_values_for_date(
    dated_rows: list[tuple[date, list[str]]],
    target_date: date | None,
) -> list[str]:
    if target_date is None:
        return []
    for row_date, row in reversed(dated_rows):
        if row_date == target_date and any(_float_or_none(value) is not None for value in row[1:]):
            return row
    return []


def _points_from_headers(
    headers: dict[int, float],
    row: list[str],
    *,
    unit: str,
) -> dict[int, float]:
    points: dict[int, float] = {}
    for col_index, maturity in headers.items():
        if col_index >= len(row):
            continue
        value = _float_or_none(row[col_index])
        if value is None:
            continue
        if unit == "months":
            tenor = _month_tenor_days(maturity)
        else:
            tenor = _year_tenor_days(maturity)
        if tenor in BOE_OIS_TENORS:
            points[tenor] = value
    return points


def _month_tenor_days(months: float) -> int:
    rounded = round(months)
    return {1: 30, 3: 90, 6: 180, 12: 365}.get(rounded, int(round(months * 30.4375)))


def _year_tenor_days(years: float) -> int:
    rounded = round(years * 2) / 2
    return {0.5: 180, 1.0: 365, 2.0: 730}.get(rounded, int(round(years * 365)))


def _excel_serial_date(value: str) -> date | None:
    parsed = _float_or_none(value)
    if parsed is None:
        return None
    return date(1899, 12, 30) + timedelta(days=int(parsed))


def _float_or_none(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None
