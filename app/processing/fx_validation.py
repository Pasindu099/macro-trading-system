"""Integrate FX prices and validate policy signals against currency returns."""

from __future__ import annotations

import csv
import io
import json
import math
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope
from app.processing.macro_dataset import csv_value, json_default


FX_TABLES = (
    "processed.fx_price_history",
    "processed.fx_returns",
    "processed.fx_validation_samples",
    "processed.fx_validation_metrics",
    "processed.fx_pair_divergence_metrics",
)


@dataclass(frozen=True)
class FxInstrument:
    pair_code: str
    yahoo_symbol: str
    base_country: str | None
    quote_country: str | None
    country_code: str | None
    currency_code: str
    return_sign_for_country: int
    is_dollar_index: bool = False


FX_INSTRUMENTS: tuple[FxInstrument, ...] = (
    FxInstrument("USD_BASKET", "DX-Y.NYB", None, None, "US", "USD", 1, True),
    FxInstrument("EURUSD", "EURUSD=X", "EU", "US", "EU", "EUR", 1),
    FxInstrument("GBPUSD", "GBPUSD=X", "UK", "US", "UK", "GBP", 1),
    FxInstrument("USDJPY", "JPY=X", "US", "JP", "JP", "JPY", -1),
    FxInstrument("USDCAD", "CAD=X", "US", "CA", "CA", "CAD", -1),
    FxInstrument("AUDUSD", "AUDUSD=X", "AU", "US", "AU", "AUD", 1),
    FxInstrument("NZDUSD", "NZDUSD=X", "NZ", "US", "NZ", "NZD", 1),
    FxInstrument("USDCHF", "CHF=X", "US", "CH", "CH", "CHF", -1),
)

PAIR_INSTRUMENTS = tuple(i for i in FX_INSTRUMENTS if not i.is_dollar_index)


@dataclass(frozen=True)
class FxValidationConfig:
    start_date: date | None = None
    end_date: date | None = None
    horizons_days: tuple[int, ...] = (5, 10, 21)
    timeout_seconds: float = 30.0


async def build_fx_validation_layer(
    output_dir: Path | str = Path("data/fx_validation"),
    config: FxValidationConfig = FxValidationConfig(),
) -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    async with session_scope() as session:
        await create_fx_schema(session)
        coverage = await policy_signal_coverage(session)
        start_date = config.start_date or coverage["min_signal_date"]
        end_date = config.end_date or coverage["max_signal_date"]

        fetch_report = await fetch_and_store_fx_prices(
            session, start_date=start_date, end_date=end_date, config=config
        )
        await build_fx_returns(session, config)
        await build_fx_validation_samples(session, config)
        await build_fx_validation_metrics(session)
        await build_fx_pair_divergence_metrics(session, config)
        summary = await build_fx_summary(session, fetch_report)

        for table_name in FX_TABLES:
            await export_table_csv(
                session,
                table_name,
                output_path / f"{table_name.split('.')[-1]}.csv",
            )
        write_json(output_path / "fx_validation_report.json", summary)
        write_charts_html(output_path, summary)
        write_readme(output_path)

    return {
        "output_dir": str(output_path),
        "tables": list(FX_TABLES),
        "summary": summary,
    }


