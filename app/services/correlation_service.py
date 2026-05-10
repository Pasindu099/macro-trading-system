"""Data assembly for the Currency Research Lab correlation views."""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope
from app.ingestion.eodhd_client import EODHDClient, EODHDError, EODHDRateLimitError

DATA_DIR = Path("data")
FX_PRICE_HISTORY_PATH = DATA_DIR / "fx_validation" / "fx_price_history.csv"
INDICATOR_FEATURES_PATH = DATA_DIR / "features" / "indicator_features.csv"
POLICY_SIGNALS_PATH = DATA_DIR / "policy" / "policy_signals.csv"

SUPPORTED_PAIRS = ("EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "USDCHF")
SUPPORTED_TIMEFRAMES = {"3M": 3, "6M": 6, "1Y": 12, "2Y": 24}
SUPPORTED_OVERLAYS = {"yieldDiff", "cpi", "rate", "pmi"}

PAIR_CURRENCIES = {
    "EURUSD": ("EUR", "USD"),
    "USDJPY": ("USD", "JPY"),
    "GBPUSD": ("GBP", "USD"),
    "AUDUSD": ("AUD", "USD"),
    "USDCHF": ("USD", "CHF"),
}

YIELD_SYMBOLS = {
    "USD": "US10Y.GBOND",
    "EUR": "DE10Y.GBOND",
    "GBP": "UK10Y.GBOND",
    "JPY": "JP10Y.GBOND",
    "AUD": "AU10Y.GBOND",
    "CHF": "SW10Y.GBOND",
}

DRIVER_LABELS = {
    "yieldDiff": "Yield diff",
    "rateDiff": "Rate diff",
    "cpiDiff": "CPI diff",
    "pmiDiff": "PMI diff",
    "gdpDiff": "GDP diff",
}


@dataclass(frozen=True)
class MonthlyPoint:
    month: date
    value: float


async def ensure_correlation_schema() -> None:
    async with session_scope() as session:
        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS correlation_cache (
                    id BIGSERIAL PRIMARY KEY,
                    pair VARCHAR(10) NOT NULL,
                    series_key VARCHAR(50) NOT NULL,
                    month DATE NOT NULL,
                    value NUMERIC(18, 6),
                    fetched_at TIMESTAMPTZ DEFAULT now(),
                    UNIQUE(pair, series_key, month)
                )
                """
            )
        )
        await session.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_correlation_cache_pair_series_month
                ON correlation_cache (pair, series_key, month)
                """
            )
        )


def normalize_pair(pair: str) -> str:
    normalized = pair.replace("/", "").replace("-", "").upper()
    if normalized not in SUPPORTED_PAIRS:
        raise ValueError(f"Unsupported pair {pair!r}")
    return normalized


def normalize_timeframe(tf: str) -> str:
    normalized = tf.upper()
    if normalized not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe {tf!r}")
    return normalized


def normalize_overlays(overlays: str | None) -> list[str]:
    if not overlays:
        return ["yieldDiff", "cpi", "rate", "pmi"]
    parsed = [
        item.strip()
        for item in overlays.split(",")
        if item.strip()
    ]
    return [item for item in parsed if item in SUPPORTED_OVERLAYS]


async def get_correlation_series(
    session: AsyncSession,
    *,
    pair: str,
    tf: str,
    overlays: str | None,
) -> dict[str, Any]:
    pair = normalize_pair(pair)
    tf = normalize_timeframe(tf)
    base_ccy, quote_ccy = PAIR_CURRENCIES[pair]

    price_history = _load_monthly_prices(pair)
    if not price_history:
        raise ValueError(f"No FX price history found for {pair}")

    driver_maps = await _load_driver_maps(session, pair, base_ccy, quote_ccy)
    months = sorted(set(price_history).intersection(_available_driver_months(driver_maps)))
    months = months[-SUPPORTED_TIMEFRAMES[tf]:]
    if not months:
        raise ValueError(f"No overlapping macro history found for {pair}")

    price = [price_history[month] for month in months]
    yield_diff = [_value_or_none(driver_maps["yieldDiff"].get(month)) for month in months]
    cpi_diff = [_value_or_none(driver_maps["cpiDiff"].get(month)) for month in months]
    rate_diff = [_value_or_none(driver_maps["rateDiff"].get(month)) for month in months]
    pmi_diff = [_value_or_none(driver_maps["pmiDiff"].get(month)) for month in months]
    fair_value = _fair_value(price, yield_diff, rate_diff, cpi_diff, pmi_diff)
    correlation = _pearson_pairwise(price, yield_diff)
    divergence_pct = _pct_divergence(price[-1], fair_value[-1])

    response: dict[str, Any] = {
        "pair": pair,
        "base_ccy": base_ccy,
        "quote_ccy": quote_ccy,
        "labels": [_month_label(month) for month in months],
        "price": [_round(value, 4) for value in price],
        "fairValue": [_round(value, 4) for value in fair_value],
        "cachedData": bool(driver_maps.get("yieldCached")),
        "stats": {
            "currentPrice": _round(price[-1], 4),
            "priceChange1d": _round(_latest_daily_change_pct(pair), 2),
            "yieldDiff": _round(_last_number(yield_diff), 2),
            "correlation": _round(correlation, 2),
            "rSquared": _round((correlation or 0) ** 2 * 100, 1),
            "divergencePct": _round(divergence_pct, 1),
        },
    }

    response["yieldDiff"] = [_round_or_none(value, 3) for value in yield_diff]
    response["cpiDiff"] = [_round_or_none(value, 3) for value in cpi_diff]
    response["rateDiff"] = [_round_or_none(value, 3) for value in rate_diff]
    response["pmiDiff"] = [_round_or_none(value, 3) for value in pmi_diff]

    return response


