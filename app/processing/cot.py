"""CFTC Commitment of Traders ingestion and derived positioning payloads."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO, TextIOWrapper
from typing import Any
from zipfile import ZipFile

import httpx

CFTC_BASE_URL = "https://www.cftc.gov/dea/newcot/"
CFTC_HISTORY_BASE_URL = "https://www.cftc.gov/files/dea/history/"
COT_CACHE_TTL = timedelta(hours=6)

COT_PAIRS = {
    "EUR": {
        "label": "EUR",
        "pair_label": "EUR/USD",
        "market": "EURO FX",
    },
    "GBP": {
        "label": "GBP",
        "pair_label": "GBP/USD",
        "market": "BRITISH POUND",
    },
    "JPY": {
        "label": "JPY",
        "pair_label": "USD/JPY",
        "market": "JAPANESE YEN",
    },
    "CAD": {
        "label": "CAD",
        "pair_label": "USD/CAD",
        "market": "CANADIAN DOLLAR",
    },
    "CHF": {
        "label": "CHF",
        "pair_label": "USD/CHF",
        "market": "SWISS FRANC",
    },
    "AUD": {
        "label": "AUD",
        "pair_label": "AUD/USD",
        "market": "AUSTRALIAN DOLLAR",
    },
    "NZD": {
        "label": "NZD",
        "pair_label": "NZD/USD",
        "market": "NZ DOLLAR",
    },
    "MXN": {
        "label": "MXN",
        "pair_label": "USD/MXN",
        "market": "MEXICAN PESO",
    },
    "USD": {
        "label": "USD",
        "pair_label": "USD Index",
        "market": "USD INDEX",
    },
    "GOLD": {
        "label": "GOLD",
        "pair_label": "Gold Futures",
        "market": "GOLD",
    },
    "OIL": {
        "label": "OIL",
        "pair_label": "Crude Oil (WTI)",
        "market": "CRUDE OIL, LIGHT SWEET-WTI",
    },
}

CSV_COLUMNS = {
    "market": "Market and Exchange Names",
    "noncom_long": "Noncommercial Positions-Long (All)",
    "noncom_short": "Noncommercial Positions-Short (All)",
    "com_long": "Commercial Positions-Long (All)",
    "com_short": "Commercial Positions-Short (All)",
    "nonrept_long": "Nonreportable Positions-Long (All)",
    "nonrept_short": "Nonreportable Positions-Short (All)",
    "open_interest": "Open Interest (All)",
    "week_date": "As of Date in Form YYYY-MM-DD",
}


@dataclass
class CotCache:
    expires_at: datetime
    rows_by_pair: dict[str, list[dict[str, Any]]]


_cache: CotCache | None = None


def normalize_cot_pair(pair: str) -> str:
    normalized = pair.upper().replace("/", "").replace("-", "").replace(" ", "")
    aliases = {
        "EURUSD": "EUR",
        "GBPUSD": "GBP",
        "USDJPY": "JPY",
        "USDCAD": "CAD",
        "USDCHF": "CHF",
        "AUDUSD": "AUD",
        "NZDUSD": "NZD",
        "USDMXN": "MXN",
    }
    if normalized in aliases:
        return aliases[normalized]
    return normalized


async def get_cot_payload(pair: str) -> dict[str, Any]:
    pair_key = normalize_cot_pair(pair)
    if pair_key not in COT_PAIRS:
        raise ValueError(f"Unsupported COT pair: {pair}")

    rows_by_pair = await _load_cot_rows()
    rows = rows_by_pair.get(pair_key, [])
    if len(rows) < 2:
        raise RuntimeError(f"CFTC COT data is unavailable for {COT_PAIRS[pair_key]['label']}.")
    return _build_pair_payload(pair_key, rows[-12:])


async def _load_cot_rows() -> dict[str, list[dict[str, Any]]]:
    global _cache
    now = datetime.now(timezone.utc)
    if _cache and _cache.expires_at > now:
        return _cache.rows_by_pair

    source_rows: list[dict[str, Any]] = []
    current_year = now.year
    years = [current_year - 2, current_year - 1, current_year]

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        for year in years:
            try:
                response = await client.get(_history_zip_url(year))
                response.raise_for_status()
                source_rows.extend(_parse_cot_zip(response.content))
            except httpx.HTTPError:
                pass

    rows_by_pair: dict[str, list[dict[str, Any]]] = {}
    for pair_key, config in COT_PAIRS.items():
        rows = _filter_market_rows(
            source_rows,
            str(config["market"]),
        )
        rows_by_pair[pair_key] = sorted(rows, key=lambda row: row["week_date"])

    _cache = CotCache(
        expires_at=now + COT_CACHE_TTL,
        rows_by_pair=rows_by_pair,
    )
    return rows_by_pair


def _history_zip_url(year: int) -> str:
    return f"{CFTC_HISTORY_BASE_URL}deacot{year}.zip"


def _parse_cot_zip(content: bytes) -> list[dict[str, Any]]:
    with ZipFile(BytesIO(content)) as archive:
        name = next(
            (
                entry
                for entry in archive.namelist()
                if entry.lower().endswith((".csv", ".txt"))
            ),
            None,
        )
        if name is None:
            return []
        with archive.open(name) as raw_file:
            text_file = TextIOWrapper(raw_file, encoding="latin-1", newline="")
            reader = csv.DictReader(text_file)
            return [
                _normalize_row(row)
                for row in reader
                if row.get(CSV_COLUMNS["market"])
            ]


def _normalize_row(row: dict[str, str]) -> dict[str, Any]:
    return {
        "market": str(row.get(CSV_COLUMNS["market"], "")).strip(),
        "week_date": str(row.get(CSV_COLUMNS["week_date"], "")).strip(),
        "noncom_long": _number(row.get(CSV_COLUMNS["noncom_long"])),
        "noncom_short": _number(row.get(CSV_COLUMNS["noncom_short"])),
        "com_long": _number(row.get(CSV_COLUMNS["com_long"])),
        "com_short": _number(row.get(CSV_COLUMNS["com_short"])),
        "nonrept_long": _number(row.get(CSV_COLUMNS["nonrept_long"])),
        "nonrept_short": _number(row.get(CSV_COLUMNS["nonrept_short"])),
        "open_interest": _number(row.get(CSV_COLUMNS["open_interest"])),
    }


def _filter_market_rows(rows: list[dict[str, Any]], market_match: str) -> list[dict[str, Any]]:
    match = market_match.casefold()
    prefix = match if " - " in match else f"{match} -"
    return [
        row
        for row in rows
        if str(row.get("market", "")).casefold().startswith(prefix)
        and row.get("week_date")
    ]


def _number(value: Any) -> int:
    if value is None:
        return 0
    cleaned = str(value).replace(",", "").strip()
    if not cleaned:
        return 0
    try:
        return int(float(cleaned))
    except ValueError:
        return 0


def _k(value: int | float) -> int:
    return int(round(float(value) / 1000))


def _sentiment_index(noncom_long: int, noncom_short: int) -> int:
    total = noncom_long + noncom_short
    if total == 0:
        return 0
    return int(round(((noncom_long - noncom_short) / total) * 100))


def _percentile_rank(value: int, values: list[int]) -> int:
    if not values:
        return 50
    lower_or_equal = sum(1 for item in values if item <= value)
    return int(round((lower_or_equal / len(values)) * 100))


def _cot_signal(current_net: int, previous_net: int, commercial_net: int, history_net: list[int]) -> dict[str, Any]:
    percentile = _percentile_rank(current_net, history_net)
    wow = current_net - previous_net
    divergence = current_net - commercial_net

    if percentile >= 80 and divergence > 0:
        bias = "Bearish"
        tone = "Crowded long"
    elif percentile <= 20 and divergence < 0:
        bias = "Bullish"
        tone = "Crowded short"
    elif abs(wow) >= 15000:
        bias = "Bullish" if wow > 0 else "Bearish"
        tone = "Momentum build"
    else:
        bias = "Neutral"
        tone = "Balanced"

    return {
        "bias": bias,
        "tone": tone,
        "percentile": percentile,
        "divergence": _k(divergence),
    }


def _build_pair_payload(pair_key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    current = rows[-1]
    previous = rows[-2]
    net_position = int(current["noncom_long"]) - int(current["noncom_short"])
    previous_net = int(previous["noncom_long"]) - int(previous["noncom_short"])
    current_com_net = int(current["com_long"]) - int(current["com_short"])
    previous_com_net = int(previous["com_long"]) - int(previous["com_short"])
    current_nr_net = int(current["nonrept_long"]) - int(current["nonrept_short"])
    previous_nr_net = int(previous["nonrept_long"]) - int(previous["nonrept_short"])
    history_net = [int(row["noncom_long"]) - int(row["noncom_short"]) for row in rows]

    weeks = [str(row["week_date"]) for row in rows]
    longs = [_k(int(row["noncom_long"])) for row in rows]
    shorts = [_k(int(row["noncom_short"])) for row in rows]
    net = [_k(value) for value in history_net]
    commercial_net = [_k(int(row["com_long"]) - int(row["com_short"])) for row in rows]
    nonreportable_net = [_k(int(row["nonrept_long"]) - int(row["nonrept_short"])) for row in rows]
    signal = _cot_signal(net_position, previous_net, current_com_net, history_net)

    return {
        "pair": pair_key,
        "label": COT_PAIRS[pair_key]["label"],
        "pair_label": COT_PAIRS[pair_key]["pair_label"],
        "weeks": weeks,
        "longs": longs,
        "shorts": shorts,
        "net": net,
        "commercial_net": commercial_net,
        "nonreportable_net": nonreportable_net,
        "noncom_long": _k(int(current["noncom_long"])),
        "noncom_short": _k(int(current["noncom_short"])),
        "com_long": _k(int(current["com_long"])),
        "com_short": _k(int(current["com_short"])),
        "nonrept_long": _k(int(current["nonrept_long"])),
        "nonrept_short": _k(int(current["nonrept_short"])),
        "open_interest": _k(int(current["open_interest"])),
        "prev_nl": _k(int(previous["noncom_long"])),
        "prev_ns": _k(int(previous["noncom_short"])),
        "prev_cl": _k(int(previous["com_long"])),
        "prev_cs": _k(int(previous["com_short"])),
        "prev_nrl": _k(int(previous["nonrept_long"])),
        "prev_nrs": _k(int(previous["nonrept_short"])),
        "net_position": _k(net_position),
        "commercial_net_current": _k(current_com_net),
        "nonreportable_net_current": _k(current_nr_net),
        "sentiment_index": _sentiment_index(int(current["noncom_long"]), int(current["noncom_short"])),
        "chg_long": _k(int(current["noncom_long"]) - int(previous["noncom_long"])),
        "chg_short": _k(int(current["noncom_short"]) - int(previous["noncom_short"])),
        "chg_net": _k(net_position - previous_net),
        "chg_commercial_net": _k(current_com_net - previous_com_net),
        "chg_nonreportable_net": _k(current_nr_net - previous_nr_net),
        "signal": signal,
    }


async def get_all_cot_rows() -> dict[str, list[dict[str, Any]]]:
    """Return raw COT row history for all supported markets, formatted for the frontend chart dashboard."""
    rows_by_pair = await _load_cot_rows()
    result: dict[str, list[dict[str, Any]]] = {}
    for pair_key, rows in rows_by_pair.items():
        history = rows[-156:]
        result[pair_key] = [
            {
                "date": row["week_date"],
                "specLong": int(row["noncom_long"]),
                "specShort": int(row["noncom_short"]),
                "commercialLong": int(row["com_long"]),
                "commercialShort": int(row["com_short"]),
                "openInterest": int(row["open_interest"]),
                "specNet": int(row["noncom_long"]) - int(row["noncom_short"]),
                "commercialNet": int(row["com_long"]) - int(row["com_short"]),
            }
            for row in history
        ]
    return result