async def create_fx_schema(session: AsyncSession) -> None:
    statements = [
        "CREATE SCHEMA IF NOT EXISTS processed",
        "DROP TABLE IF EXISTS processed.fx_pair_divergence_metrics",
        "DROP TABLE IF EXISTS processed.fx_validation_metrics",
        "DROP TABLE IF EXISTS processed.fx_validation_samples",
        "DROP TABLE IF EXISTS processed.fx_returns",
        "DROP TABLE IF EXISTS processed.fx_price_history",
        """
        CREATE TABLE processed.fx_price_history (
            price_id bigserial PRIMARY KEY,
            pair_code text NOT NULL,
            yahoo_symbol text NOT NULL,
            price_date date NOT NULL,
            open_price numeric(20,8),
            high_price numeric(20,8),
            low_price numeric(20,8),
            close_price numeric(20,8),
            volume numeric(20,2),
            source text NOT NULL,
            fetched_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (pair_code, price_date)
        )
        """,
        """
        CREATE TABLE processed.fx_returns (
            return_id bigserial PRIMARY KEY,
            pair_code text NOT NULL,
            yahoo_symbol text NOT NULL,
            price_date date NOT NULL,
            close_price numeric(20,8) NOT NULL,
            daily_return numeric(20,8),
            weekly_return numeric(20,8),
            monthly_return numeric(20,8),
            UNIQUE (pair_code, price_date)
        )
        """,
        """
        CREATE TABLE processed.fx_validation_samples (
            sample_id bigserial PRIMARY KEY,
            signal_date date NOT NULL,
            country_code varchar(2) NOT NULL,
            currency_code varchar(3) NOT NULL,
            pair_code text NOT NULL,
            horizon_days integer NOT NULL,
            policy_score numeric(20,6),
            policy_label text NOT NULL,
            confidence text NOT NULL,
            entry_date date NOT NULL,
            exit_date date NOT NULL,
            entry_close numeric(20,8) NOT NULL,
            exit_close numeric(20,8) NOT NULL,
            pair_forward_return numeric(20,8) NOT NULL,
            currency_forward_return numeric(20,8) NOT NULL,
            policy_direction smallint NOT NULL,
            fx_direction smallint NOT NULL,
            direction_correct boolean NOT NULL,
            no_lookahead boolean NOT NULL
        )
        """,
        """
        CREATE TABLE processed.fx_validation_metrics (
            metric_id bigserial PRIMARY KEY,
            country_code varchar(2) NOT NULL,
            currency_code varchar(3) NOT NULL,
            pair_code text NOT NULL,
            horizon_days integer NOT NULL,
            sample_count integer NOT NULL,
            correlation numeric(20,6),
            directional_accuracy numeric(20,6),
            avg_forward_return numeric(20,8),
            avg_signal_aligned_return numeric(20,8),
            return_stddev numeric(20,8),
            sharpe_like numeric(20,6),
            high_confidence_accuracy numeric(20,6),
            medium_confidence_accuracy numeric(20,6),
            low_confidence_accuracy numeric(20,6),
            validation_status text NOT NULL
        )
        """,
        """
        CREATE TABLE processed.fx_pair_divergence_metrics (
            metric_id bigserial PRIMARY KEY,
            pair_code text NOT NULL,
            base_country varchar(2) NOT NULL,
            quote_country varchar(2) NOT NULL,
            horizon_days integer NOT NULL,
            sample_count integer NOT NULL,
            correlation numeric(20,6),
            directional_accuracy numeric(20,6),
            avg_forward_return numeric(20,8),
            avg_signal_aligned_return numeric(20,8),
            sharpe_like numeric(20,6),
            validation_status text NOT NULL
        )
        """,
    ]
    for statement in statements:
        await session.execute(text(statement))


async def policy_signal_coverage(session: AsyncSession) -> dict[str, date]:
    row = await fetch_one(
        session,
        """
        SELECT min(signal_date) AS min_signal_date, max(signal_date) AS max_signal_date
        FROM processed.policy_signals
        """,
    )
    return row