async def get_correlation_matrix(
    session: AsyncSession,
    *,
    pair: str,
) -> dict[str, Any]:
    pair = normalize_pair(pair)
    base_ccy, quote_ccy = PAIR_CURRENCIES[pair]
    price_history = _load_monthly_prices(pair)
    if not price_history:
        raise ValueError(f"No FX price history found for {pair}")

    driver_maps = await _load_driver_maps(session, pair, base_ccy, quote_ccy)
    drivers = []
    for key in ("yieldDiff", "rateDiff", "cpiDiff", "pmiDiff", "gdpDiff"):
        common_months = sorted(set(price_history).intersection(driver_maps[key]))
        r = _pearson_pairwise(
            [price_history[month] for month in common_months],
            [driver_maps[key][month] for month in common_months],
        )
        drivers.append({"label": DRIVER_LABELS[key], "r": _round(r, 2)})

    return {
        "pair": pair,
        "drivers": sorted(
            drivers,
            key=lambda item: abs(float(item["r"] or 0)),
            reverse=True,
        ),
    }


async def _load_driver_maps(
    session: AsyncSession,
    pair: str,
    base_ccy: str,
    quote_ccy: str,
) -> dict[str, dict[date, float]]:
    cached_yields = await _load_cached_series(session, pair, "yieldDiff")
    if not cached_yields:
        cached_yields = await _fetch_and_cache_yield_diff(
            session,
            pair,
            base_ccy,
            quote_ccy,
        )
    cpi = _macro_diff(base_ccy, quote_ccy, "cpi")
    pmi = _macro_diff(base_ccy, quote_ccy, "pmi")
    gdp = _macro_diff(base_ccy, quote_ccy, "gdp")
    rate = _policy_or_macro_rate_diff(base_ccy, quote_ccy)
    yield_proxy = cached_yields or _smooth_driver(rate)
    return {
        "yieldDiff": yield_proxy,
        "yieldCached": {date(1970, 1, 1): 1.0} if cached_yields else {},
        "cpiDiff": cpi,
        "rateDiff": rate,
        "pmiDiff": pmi,
        "gdpDiff": gdp,
    }


async def _fetch_and_cache_yield_diff(
    session: AsyncSession,
    pair: str,
    base_ccy: str,
    quote_ccy: str,
) -> dict[date, float]:
    base_symbol = YIELD_SYMBOLS.get(base_ccy)
    quote_symbol = YIELD_SYMBOLS.get(quote_ccy)
    if not base_symbol or not quote_symbol:
        return {}

    try:
        today = date.today()
        from_date = today - timedelta(days=365 * 4)
        async with EODHDClient(timeout_seconds=8, max_retries=0) as client:
            base_rows = await client.fetch_eod_history(
                base_symbol,
                from_date=from_date,
                to_date=today,
                period="m",
            )
            quote_rows = await client.fetch_eod_history(
                quote_symbol,
                from_date=from_date,
                to_date=today,
                period="m",
            )
    except EODHDRateLimitError:
        return await _load_cached_series(session, pair, "yieldDiff")
    except (EODHDError, ValueError):
        return {}

    base_history = _parse_monthly_market_history(base_rows)
    quote_history = _parse_monthly_market_history(quote_rows)
    yield_diff = _diff_maps(base_history, quote_history)
    if not yield_diff:
        return {}

    await _cache_series(session, pair, "yieldDiff", yield_diff)
    return yield_diff


