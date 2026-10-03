"""Rates and FX research data from stored observations."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from app.db.session import get_sessionmaker

from app.ingestion.eodhd_client import EODHDAuthError, EODHDClient, EODHDError

YIELD_HISTORY_DAYS = 70

YIELD_BASE_CURRENCY = "USD"

YIELD_BENCHMARKS = (
    {"currency": "USD", "country_code": "US", "label": "US 10Y", "symbol": "US10Y.GBOND"},
    {"currency": "EUR", "country_code": "DE", "label": "Germany 10Y", "symbol": "DE10Y.GBOND"},
    {"currency": "GBP", "country_code": "UK", "label": "UK 10Y", "symbol": "UK10Y.GBOND"},
    {"currency": "JPY", "country_code": "JP", "label": "Japan 10Y", "symbol": "JP10Y.GBOND"},
    {"currency": "AUD", "country_code": "AU", "label": "Australia 10Y", "symbol": "AU10Y.GBOND"},
    {"currency": "CAD", "country_code": "CA", "label": "Canada 10Y", "symbol": "CA10Y.GBOND"},
    {"currency": "CHF", "country_code": "CH", "label": "Switzerland 10Y", "symbol": "SW10Y.GBOND"},
    {"currency": "NZD", "country_code": "NZ", "label": "New Zealand 10Y", "symbol": "NZ10Y.GBOND"},
)

YIELD_PAIR_DEFS = (
    ("EUR/USD", "EUR", "USD"),
    ("GBP/USD", "GBP", "USD"),
    ("USD/JPY", "USD", "JPY"),
    ("AUD/USD", "AUD", "USD"),
    ("USD/CAD", "USD", "CAD"),
    ("USD/CHF", "USD", "CHF"),
    ("NZD/USD", "NZD", "USD"),
)

FX_PAIR_DEFS = (
    {"label": "EUR/USD", "symbol": "EURUSD.FOREX", "left_currency": "EUR", "right_currency": "USD"},
    {"label": "GBP/USD", "symbol": "GBPUSD.FOREX", "left_currency": "GBP", "right_currency": "USD"},
    {"label": "USD/JPY", "symbol": "USDJPY.FOREX", "left_currency": "USD", "right_currency": "JPY"},
    {"label": "AUD/USD", "symbol": "AUDUSD.FOREX", "left_currency": "AUD", "right_currency": "USD"},
    {"label": "USD/CAD", "symbol": "USDCAD.FOREX", "left_currency": "USD", "right_currency": "CAD"},
    {"label": "USD/CHF", "symbol": "USDCHF.FOREX", "left_currency": "USD", "right_currency": "CHF"},
    {"label": "NZD/USD", "symbol": "NZDUSD.FOREX", "left_currency": "NZD", "right_currency": "USD"},
)

YIELD_MATURITIES = (
    {"key": "1m", "label": "1M", "name": "1 month"},
    {"key": "3m", "label": "3M", "name": "3 months"},
    {"key": "6m", "label": "6M", "name": "6 months"},
    {"key": "1y", "label": "1Y", "name": "1 year"},
    {"key": "2y", "label": "2Y", "name": "2 years"},
    {"key": "3y", "label": "3Y", "name": "3 years"},
    {"key": "5y", "label": "5Y", "name": "5 years"},
    {"key": "10y", "label": "10Y", "name": "10 years"},
)

YIELD_MATURITY_SUFFIXES = {
    "1m": "1M",
    "3m": "3M",
    "6m": "6M",
    "1y": "1Y",
    "2y": "2Y",
    "3y": "3Y",
    "5y": "5Y",
    "10y": "10Y",
}

REPRICING_FRONT_TENOR = "2Y"

REPRICING_LONG_TENOR = "10Y"

REPRICING_HISTORY_DAYS = 400

REPRICING_LOOKBACK_DAYS = 7

REPRICING_STALE_DAYS = 3

REPRICING_MIN_REGRESSION_POINTS = 30

REPRICING_DIVERGENCE_SIGMA = 1.0

COUNTRY_FLAGS = {
    "US": "\U0001F1FA\U0001F1F8",
    "EU": "\U0001F1EA\U0001F1FA",
    "DE": "\U0001F1E9\U0001F1EA",
    "FR": "\U0001F1EB\U0001F1F7",
    "UK": "\U0001F1EC\U0001F1E7",
    "JP": "\U0001F1EF\U0001F1F5",
    "AU": "\U0001F1E6\U0001F1FA",
    "NZ": "\U0001F1F3\U0001F1FF",
    "CA": "\U0001F1E8\U0001F1E6",
    "CH": "\U0001F1E8\U0001F1ED",
}

def _flag_for_country(country_code: str) -> str:
    return COUNTRY_FLAGS.get(country_code, "\U0001F3F3\ufe0f")

def _spread_color_class(value: float | None) -> str:
    if value is None or abs(value) < 5:
        return "is-neutral"
    if value > 0:
        return "is-positive"
    return "is-negative"

def _format_bp(value: float | None, decimals: int = 0) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.{decimals}f} bp"

def _format_yield(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}%"

def _yield_history_point(
    rows: list[dict[str, Any]],
    offset: int,
) -> tuple[date | None, float | None]:
    if not rows:
        return None, None
    index = max(0, len(rows) - 1 - offset)
    row = rows[index]
    row_date = row.get("date")
    parsed_date = None
    if isinstance(row_date, date):
        parsed_date = row_date
    elif row_date:
        try:
            parsed_date = date.fromisoformat(str(row_date)[:10])
        except ValueError:
            parsed_date = None

    for key in ("close", "adjusted_close", "value"):
        if row.get(key) is None:
            continue
        try:
            return parsed_date, float(row[key])
        except (TypeError, ValueError):
            continue
    return parsed_date, None

def _parse_yield_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        parsed_date, value = _yield_history_point([row], 0)
        if parsed_date is None or value is None:
            continue
        points.append({
            "date": parsed_date,
            "date_key": parsed_date.isoformat(),
            "yield": value,
        })
    points.sort(key=lambda point: point["date"])
    return points

def _build_yield_chart_data(
    histories_by_currency: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    yield_series: list[dict[str, Any]] = []
    history_maps: dict[str, dict[str, float]] = {}

    for benchmark in YIELD_BENCHMARKS:
        currency = str(benchmark["currency"])
        points = histories_by_currency.get(currency, [])
        history_maps[currency] = {
            str(point["date_key"]): float(point["yield"])
            for point in points
        }
        yield_series.append({
            "name": f"{currency} 10Y",
            "currency": currency,
            "label": str(benchmark["label"]),
            "symbol": str(benchmark["symbol"]),
            "data": [
                [str(point["date_key"]), round(float(point["yield"]), 4)]
                for point in points
            ],
        })

    spread_series: list[dict[str, Any]] = []
    latest_spreads: list[dict[str, Any]] = []
    for label, left_currency, right_currency in YIELD_PAIR_DEFS:
        left_history = history_maps.get(left_currency, {})
        right_history = history_maps.get(right_currency, {})
        dates = sorted(set(left_history).intersection(right_history))
        data = [
            [
                point_date,
                round((left_history[point_date] - right_history[point_date]) * 100, 1),
            ]
            for point_date in dates
        ]
        spread_series.append({
            "name": label,
            "left_currency": left_currency,
            "right_currency": right_currency,
            "data": data,
        })
        if data:
            latest_spreads.append({
                "label": label,
                "value": data[-1][1],
                "date": data[-1][0],
            })

    latest_spreads.sort(key=lambda point: float(point["value"]), reverse=True)
    return {
        "yield_series": yield_series,
        "spread_series": spread_series,
        "latest_spreads": latest_spreads,
        "base_currency": YIELD_BASE_CURRENCY,
        "unit": "%",
        "spread_unit": "bp",
    }

def _gbond_symbol_code(row: dict[str, Any]) -> str | None:
    for key in ("Code", "code", "Symbol", "symbol"):
        value = row.get(key)
        if value:
            return str(value).split(".")[0].upper()
    return None

async def _fetch_gbond_symbol_set() -> set[str]:
    try:
        async with EODHDClient() as client:
            symbols = await client.fetch_exchange_symbols("GBOND")
    except (EODHDError, ValueError):
        return set()
    return {
        code
        for row in symbols
        if isinstance(row, dict)
        if (code := _gbond_symbol_code(row))
    }


async def _available_yield_symbols() -> set[str]:
    async with get_sessionmaker()() as session:
        result = await session.execute(text("""
            SELECT DISTINCT provider_symbol FROM government_yield_observations
            WHERE quality_status = 'valid'
        """))
    return {str(symbol).split(".")[0].upper() for symbol in result.scalars()}


async def _stored_histories(
    symbols: list[str], from_date: date, to_date: date, *, fx: bool = False,
) -> list[list[dict[str, Any]]]:
    table = "fx_spot_observations" if fx else "government_yield_observations"
    date_column = "observation_date" if fx else "market_observation_date"
    value_column = "close_value" if fx else "yield_value"
    # The table and column names above are constants, never caller input.
    query = text(f"""
        SELECT DISTINCT ON (provider_symbol, {date_column})
            provider_symbol, {date_column} AS obs_date, {value_column}::float AS close
        FROM {table}
        WHERE provider_symbol = ANY(:symbols)
          AND {date_column} BETWEEN :from_date AND :to_date
          AND quality_status = 'valid'
        ORDER BY provider_symbol, {date_column}, ingested_at DESC, id DESC
    """)
    async with get_sessionmaker()() as session:
        result = await session.execute(query, {
            "symbols": symbols, "from_date": from_date, "to_date": to_date,
        })
    histories = {symbol: [] for symbol in symbols}
    for row in result:
        histories[row.provider_symbol].append({"date": row.obs_date.isoformat(), "close": row.close})
    return [histories[symbol] for symbol in symbols]

def _yield_symbol_prefix(benchmark: dict[str, Any]) -> str:
    symbol = str(benchmark["symbol"]).split(".", 1)[0]
    return symbol.removesuffix("10Y")

def _build_maturity_benchmarks(
    maturity_key: str,
    available_symbols: set[str],
) -> list[dict[str, Any]]:
    suffix = YIELD_MATURITY_SUFFIXES[maturity_key]
    benchmarks: list[dict[str, Any]] = []
    for benchmark in YIELD_BENCHMARKS:
        symbol_code = f"{_yield_symbol_prefix(benchmark)}{suffix}"
        if available_symbols and symbol_code not in available_symbols:
            continue
        benchmarks.append({
            **benchmark,
            "label": str(benchmark["label"]).replace("10Y", suffix),
            "symbol": f"{symbol_code}.GBOND",
            "maturity_key": maturity_key,
            "maturity_label": suffix,
        })
    return benchmarks

def _build_fx_chart_data(
    histories_by_pair: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    for pair in FX_PAIR_DEFS:
        label = str(pair["label"])
        points = _parse_yield_history(histories_by_pair.get(label, []))
        if not points:
            series.append({
                "name": label,
                "symbol": pair["symbol"],
                "data": [],
            })
            continue
        base = float(points[0]["yield"])
        data = []
        for point in points:
            value = float(point["yield"])
            performance = ((value - base) / base) * 100 if base else 0
            data.append([str(point["date_key"]), round(performance, 3)])
        series.append({
            "name": label,
            "symbol": pair["symbol"],
            "data": data,
        })
    return series

def _build_maturity_panels(
    yield_by_maturity: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    def available_for(key: str) -> bool:
        return bool(yield_by_maturity.get(key, {}).get("rows"))

    return [
        {
            "key": maturity["key"],
            "label": maturity["label"],
            "name": maturity["name"],
            "available": available_for(str(maturity["key"])),
            "summary": (
                "Live government-bond feed"
                if available_for(str(maturity["key"]))
                else "Awaiting feed"
            ),
        }
        for maturity in YIELD_MATURITIES
    ]

async def _build_rates_research_context() -> dict[str, Any]:
    available_symbols = await _available_yield_symbols()
    yield_by_maturity: dict[str, dict[str, Any]] = {}
    for maturity in YIELD_MATURITIES:
        maturity_key = str(maturity["key"])
        benchmarks = _build_maturity_benchmarks(maturity_key, available_symbols)
        if not benchmarks:
            yield_by_maturity[maturity_key] = {
                "rows": [],
                "pairs": [],
                "stats": [],
                "base_currency": YIELD_BASE_CURRENCY,
                "symbols": [],
                "errors": [],
                "chart_data": _build_yield_chart_data({}),
                "message": "No stored government yields were found for this maturity.",
            }
            continue
        yield_by_maturity[maturity_key] = await _build_yield_differentials(
            benchmarks,
            maturity_label=str(maturity["label"]),
        )

    active_maturity = next(
        (
            str(maturity["key"])
            for maturity in YIELD_MATURITIES
            if yield_by_maturity.get(str(maturity["key"]), {}).get("rows")
        ),
        "10y",
    )
    yield_differentials = yield_by_maturity.get(active_maturity, yield_by_maturity["10y"])
    today = _now().date()
    from_date = today - timedelta(days=YIELD_HISTORY_DAYS)
    histories_by_pair: dict[str, list[dict[str, Any]]] = {}
    fx_errors: list[dict[str, Any]] = []

    histories = await _stored_histories(
        [str(pair["symbol"]) for pair in FX_PAIR_DEFS], from_date, today, fx=True,
    )

    for pair, history in zip(FX_PAIR_DEFS, histories, strict=False):
        if isinstance(history, Exception):
            fx_errors.append({
                "symbol": pair["symbol"],
                "label": pair["label"],
                "error": str(history),
            })
            continue
        histories_by_pair[str(pair["label"])] = [
            row for row in history
            if isinstance(row, dict) and row.get("date") and row.get("close") is not None
        ]

    fx_series = _build_fx_chart_data(histories_by_pair)
    pair_research = []
    spread_lookup = {
        str(pair["label"]): pair
        for pair in yield_differentials.get("pairs", [])
    }
    for pair in FX_PAIR_DEFS:
        fx_points = next(
            (series["data"] for series in fx_series if series["name"] == pair["label"]),
            [],
        )
        spread = spread_lookup.get(str(pair["label"]))
        performance = fx_points[-1][1] if fx_points else None
        pair_research.append({
            "label": pair["label"],
            "symbol": pair["symbol"],
            "spread_display": spread["spread_display"] if spread else "N/A",
            "spread_class": spread["spread_class"] if spread else "is-neutral",
            "fx_performance": f"{performance:+.2f}%" if performance is not None else "N/A",
            "fx_class": _spread_color_class(performance) if performance is not None else "is-neutral",
        })

    return {
        "yield_differentials": yield_differentials,
        "yield_by_maturity": yield_by_maturity,
        "yield_chart_by_maturity": {
            key: value.get("chart_data", _build_yield_chart_data({}))
            for key, value in yield_by_maturity.items()
        },
        "active_maturity": active_maturity,
        "maturity_panels": _build_maturity_panels(yield_by_maturity),
        "fx_series": fx_series,
        "pair_research": pair_research,
        "fx_errors": fx_errors,
    }

async def _build_yield_differentials(
    benchmarks: list[dict[str, Any]] | None = None,
    maturity_label: str = "10Y",
) -> dict[str, Any]:
    benchmarks = benchmarks or list(YIELD_BENCHMARKS)
    today = _now().date()
    from_date = today - timedelta(days=YIELD_HISTORY_DAYS)

    histories = await _stored_histories(
        [str(benchmark["symbol"]) for benchmark in benchmarks], from_date, today,
    )

    auth_blocked = any(isinstance(history, EODHDAuthError) for history in histories)
    failed_symbols = [
        {
            "symbol": str(benchmark["symbol"]),
            "label": str(benchmark["label"]),
            "error": str(history),
            "is_access_error": isinstance(history, EODHDAuthError),
        }
        for benchmark, history in zip(benchmarks, histories, strict=True)
        if isinstance(history, Exception)
    ]

    rows: list[dict[str, Any]] = []
    histories_by_currency: dict[str, list[dict[str, Any]]] = {}
    for benchmark, history in zip(benchmarks, histories, strict=True):
        if isinstance(history, Exception):
            continue
        clean_history = [
            row for row in history
            if isinstance(row, dict) and row.get("date") and row.get("close") is not None
        ]
        clean_history.sort(key=lambda row: str(row.get("date")))
        histories_by_currency[str(benchmark["currency"])] = _parse_yield_history(clean_history)
        latest_date, latest_yield = _yield_history_point(clean_history, 0)
        _, yield_5d = _yield_history_point(clean_history, 5)
        _, yield_1m = _yield_history_point(clean_history, 21)
        if latest_yield is None:
            continue

        change_5d_bp = (
            (latest_yield - yield_5d) * 100
            if yield_5d is not None
            else None
        )
        change_1m_bp = (
            (latest_yield - yield_1m) * 100
            if yield_1m is not None
            else None
        )
        rows.append({
            "currency": benchmark["currency"],
            "country_code": benchmark["country_code"],
            "flag": _flag_for_country(str(benchmark["country_code"])),
            "label": benchmark["label"],
            "symbol": benchmark["symbol"],
            "date": latest_date,
            "yield": latest_yield,
            "yield_display": _format_yield(latest_yield),
            "change_5d_bp": change_5d_bp,
            "change_5d_display": _format_bp(change_5d_bp),
            "change_5d_class": _spread_color_class(change_5d_bp),
            "change_1m_bp": change_1m_bp,
            "change_1m_display": _format_bp(change_1m_bp),
            "change_1m_class": _spread_color_class(change_1m_bp),
        })

    if not rows:
        message = (
            "EODHD rejected the GBOND yield requests for the current subscription. "
            "The symbols are valid, but this API key needs GBOND/government-bond access."
            if auth_blocked
            else "Stored bond yield data is unavailable right now."
        )
        return {
            "rows": [],
            "pairs": [],
            "stats": [],
            "base_currency": YIELD_BASE_CURRENCY,
            "symbols": [benchmark["symbol"] for benchmark in benchmarks],
            "errors": failed_symbols,
            "chart_data": _build_yield_chart_data(histories_by_currency),
            "message": message,
        }

    rows_by_currency = {str(row["currency"]): row for row in rows}
    base_yield = rows_by_currency.get(YIELD_BASE_CURRENCY, {}).get("yield")
    for row in rows:
        spread = (
            (float(row["yield"]) - float(base_yield)) * 100
            if base_yield is not None
            else None
        )
        row["spread_vs_base_bp"] = spread
        row["spread_display"] = _format_bp(spread)
        row["spread_class"] = _spread_color_class(spread)
        row["spread_abs"] = abs(spread) if spread is not None else 0

    rows.sort(key=lambda row: float(row["spread_vs_base_bp"] or 0), reverse=True)

    pairs: list[dict[str, Any]] = []
    for label, left_currency, right_currency in YIELD_PAIR_DEFS:
        left = rows_by_currency.get(left_currency)
        right = rows_by_currency.get(right_currency)
        if not left or not right:
            continue
        spread = (float(left["yield"]) - float(right["yield"])) * 100
        pairs.append({
            "label": label,
            "left_currency": left_currency,
            "right_currency": right_currency,
            "spread_bp": spread,
            "spread_display": _format_bp(spread),
            "spread_class": _spread_color_class(spread),
        })

    dated_rows = [row for row in rows if row.get("date") is not None]
    latest_date = max((row["date"] for row in dated_rows), default=None)
    non_base_rows = [row for row in rows if row["currency"] != YIELD_BASE_CURRENCY]
    highest = max(non_base_rows, key=lambda row: float(row["spread_vs_base_bp"] or 0), default=None)
    lowest = min(non_base_rows, key=lambda row: float(row["spread_vs_base_bp"] or 0), default=None)
    base = rows_by_currency.get(YIELD_BASE_CURRENCY)
    stats = [
        {
            "label": f"US {maturity_label}",
            "value": base["yield_display"] if base else "N/A",
            "detail": "base benchmark",
            "class": "is-neutral",
        },
        {
            "label": "Highest vs USD",
            "value": (
                f"{highest['currency']} {highest['spread_display']}"
                if highest else "N/A"
            ),
            "detail": highest["label"] if highest else "no spread",
            "class": highest["spread_class"] if highest else "is-neutral",
        },
        {
            "label": "Lowest vs USD",
            "value": (
                f"{lowest['currency']} {lowest['spread_display']}"
                if lowest else "N/A"
            ),
            "detail": lowest["label"] if lowest else "no spread",
            "class": lowest["spread_class"] if lowest else "is-neutral",
        },
        {
            "label": "Updated",
            "value": latest_date.strftime("%b %d") if latest_date else "N/A",
            "detail": "latest EOD close",
            "class": "is-neutral",
        },
    ]

    return {
        "rows": rows,
        "pairs": pairs,
        "stats": stats,
        "base_currency": YIELD_BASE_CURRENCY,
        "symbols": [benchmark["symbol"] for benchmark in benchmarks],
        "errors": failed_symbols,
        "chart_data": _build_yield_chart_data(histories_by_currency),
        "message": "",
    }

def _repricing_curve(history: list[dict[str, Any]]) -> dict[date, float]:
    """Collapse an EODHD EOD payload to {date: close}."""
    curve: dict[date, float] = {}
    for row in history:
        if not isinstance(row, dict):
            continue
        parsed_date, value = _yield_history_point([row], 0)
        if parsed_date is not None and value is not None:
            curve[parsed_date] = value
    return curve

def _repricing_anchors(
    dates: list[date],
    lookback_days: int = REPRICING_LOOKBACK_DAYS,
) -> tuple[date, date] | None:
    """Pick (latest, prior) from dates both legs share.

    `prior` is the newest shared date at least `lookback_days` calendar days
    before `latest`, so every pair measures the same window even when one
    market was closed.
    """
    if len(dates) < 2:
        return None
    latest = dates[-1]
    target = latest - timedelta(days=lookback_days)
    earlier = [value for value in dates if value <= target]
    return (latest, earlier[-1]) if earlier else (latest, dates[0])

def _repricing_beta(
    spread_curve: dict[date, float],
    fx_curve: dict[date, float],
) -> tuple[float, float] | None:
    """OLS of daily FX % change on daily spread change, over shared dates.

    Returns (beta, residual_sigma). Univariate by design: this is the landing
    panel's sanity check on whether spot has followed rates, not the full
    fair-value model.
    """
    shared = sorted(set(spread_curve) & set(fx_curve))
    samples: list[tuple[float, float]] = []
    for previous, current in zip(shared, shared[1:], strict=False):
        if (current - previous).days > 5:
            continue  # Don't let a gap across a long holiday become one "day".
        fx_previous = fx_curve[previous]
        if not fx_previous:
            continue
        samples.append((
            spread_curve[current] - spread_curve[previous],
            (fx_curve[current] - fx_previous) / fx_previous * 100,
        ))

    if len(samples) < REPRICING_MIN_REGRESSION_POINTS:
        return None

    count = len(samples)
    mean_x = sum(x for x, _ in samples) / count
    mean_y = sum(y for _, y in samples) / count
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in samples)
    variance = sum((x - mean_x) ** 2 for x, _ in samples)
    if variance <= 0:
        return None

    beta = covariance / variance
    intercept = mean_y - beta * mean_x
    residuals = [y - (intercept + beta * x) for x, y in samples]
    sigma = (sum(value**2 for value in residuals) / (count - 2)) ** 0.5
    if sigma <= 0:
        return None
    return beta, sigma

def _repricing_regime(
    front: dict[date, float],
    long: dict[date, float],
) -> dict[str, Any]:
    """Classify the base-currency curve move as bull/bear × steepening/flattening.

    Direction alone can't tell a policy shock from a growth shock; the slope
    change is what separates them, so the panel labels the regime rather than
    leaving the reader to infer it from a bp number.
    """
    shared = sorted(set(front) & set(long))
    anchors = _repricing_anchors(shared)
    if anchors is None:
        return {"label": "", "detail": "", "class": "is-neutral"}

    latest, prior = anchors
    front_change = (front[latest] - front[prior]) * 100
    long_change = (long[latest] - long[prior]) * 100
    slope_change = long_change - front_change

    if abs(front_change) < 2 and abs(long_change) < 2:
        return {
            "label": "RANGEBOUND",
            "detail": f"{YIELD_BASE_CURRENCY} curve unchanged",
            "class": "is-neutral",
        }

    direction = "BEAR" if long_change > 0 else "BULL"
    shape = "STEEPENING" if slope_change > 0 else "FLATTENING"
    meaning = {
        ("BEAR", "FLATTENING"): "hawkish repricing",
        ("BEAR", "STEEPENING"): "growth / term premium",
        ("BULL", "FLATTENING"): "long-end demand",
        ("BULL", "STEEPENING"): "easing priced",
    }[(direction, shape)]
    return {
        "label": f"{direction} {shape}",
        "detail": f"{YIELD_BASE_CURRENCY} 2s10s {slope_change:+.0f}bp · {meaning}",
        "class": "is-negative" if direction == "BEAR" else "is-positive",
    }

async def _build_rate_repricing() -> dict[str, Any]:
    """Rank G10 pairs by how much their expected policy differential moved.

    Levels are near-constant and carry no decision value; what trades is the
    *change* in the differential and whether spot has confirmed it. Each row
    pairs the 2Y spread move with the pair's actual move and flags the gap.
    """
    currencies = [str(benchmark["currency"]) for benchmark in YIELD_BENCHMARKS]
    prefixes = {
        str(benchmark["currency"]): _yield_symbol_prefix(benchmark)
        for benchmark in YIELD_BENCHMARKS
    }
    today = _now().date()
    from_date = today - timedelta(days=REPRICING_HISTORY_DAYS)

    front_symbols = [f"{prefixes[code]}{REPRICING_FRONT_TENOR}.GBOND" for code in currencies]
    long_symbols = [f"{prefixes[code]}{REPRICING_LONG_TENOR}.GBOND" for code in currencies]
    fx_symbols = [str(pair["symbol"]) for pair in FX_PAIR_DEFS]

    payloads = (
        await _stored_histories(front_symbols + long_symbols, from_date, today)
        + await _stored_histories(fx_symbols, from_date, today, fx=True)
    )

    curves = [
        _repricing_curve(payload) if not isinstance(payload, Exception) else {}
        for payload in payloads
    ]
    split = len(currencies)
    front_by_currency = dict(zip(currencies, curves[:split], strict=True))
    long_by_currency = dict(zip(currencies, curves[split : split * 2], strict=True))
    fx_by_pair = {
        str(pair["label"]): curve
        for pair, curve in zip(FX_PAIR_DEFS, curves[split * 2 :], strict=True)
    }

    rows: list[dict[str, Any]] = []
    for pair in FX_PAIR_DEFS:
        label = str(pair["label"])
        left = str(pair["left_currency"])
        right = str(pair["right_currency"])
        left_curve = front_by_currency.get(left, {})
        right_curve = front_by_currency.get(right, {})

        # Spread only within a date both legs actually printed. Taking each
        # leg's own latest close would cross-date the spread whenever one
        # market is closed or lags.
        shared = sorted(set(left_curve) & set(right_curve))
        anchors = _repricing_anchors(shared)
        if anchors is None:
            continue
        latest, prior = anchors

        spread_curve = {
            value: (left_curve[value] - right_curve[value]) * 100 for value in shared
        }
        spread_now = spread_curve[latest]
        spread_change = spread_now - spread_curve[prior]

        fx_curve = fx_by_pair.get(label, {})
        fx_dates = sorted(fx_curve)
        fx_now = next((fx_curve[d] for d in reversed(fx_dates) if d <= latest), None)
        fx_then = next((fx_curve[d] for d in reversed(fx_dates) if d <= prior), None)
        fx_change_pct = (
            (fx_now - fx_then) / fx_then * 100
            if fx_now is not None and fx_then not in (None, 0)
            else None
        )

        residual_sigma: float | None = None
        divergence = ""
        fit = _repricing_beta(spread_curve, fx_curve)
        if fit is not None and fx_change_pct is not None:
            beta, sigma = fit
            residual_sigma = (fx_change_pct - beta * spread_change) / sigma
            if abs(residual_sigma) >= REPRICING_DIVERGENCE_SIGMA:
                divergence = (
                    f"{label.split('/')[0]} rich vs rates"
                    if residual_sigma > 0
                    else f"{label.split('/')[0]} cheap vs rates"
                )

        staleness = (today - latest).days
        rows.append({
            "label": label,
            "left_currency": left,
            "right_currency": right,
            "spread_bp": spread_now,
            "spread_display": _format_bp(spread_now),
            "change_bp": spread_change,
            "change_display": _format_bp(spread_change),
            "change_class": _spread_color_class(spread_change),
            "change_abs": abs(spread_change),
            "fx_change_pct": fx_change_pct,
            "fx_change_display": (
                f"{fx_change_pct:+.2f}%" if fx_change_pct is not None else "N/A"
            ),
            "fx_change_class": _spread_color_class(fx_change_pct),
            "residual_sigma": residual_sigma,
            "residual_display": (
                f"{residual_sigma:+.1f}σ" if residual_sigma is not None else "--"
            ),
            "residual_class": "is-warning" if divergence else "is-neutral",
            "divergence": divergence,
            "as_of": latest,
            "window_from": prior,
            "window_days": (latest - prior).days,
            "is_stale": staleness > REPRICING_STALE_DAYS,
            "stale_display": latest.strftime("%b %d") if staleness > REPRICING_STALE_DAYS else "",
        })

    # Biggest repricing first: the panel should re-rank to whatever actually
    # moved, not hold a fixed pair order that never changes.
    rows.sort(key=lambda row: float(row["change_abs"]), reverse=True)

    return {
        "rows": rows,
        "regime": _repricing_regime(
            front_by_currency.get(YIELD_BASE_CURRENCY, {}),
            long_by_currency.get(YIELD_BASE_CURRENCY, {}),
        ),
        "tenor": REPRICING_FRONT_TENOR,
        "message": "" if rows else "No overlapping yield dates across pairs.",
    }

def _now():
    from datetime import datetime

    return datetime.now(timezone.utc)


# Public entry points retain the existing calculations.
async def get_rates_research_context() -> dict[str, Any]:
    return await _build_rates_research_context()


async def get_yield_differentials(
    benchmarks: list[dict[str, Any]] | None = None, maturity_label: str = "10Y",
) -> dict[str, Any]:
    return await _build_yield_differentials(benchmarks, maturity_label)


async def get_rate_repricing() -> dict[str, Any]:
    return await _build_rate_repricing()


async def get_gbond_symbol_set() -> set[str]:
    return await _fetch_gbond_symbol_set()


def build_yield_chart_data(histories_by_currency: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return _build_yield_chart_data(histories_by_currency)


def build_maturity_benchmarks(maturity_key: str, available_symbols: set[str]) -> list[dict[str, Any]]:
    return _build_maturity_benchmarks(maturity_key, available_symbols)


def build_fx_chart_data(histories_by_pair: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return _build_fx_chart_data(histories_by_pair)