async def fetch_and_store_fx_prices(
    session: AsyncSession,
    *,
    start_date: date,
    end_date: date,
    config: FxValidationConfig,
) -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        for instrument in FX_INSTRUMENTS:
            try:
                rows = await fetch_yahoo_chart(client, instrument, start_date, end_date)
                await insert_price_rows(session, instrument, rows)
                report.append(
                    {
                        "pair_code": instrument.pair_code,
                        "symbol": instrument.yahoo_symbol,
                        "status": "fetched",
                        "rows": len(rows),
                    }
                )
            except Exception as exc:
                report.append(
                    {
                        "pair_code": instrument.pair_code,
                        "symbol": instrument.yahoo_symbol,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
        try:
            ecb_rows = await fetch_ecb_derived_fx(client, start_date, end_date)
            for pair_code, rows in ecb_rows.items():
                instrument = instrument_by_pair(pair_code)
                if instrument is None:
                    continue
                await insert_price_rows(session, instrument, rows, source="ecb_derived")
                report.append(
                    {
                        "pair_code": pair_code,
                        "symbol": "ECB_EUR_REFERENCE_RATES",
                        "status": "fetched_ecb_fallback",
                        "rows": len(rows),
                    }
                )
        except Exception as exc:
            report.append(
                {
                    "pair_code": "ECB_FALLBACK",
                    "symbol": "ECB_EUR_REFERENCE_RATES",
                    "status": "failed",
                    "error": str(exc),
                }
            )
    return report


def instrument_by_pair(pair_code: str) -> FxInstrument | None:
    for instrument in FX_INSTRUMENTS:
        if instrument.pair_code == pair_code:
            return instrument
    return None


async def fetch_yahoo_chart(
    client: httpx.AsyncClient,
    instrument: FxInstrument,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    period1 = int(datetime.combine(start_date, time.min, tzinfo=timezone.utc).timestamp())
    period2 = int(datetime.combine(end_date, time.max, tzinfo=timezone.utc).timestamp())
    symbol = quote(instrument.yahoo_symbol, safe="")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={period1}&period2={period2}&interval=1d&events=history"
    )
    response = await client.get(url)
    response.raise_for_status()
    payload = response.json()
    result = payload.get("chart", {}).get("result")
    if not result:
        error = payload.get("chart", {}).get("error")
        raise RuntimeError(f"Yahoo chart returned no result: {error}")

    chart = result[0]
    timestamps = chart.get("timestamp") or []
    quote_data = (chart.get("indicators", {}).get("quote") or [{}])[0]
    rows: list[dict[str, Any]] = []
    for idx, ts in enumerate(timestamps):
        close_price = value_at(quote_data.get("close"), idx)
        if close_price is None:
            continue
        rows.append(
            {
                "price_date": datetime.fromtimestamp(ts, timezone.utc).date(),
                "open_price": value_at(quote_data.get("open"), idx),
                "high_price": value_at(quote_data.get("high"), idx),
                "low_price": value_at(quote_data.get("low"), idx),
                "close_price": close_price,
                "volume": value_at(quote_data.get("volume"), idx),
            }
        )
    return rows


def value_at(values: list[Any] | None, idx: int) -> float | None:
    if not values or idx >= len(values):
        return None
    value = values[idx]
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return float(value)


async def insert_price_rows(
    session: AsyncSession,
    instrument: FxInstrument,
    rows: list[dict[str, Any]],
    source: str = "yahoo_chart",
) -> None:
    if not rows:
        return
    await session.execute(
        text(
            """
            INSERT INTO processed.fx_price_history (
                pair_code, yahoo_symbol, price_date, open_price, high_price,
                low_price, close_price, volume, source
            )
            VALUES (
                :pair_code, :yahoo_symbol, :price_date, :open_price, :high_price,
                :low_price, :close_price, :volume, :source
            )
            ON CONFLICT (pair_code, price_date) DO UPDATE SET
                open_price = excluded.open_price,
                high_price = excluded.high_price,
                low_price = excluded.low_price,
                close_price = excluded.close_price,
                volume = excluded.volume,
                fetched_at = now()
            """
        ),
        [
            {
                **row,
                "pair_code": instrument.pair_code,
                "yahoo_symbol": instrument.yahoo_symbol,
                "source": source,
            }
            for row in rows
        ],
    )


async def fetch_ecb_derived_fx(
    client: httpx.AsyncClient, start_date: date, end_date: date
) -> dict[str, list[dict[str, Any]]]:
    url = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"
    response = await client.get(url)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        csv_name = archive.namelist()[0]
        with archive.open(csv_name) as handle:
            text = io.TextIOWrapper(handle, encoding="utf-8")
            reader = csv.DictReader(text)
            raw_rows = [
                row
                for row in reader
                if start_date <= date.fromisoformat(row["Date"]) <= end_date
            ]

    raw_rows.sort(key=lambda row: row["Date"])
    pair_rows: dict[str, list[dict[str, Any]]] = {
        "EURUSD": [],
        "GBPUSD": [],
        "USDJPY": [],
        "USDCAD": [],
        "AUDUSD": [],
        "NZDUSD": [],
        "USDCHF": [],
    }
    usd_basket_rows: list[dict[str, Any]] = []
    prior_prices: dict[str, float] = {}
    usd_basket = 100.0

    for row in raw_rows:
        price_date = date.fromisoformat(row["Date"])
        rates = {key: parse_float(row.get(key)) for key in ["USD", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"]}
        if any(value is None for value in rates.values()):
            continue
        assert all(value is not None for value in rates.values())
        prices = {
            "EURUSD": rates["USD"],
            "GBPUSD": rates["USD"] / rates["GBP"],
            "USDJPY": rates["JPY"] / rates["USD"],
            "USDCAD": rates["CAD"] / rates["USD"],
            "AUDUSD": rates["USD"] / rates["AUD"],
            "NZDUSD": rates["USD"] / rates["NZD"],
            "USDCHF": rates["CHF"] / rates["USD"],
        }
        usd_strength_returns: list[float] = []
        for pair_code, close_price in prices.items():
            pair_rows[pair_code].append(price_row(price_date, close_price))
            previous = prior_prices.get(pair_code)
            if previous and previous != 0:
                pair_return = close_price / previous - 1
                sign_for_usd = -1 if pair_code in {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"} else 1
                usd_strength_returns.append(sign_for_usd * pair_return)
            prior_prices[pair_code] = close_price
        if usd_strength_returns:
            usd_basket *= 1 + (sum(usd_strength_returns) / len(usd_strength_returns))
        usd_basket_rows.append(price_row(price_date, usd_basket))

    pair_rows["USD_BASKET"] = usd_basket_rows
    return pair_rows


def parse_float(value: str | None) -> float | None:
    if value is None or value in {"", "N/A"}:
        return None
    return float(value)


def price_row(price_date: date, close_price: float) -> dict[str, Any]:
    return {
        "price_date": price_date,
        "open_price": close_price,
        "high_price": close_price,
        "low_price": close_price,
        "close_price": close_price,
        "volume": None,
    }


async def build_fx_returns(session: AsyncSession, config: FxValidationConfig) -> None:
    await session.execute(
        text(
            """
            WITH ordered AS (
                SELECT
                    pair_code,
                    yahoo_symbol,
                    price_date,
                    close_price,
                    lag(close_price, 1) OVER (PARTITION BY pair_code ORDER BY price_date) AS prev_close,
                    lag(close_price, 5) OVER (PARTITION BY pair_code ORDER BY price_date) AS prev_5_close,
                    lag(close_price, 21) OVER (PARTITION BY pair_code ORDER BY price_date) AS prev_21_close
                FROM processed.fx_price_history
                WHERE close_price IS NOT NULL
            )
            INSERT INTO processed.fx_returns (
                pair_code, yahoo_symbol, price_date, close_price,
                daily_return, weekly_return, monthly_return
            )
            SELECT
                pair_code,
                yahoo_symbol,
                price_date,
                close_price,
                close_price / nullif(prev_close, 0) - 1,
                close_price / nullif(prev_5_close, 0) - 1,
                close_price / nullif(prev_21_close, 0) - 1
            FROM ordered
            """
        )
    )


async def build_fx_validation_samples(
    session: AsyncSession, config: FxValidationConfig
) -> None:
    await session.execute(text(create_instrument_temp_table_sql()))
    await session.execute(
        text(
            """
            INSERT INTO fx_instruments_tmp (
                pair_code, country_code, currency_code, return_sign_for_country
            )
            VALUES (:pair_code, :country_code, :currency_code, :return_sign_for_country)
            """
        ),
        [
            {
                "pair_code": i.pair_code,
                "country_code": i.country_code,
                "currency_code": i.currency_code,
                "return_sign_for_country": i.return_sign_for_country,
            }
            for i in FX_INSTRUMENTS
            if i.country_code is not None
        ],
    )
    values = ", ".join(f"({h})" for h in config.horizons_days)
    await session.execute(
        text(
            f"""
            WITH horizons(horizon_days) AS (VALUES {values}),
            mapped AS (
                SELECT
                    p.signal_id,
                    p.signal_date,
                    p.country_code,
                    p.currency_code,
                    p.policy_score,
                    p.policy_label,
                    p.confidence,
                    h.horizon_days,
                    i.pair_code,
                    i.return_sign_for_country
                FROM processed.policy_signals p
                JOIN fx_instruments_tmp i ON i.country_code = p.country_code
                CROSS JOIN horizons h
                WHERE sign(p.policy_score) <> 0
            ),
            entry_exit AS (
                SELECT
                    m.*,
                    entry.price_date AS entry_date,
                    entry.close_price AS entry_close,
                    exit_px.price_date AS exit_date,
                    exit_px.close_price AS exit_close
                FROM mapped m
                JOIN LATERAL (
                    SELECT price_date, close_price
                    FROM processed.fx_price_history px
                    WHERE px.pair_code = m.pair_code
                      AND px.price_date >= m.signal_date
                    ORDER BY px.price_date
                    LIMIT 1
                ) entry ON true
                JOIN LATERAL (
                    SELECT price_date, close_price
                    FROM processed.fx_price_history px
                    WHERE px.pair_code = m.pair_code
                      AND px.price_date >= m.signal_date + m.horizon_days
                    ORDER BY px.price_date
                    LIMIT 1
                ) exit_px ON true
            )
            INSERT INTO processed.fx_validation_samples (
                signal_date, country_code, currency_code, pair_code, horizon_days,
                policy_score, policy_label, confidence, entry_date, exit_date,
                entry_close, exit_close, pair_forward_return, currency_forward_return,
                policy_direction, fx_direction, direction_correct, no_lookahead
            )
            SELECT
                signal_date,
                country_code,
                currency_code,
                pair_code,
                horizon_days,
                policy_score,
                policy_label,
                confidence,
                entry_date,
                exit_date,
                entry_close,
                exit_close,
                exit_close / nullif(entry_close, 0) - 1,
                return_sign_for_country * (exit_close / nullif(entry_close, 0) - 1),
                sign(policy_score)::smallint,
                sign(return_sign_for_country * (exit_close / nullif(entry_close, 0) - 1))::smallint,
                sign(policy_score)::smallint = sign(return_sign_for_country * (exit_close / nullif(entry_close, 0) - 1))::smallint,
                entry_date >= signal_date AND exit_date > entry_date
            FROM entry_exit
            WHERE exit_close IS NOT NULL
              AND entry_close IS NOT NULL
              AND sign(return_sign_for_country * (exit_close / nullif(entry_close, 0) - 1)) <> 0
            """
        )
    )


def create_instrument_temp_table_sql() -> str:
    return """
        CREATE TEMP TABLE IF NOT EXISTS fx_instruments_tmp (
            pair_code text,
            country_code varchar(2),
            currency_code varchar(3),
            return_sign_for_country integer
        ) ON COMMIT DROP
    """


async def build_fx_validation_metrics(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            INSERT INTO processed.fx_validation_metrics (
                country_code, currency_code, pair_code, horizon_days, sample_count,
                correlation, directional_accuracy, avg_forward_return,
                avg_signal_aligned_return, return_stddev, sharpe_like,
                high_confidence_accuracy, medium_confidence_accuracy,
                low_confidence_accuracy, validation_status
            )
            SELECT
                country_code,
                currency_code,
                pair_code,
                horizon_days,
                count(*)::integer,
                corr(policy_score, currency_forward_return)::numeric,
                avg(CASE WHEN direction_correct THEN 1.0 ELSE 0.0 END)::numeric,
                avg(currency_forward_return)::numeric,
                avg(policy_direction * currency_forward_return)::numeric,
                stddev_samp(policy_direction * currency_forward_return)::numeric,
                (
                    avg(policy_direction * currency_forward_return)
                    / nullif(stddev_samp(policy_direction * currency_forward_return), 0)
                )::numeric,
                avg(CASE WHEN confidence = 'high' THEN CASE WHEN direction_correct THEN 1.0 ELSE 0.0 END END)::numeric,
                avg(CASE WHEN confidence = 'medium' THEN CASE WHEN direction_correct THEN 1.0 ELSE 0.0 END END)::numeric,
                avg(CASE WHEN confidence = 'low' THEN CASE WHEN direction_correct THEN 1.0 ELSE 0.0 END END)::numeric,
                CASE
                    WHEN count(*) < 30 THEN 'insufficient_samples'
                    WHEN avg(CASE WHEN direction_correct THEN 1.0 ELSE 0.0 END) >= 0.53
                         AND avg(policy_direction * currency_forward_return) > 0
                    THEN 'promising'
                    ELSE 'weak'
                END
            FROM processed.fx_validation_samples
            GROUP BY country_code, currency_code, pair_code, horizon_days
            """
        )
    )


async def build_fx_pair_divergence_metrics(
    session: AsyncSession, config: FxValidationConfig
) -> None:
    await session.execute(text(create_pair_temp_table_sql()))
    await session.execute(
        text(
            """
            INSERT INTO fx_pairs_tmp (pair_code, base_country, quote_country)
            VALUES (:pair_code, :base_country, :quote_country)
            """
        ),
        [
            {
                "pair_code": i.pair_code,
                "base_country": i.base_country,
                "quote_country": i.quote_country,
            }
            for i in PAIR_INSTRUMENTS
            if i.base_country and i.quote_country
        ],
    )
    values = ", ".join(f"({h})" for h in config.horizons_days)
    await session.execute(
        text(
            f"""
            WITH horizons(horizon_days) AS (VALUES {values}),
            paired_signals AS (
                SELECT
                    p.pair_code,
                    p.base_country,
                    p.quote_country,
                    b.signal_date,
                    h.horizon_days,
                    b.policy_score - q.policy_score AS policy_divergence
                FROM fx_pairs_tmp p
                JOIN processed.policy_signals b ON b.country_code = p.base_country
                JOIN processed.policy_signals q
                    ON q.country_code = p.quote_country
                    AND q.signal_date = b.signal_date
                CROSS JOIN horizons h
                WHERE sign(b.policy_score - q.policy_score) <> 0
            ),
            entry_exit AS (
                SELECT
                    s.*,
                    entry.price_date AS entry_date,
                    entry.close_price AS entry_close,
                    exit_px.price_date AS exit_date,
                    exit_px.close_price AS exit_close
                FROM paired_signals s
                JOIN LATERAL (
                    SELECT price_date, close_price
                    FROM processed.fx_price_history px
                    WHERE px.pair_code = s.pair_code
                      AND px.price_date >= s.signal_date
                    ORDER BY px.price_date
                    LIMIT 1
                ) entry ON true
                JOIN LATERAL (
                    SELECT price_date, close_price
                    FROM processed.fx_price_history px
                    WHERE px.pair_code = s.pair_code
                      AND px.price_date >= s.signal_date + s.horizon_days
                    ORDER BY px.price_date
                    LIMIT 1
                ) exit_px ON true
            ),
            samples AS (
                SELECT
                    pair_code,
                    base_country,
                    quote_country,
                    horizon_days,
                    policy_divergence,
                    exit_close / nullif(entry_close, 0) - 1 AS pair_forward_return
                FROM entry_exit
                WHERE exit_close IS NOT NULL
                  AND entry_close IS NOT NULL
                  AND sign(exit_close / nullif(entry_close, 0) - 1) <> 0
            )
            INSERT INTO processed.fx_pair_divergence_metrics (
                pair_code, base_country, quote_country, horizon_days, sample_count,
                correlation, directional_accuracy, avg_forward_return,
                avg_signal_aligned_return, sharpe_like, validation_status
            )
            SELECT
                pair_code,
                base_country,
                quote_country,
                horizon_days,
                count(*)::integer,
                corr(policy_divergence, pair_forward_return)::numeric,
                avg(CASE WHEN sign(policy_divergence) = sign(pair_forward_return) THEN 1.0 ELSE 0.0 END)::numeric,
                avg(pair_forward_return)::numeric,
                avg(sign(policy_divergence) * pair_forward_return)::numeric,
                (
                    avg(sign(policy_divergence) * pair_forward_return)
                    / nullif(stddev_samp(sign(policy_divergence) * pair_forward_return), 0)
                )::numeric,
                CASE
                    WHEN count(*) < 30 THEN 'insufficient_samples'
                    WHEN avg(CASE WHEN sign(policy_divergence) = sign(pair_forward_return) THEN 1.0 ELSE 0.0 END) >= 0.53
                         AND avg(sign(policy_divergence) * pair_forward_return) > 0
                    THEN 'promising'
                    ELSE 'weak'
                END
            FROM samples
            GROUP BY pair_code, base_country, quote_country, horizon_days
            """
        )
    )


def create_pair_temp_table_sql() -> str:
    return """
        CREATE TEMP TABLE IF NOT EXISTS fx_pairs_tmp (
            pair_code text,
            base_country varchar(2),
            quote_country varchar(2)
        ) ON COMMIT DROP
    """


async def build_fx_summary(
    session: AsyncSession, fetch_report: list[dict[str, Any]]
) -> dict[str, Any]:
    summary = await fetch_one(
        session,
        """
        SELECT
            (SELECT count(*) FROM processed.fx_price_history) AS price_rows,
            (SELECT count(*) FROM processed.fx_returns) AS return_rows,
            (SELECT count(*) FROM processed.fx_validation_samples) AS validation_samples,
            (SELECT count(*) FROM processed.fx_validation_metrics) AS validation_metric_rows,
            (SELECT count(*) FROM processed.fx_pair_divergence_metrics) AS pair_divergence_metric_rows
        """
    )
    country_metrics = await fetch_all(
        session,
        """
        SELECT country_code, currency_code, pair_code, horizon_days, sample_count,
               correlation, directional_accuracy, avg_signal_aligned_return,
               sharpe_like, validation_status
        FROM processed.fx_validation_metrics
        ORDER BY validation_status DESC, directional_accuracy DESC NULLS LAST,
                 avg_signal_aligned_return DESC NULLS LAST
        """
    )
    pair_metrics = await fetch_all(
        session,
        """
        SELECT pair_code, base_country, quote_country, horizon_days, sample_count,
               correlation, directional_accuracy, avg_signal_aligned_return,
               sharpe_like, validation_status
        FROM processed.fx_pair_divergence_metrics
        ORDER BY validation_status DESC, directional_accuracy DESC NULLS LAST,
                 avg_signal_aligned_return DESC NULLS LAST
        """
    )
    confidence = await fetch_all(
        session,
        """
        SELECT confidence, horizon_days, count(*) AS samples,
               avg(CASE WHEN direction_correct THEN 1.0 ELSE 0.0 END) AS directional_accuracy,
               avg(policy_direction * currency_forward_return) AS avg_signal_aligned_return
        FROM processed.fx_validation_samples
        GROUP BY confidence, horizon_days
        ORDER BY confidence, horizon_days
        """
    )
    recommendation = await build_fx_recommendation(session)
    return {
        "summary": summary,
        "fetch_report": fetch_report,
        "country_validation_metrics": country_metrics,
        "pair_divergence_metrics": pair_metrics,
        "confidence_performance": confidence,
        "recommendation": recommendation,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


async def build_fx_recommendation(session: AsyncSession) -> dict[str, Any]:
    row = await fetch_one(
        session,
        """
        SELECT
            count(*) FILTER (WHERE validation_status = 'promising') AS promising_country_metrics,
            count(*) AS country_metrics,
            (SELECT count(*) FROM processed.fx_pair_divergence_metrics WHERE validation_status = 'promising') AS promising_pair_metrics,
            (SELECT count(*) FROM processed.fx_price_history) AS price_rows
        FROM processed.fx_validation_metrics
        """
    )
    ready = (
        row.get("price_rows", 0) > 0
        and (row.get("promising_country_metrics", 0) + row.get("promising_pair_metrics", 0)) >= 3
    )
    return {
        "ready_for_trading_layer": ready,
        "status": "ready_for_trading_research" if ready else "needs_improvement_before_trading",
        "reason": (
            "FX validation found several promising signal/return relationships."
            if ready
            else "FX validation did not find enough promising relationships under current untuned signals."
        ),
        **row,
    }


async def export_table_csv(session: AsyncSession, table_name: str, path: Path) -> None:
    rows = await fetch_all(session, f"SELECT * FROM {table_name} ORDER BY 1")
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_value(row[key]) for key in headers})


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, default=json_default, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def write_charts_html(output_path: Path, summary: dict[str, Any]) -> None:
    chart_data = {
        "country_validation_metrics": summary["country_validation_metrics"],
        "pair_divergence_metrics": summary["pair_divergence_metrics"],
        "confidence_performance": summary["confidence_performance"],
    }
    output_path.joinpath("fx_validation_charts.html").write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>FX Validation Charts</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #17202f; }}
    .bar {{ display: flex; align-items: center; gap: 8px; margin: 6px 0; }}
    .label {{ width: 220px; font-size: 12px; }}
    .track {{ width: 360px; height: 14px; background: #e8edf5; }}
    .fill {{ height: 14px; background: #2670d8; }}
    pre {{ background: #f6f8fb; padding: 12px; overflow: auto; }}
  </style>
</head>
<body>
  <h1>FX Validation Charts</h1>
  <h2>Country Signal Directional Accuracy</h2>
  <div id="country-bars"></div>
  <h2>Pair Divergence Directional Accuracy</h2>
  <div id="pair-bars"></div>
  <pre id="raw"></pre>
  <script>
    const data = {json.dumps(chart_data, default=json_default)};
    function renderBars(target, rows, labelFn) {{
      const el = document.getElementById(target);
      rows.forEach(row => {{
        const value = Number(row.directional_accuracy || 0);
        const pct = Math.max(0, Math.min(1, value));
        const div = document.createElement('div');
        div.className = 'bar';
        div.innerHTML = `<span class="label">${{labelFn(row)}}</span><span class="track"><span class="fill" style="width:${{pct * 100}}%"></span></span><span>${{(pct * 100).toFixed(1)}}%</span>`;
        el.appendChild(div);
      }});
    }}
    renderBars('country-bars', data.country_validation_metrics, r => `${{r.country_code}} ${{r.horizon_days}}d`);
    renderBars('pair-bars', data.pair_divergence_metrics, r => `${{r.pair_code}} ${{r.horizon_days}}d`);
    document.getElementById('raw').textContent = JSON.stringify(data, null, 2);
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )


def write_readme(output_path: Path) -> None:
    output_path.joinpath("README.md").write_text(
        "\n".join(
            [
                "# FX Validation",
                "",
                "Generated by `python -m scripts.build_fx_validation_layer`.",
                "",
                "This layer fetches daily FX prices, computes returns, and validates policy signals against forward FX movement.",
                "No model training or weight optimization is performed.",
                "",
                "Primary report: `fx_validation_report.json`.",
                "Charts: `fx_validation_charts.html`.",
                "",
            ]
        ),
        encoding="utf-8",
    )


async def fetch_all(
    session: AsyncSession, statement: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    result = await session.execute(text(statement), params or {})
    return [dict(row) for row in result.mappings().all()]


async def fetch_one(
    session: AsyncSession, statement: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    result = await session.execute(text(statement), params or {})
    row = result.mappings().one_or_none()
    return dict(row) if row else {}