def _parse_monthly_market_history(rows: list[dict[str, Any]]) -> dict[date, float]:
    monthly: dict[date, MonthlyPoint] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        point_date = _parse_date(str(row.get("date") or ""))
        value = _market_row_value(row)
        if point_date is None or value is None:
            continue
        month = point_date.replace(day=1)
        current = monthly.get(month)
        if current is None or point_date >= current.month:
            monthly[month] = MonthlyPoint(point_date, value)
    return {month: point.value for month, point in sorted(monthly.items())}


def _market_row_value(row: dict[str, Any]) -> float | None:
    for key in ("close", "adjusted_close", "value"):
        value = _to_float(row.get(key))
        if value is not None:
            return value
    return None


async def _cache_series(
    session: AsyncSession,
    pair: str,
    series_key: str,
    values: dict[date, float],
) -> None:
    for month, value in values.items():
        await session.execute(
            text(
                """
                INSERT INTO correlation_cache (pair, series_key, month, value)
                VALUES (:pair, :series_key, :month, :value)
                ON CONFLICT (pair, series_key, month)
                DO UPDATE SET value = EXCLUDED.value, fetched_at = now()
                """
            ),
            {
                "pair": pair,
                "series_key": series_key,
                "month": month,
                "value": value,
            },
        )
    await session.commit()


async def _load_cached_series(
    session: AsyncSession,
    pair: str,
    series_key: str,
) -> dict[date, float]:
    result = await session.execute(
        text(
            """
            SELECT month, value
            FROM correlation_cache
            WHERE pair = :pair AND series_key = :series_key
              AND value IS NOT NULL
            ORDER BY month
            """
        ),
        {"pair": pair, "series_key": series_key},
    )
    rows = result.all()
    return {
        row.month: float(row.value)
        for row in rows
        if row.month is not None and row.value is not None
    }


def _load_monthly_prices(pair: str) -> dict[date, float]:
    by_month: dict[date, MonthlyPoint] = {}
    if not FX_PRICE_HISTORY_PATH.exists():
        return {}
    with FX_PRICE_HISTORY_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("pair_code") != pair:
                continue
            point_date = _parse_date(row.get("price_date"))
            close = _to_float(row.get("close_price"))
            if point_date is None or close is None:
                continue
            month = point_date.replace(day=1)
            current = by_month.get(month)
            if current is None or point_date >= current.month:
                by_month[month] = MonthlyPoint(point_date, close)
    return {month: point.value for month, point in sorted(by_month.items())}


def _latest_daily_change_pct(pair: str) -> float | None:
    points: list[tuple[date, float]] = []
    if not FX_PRICE_HISTORY_PATH.exists():
        return None
    with FX_PRICE_HISTORY_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("pair_code") != pair:
                continue
            point_date = _parse_date(row.get("price_date"))
            close = _to_float(row.get("close_price"))
            if point_date is not None and close is not None:
                points.append((point_date, close))
    points.sort(key=lambda item: item[0])
    if len(points) < 2 or not points[-2][1]:
        return None
    return ((points[-1][1] - points[-2][1]) / points[-2][1]) * 100


def _macro_diff(base_ccy: str, quote_ccy: str, driver: str) -> dict[date, float]:
    by_currency = _load_macro_driver(driver)
    return _diff_maps(by_currency.get(base_ccy, {}), by_currency.get(quote_ccy, {}))


def _load_macro_driver(driver: str) -> dict[str, dict[date, float]]:
    buckets: dict[str, dict[date, list[float]]] = defaultdict(lambda: defaultdict(list))
    if not INDICATOR_FEATURES_PATH.exists():
        return {}
    with INDICATOR_FEATURES_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ccy = str(row.get("currency_code") or "").upper()
            indicator_key = str(row.get("indicator_key") or "").lower()
            macro_theme = str(row.get("macro_theme") or "").lower()
            month = _parse_date(row.get("reference_period_start"))
            value = _to_float(row.get("actual_value"))
            if not ccy or month is None or value is None:
                continue
            if not _matches_driver(driver, indicator_key, macro_theme):
                continue
            buckets[ccy][month.replace(day=1)].append(value)
    return {
        ccy: {
            month: mean(values)
            for month, values in sorted(months.items())
            if values
        }
        for ccy, months in buckets.items()
    }


def _policy_or_macro_rate_diff(base_ccy: str, quote_ccy: str) -> dict[date, float]:
    policy = _load_policy_scores()
    diff = _diff_maps(policy.get(base_ccy, {}), policy.get(quote_ccy, {}))
    if diff:
        return diff
    return _macro_diff(base_ccy, quote_ccy, "rate")


def _load_policy_scores() -> dict[str, dict[date, float]]:
    buckets: dict[str, dict[date, list[float]]] = defaultdict(lambda: defaultdict(list))
    if not POLICY_SIGNALS_PATH.exists():
        return {}
    with POLICY_SIGNALS_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ccy = str(row.get("currency_code") or "").upper()
            month = _parse_date(row.get("signal_date"))
            value = _to_float(row.get("policy_score"))
            if ccy and month is not None and value is not None:
                buckets[ccy][month.replace(day=1)].append(value)
    return {
        ccy: {
            month: mean(values)
            for month, values in sorted(months.items())
            if values
        }
        for ccy, months in buckets.items()
    }


def _matches_driver(driver: str, indicator_key: str, macro_theme: str) -> bool:
    if driver == "cpi":
        return "cpi" in indicator_key or "inflation" in macro_theme
    if driver == "pmi":
        return "pmi" in indicator_key
    if driver == "gdp":
        return "gdp" in indicator_key
    if driver == "rate":
        return "rate" in indicator_key or macro_theme == "monetary policy"
    return False


def _diff_maps(left: dict[date, float], right: dict[date, float]) -> dict[date, float]:
    return {
        month: left[month] - right[month]
        for month in sorted(set(left).intersection(right))
    }


def _smooth_driver(values: dict[date, float]) -> dict[date, float]:
    ordered = sorted(values.items())
    smoothed: dict[date, float] = {}
    window: list[float] = []
    for month, value in ordered:
        window.append(value)
        window = window[-3:]
        smoothed[month] = mean(window)
    return smoothed


def _available_driver_months(driver_maps: dict[str, dict[date, float]]) -> set[date]:
    months: set[date] = set()
    for values in driver_maps.values():
        months.update(values)
    return months


def _fair_value(
    price: list[float],
    yield_diff: list[float | None],
    rate_diff: list[float | None],
    cpi_diff: list[float | None],
    pmi_diff: list[float | None],
) -> list[float]:
    drivers = [
        _combine_driver_row(row)
        for row in zip(yield_diff, rate_diff, cpi_diff, pmi_diff, strict=True)
    ]
    fitted = _linear_fit(drivers, price)
    return fitted or _moving_average(price, window=3)


def _combine_driver_row(row: tuple[float | None, ...]) -> float:
    values = [value for value in row if value is not None and math.isfinite(value)]
    return mean(values) if values else 0.0


def _linear_fit(x_values: list[float], y_values: list[float]) -> list[float] | None:
    if len(x_values) < 3 or len({round(value, 8) for value in x_values}) < 2:
        return None
    x_mean = mean(x_values)
    y_mean = mean(y_values)
    denominator = sum((x - x_mean) ** 2 for x in x_values)
    if not denominator:
        return None
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values, strict=True)) / denominator
    intercept = y_mean - slope * x_mean
    return [intercept + slope * x for x in x_values]


def _moving_average(values: list[float], window: int) -> list[float]:
    out = []
    for index in range(len(values)):
        subset = values[max(0, index - window + 1): index + 1]
        out.append(mean(subset))
    return out


def _pearson_pairwise(left: list[float | None], right: list[float | None]) -> float | None:
    pairs = [
        (float(a), float(b))
        for a, b in zip(left, right, strict=False)
        if a is not None
        and b is not None
        and math.isfinite(float(a))
        and math.isfinite(float(b))
    ]
    if len(pairs) < 3:
        return None
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    x_mean = mean(xs)
    y_mean = mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    x_var = sum((x - x_mean) ** 2 for x in xs)
    y_var = sum((y - y_mean) ** 2 for y in ys)
    if not x_var or not y_var:
        return None
    return numerator / math.sqrt(x_var * y_var)


def _pct_divergence(price: float, fair_value: float | None) -> float | None:
    if fair_value in (None, 0):
        return None
    return ((price - fair_value) / fair_value) * 100


def _month_label(month: date) -> str:
    return month.strftime("%b %y")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _to_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _value_or_none(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def _last_number(values: list[float | None]) -> float | None:
    for value in reversed(values):
        if value is not None:
            return value
    return None


def _round(value: float | None, digits: int) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _round_or_none(value: float | None, digits: int) -> float | None:
    return _round(value, digits)
