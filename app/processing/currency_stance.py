"""Currency stance layer built from macro pressure theme indices."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope
from app.processing.macro_dataset import csv_value, json_default


STANCE_TABLES = (
    "processed.currency_stance",
    "processed.currency_stance_rankings",
)


@dataclass(frozen=True)
class CurrencyStanceConfig:
    windows_months: tuple[int, ...] = (1, 2, 3)
    bullish_threshold: float = 0.75
    mildly_bullish_threshold: float = 0.25
    mildly_bearish_threshold: float = -0.25
    bearish_threshold: float = -0.75
    trend_flat_threshold: float = 0.10


async def build_currency_stance_layer(
    output_dir: Path | str = Path("data/currency_stance"),
    config: CurrencyStanceConfig = CurrencyStanceConfig(),
) -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    async with session_scope() as session:
        await create_stance_schema(session)
        await build_currency_stance(session, config)
        await build_currency_stance_rankings(session)
        summary = await build_stance_summary(session)

        for table_name in STANCE_TABLES:
            await export_table_csv(
                session,
                table_name,
                output_path / f"{table_name.split('.')[-1]}.csv",
            )
        write_json(output_path / "currency_stance_report.json", summary)
        write_readme(output_path)

    return {
        "output_dir": str(output_path),
        "tables": list(STANCE_TABLES),
        "summary": summary,
    }


async def create_stance_schema(session: AsyncSession) -> None:
    statements = [
        "CREATE SCHEMA IF NOT EXISTS processed",
        "DROP TABLE IF EXISTS processed.currency_stance_rankings",
        "DROP TABLE IF EXISTS processed.currency_stance",
        """
        CREATE TABLE processed.currency_stance (
            stance_id bigserial PRIMARY KEY,
            date date NOT NULL,
            country_code varchar(2) NOT NULL,
            currency varchar(3) NOT NULL,
            window_months integer NOT NULL,
            inflation_score numeric(20,6),
            labor_score numeric(20,6),
            growth_score numeric(20,6),
            inflation_direction numeric(20,6),
            labor_direction numeric(20,6),
            growth_direction numeric(20,6),
            categories_available integer NOT NULL,
            positive_categories integer NOT NULL,
            negative_categories integer NOT NULL,
            aligned_category_count integer NOT NULL,
            persistence_score numeric(20,6) NOT NULL,
            recent_direction_score numeric(20,6) NOT NULL,
            overall_stance_score numeric(20,6) NOT NULL,
            overall_stance_label text NOT NULL,
            trend_label text NOT NULL,
            confidence text NOT NULL,
            meter_color text NOT NULL,
            inflation_meter_color text NOT NULL,
            labor_meter_color text NOT NULL,
            growth_meter_color text NOT NULL,
            contribution_breakdown jsonb NOT NULL,
            no_lookahead boolean NOT NULL DEFAULT true,
            processed_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (date, currency, window_months)
        )
        """,
        """
        CREATE TABLE processed.currency_stance_rankings (
            ranking_id bigserial PRIMARY KEY,
            date date NOT NULL,
            window_months integer NOT NULL,
            currency varchar(3) NOT NULL,
            country_code varchar(2) NOT NULL,
            overall_stance_score numeric(20,6) NOT NULL,
            overall_stance_label text NOT NULL,
            trend_label text NOT NULL,
            confidence text NOT NULL,
            rank_strongest integer NOT NULL,
            rank_weakest integer NOT NULL,
            percentile_rank numeric(20,6),
            meter_color text NOT NULL,
            processed_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (date, window_months, currency)
        )
        """,
    ]
    for statement in statements:
        await session.execute(text(statement))


async def build_currency_stance(
    session: AsyncSession, config: CurrencyStanceConfig
) -> None:
    values = ", ".join(f"({window})" for window in config.windows_months)
    await session.execute(
        text(
            f"""
            WITH windows(window_months) AS (VALUES {values}),
            theme_by_currency AS (
                SELECT
                    index_date,
                    currency_code,
                    CASE
                        WHEN currency_code = 'EUR'
                            AND bool_or(country_code = 'EU') THEN 'EU'
                        ELSE min(country_code)
                    END AS country_code,
                    avg(inflation_score) AS inflation_score,
                    avg(labor_score) AS labor_score,
                    avg(growth_score) AS growth_score
                FROM processed.theme_indices
                GROUP BY index_date, currency_code
            ),
            country_dim AS (
                SELECT DISTINCT country_code, currency_code
                FROM theme_by_currency
            ),
            base_grid AS (
                SELECT index_date, country_code, currency_code
                FROM theme_by_currency
            ),
            rolling AS (
                SELECT
                    base.index_date AS date,
                    base.country_code,
                    base.currency_code AS currency,
                    w.window_months,
                    avg(hist.inflation_score) AS inflation_score,
                    avg(hist.labor_score) AS labor_score,
                    avg(hist.growth_score) AS growth_score,
                    avg(hist.inflation_score) - avg(prev.inflation_score) AS inflation_direction,
                    avg(hist.labor_score) - avg(prev.labor_score) AS labor_direction,
                    avg(hist.growth_score) - avg(prev.growth_score) AS growth_direction,
                    count(hist.inflation_score) AS inflation_obs,
                    count(hist.labor_score) AS labor_obs,
                    count(hist.growth_score) AS growth_obs,
                    max(hist.index_date) AS latest_hist_date
                FROM base_grid base
                CROSS JOIN windows w
                JOIN theme_by_currency hist
                    ON hist.currency_code = base.currency_code
                    AND hist.index_date <= base.index_date
                    AND hist.index_date > base.index_date - (w.window_months || ' months')::interval
                LEFT JOIN theme_by_currency prev
                    ON prev.currency_code = base.currency_code
                    AND prev.index_date <= base.index_date - (w.window_months || ' months')::interval
                    AND prev.index_date > base.index_date - ((w.window_months * 2) || ' months')::interval
                GROUP BY base.index_date, base.country_code, base.currency_code, w.window_months
            ),
            scored AS (
                SELECT
                    *,
                    (CASE WHEN inflation_score IS NOT NULL THEN 1 ELSE 0 END
                     + CASE WHEN labor_score IS NOT NULL THEN 1 ELSE 0 END
                     + CASE WHEN growth_score IS NOT NULL THEN 1 ELSE 0 END) AS categories_available,
                    (CASE WHEN inflation_score > 0 THEN 1 ELSE 0 END
                     + CASE WHEN labor_score > 0 THEN 1 ELSE 0 END
                     + CASE WHEN growth_score > 0 THEN 1 ELSE 0 END) AS positive_categories,
                    (CASE WHEN inflation_score < 0 THEN 1 ELSE 0 END
                     + CASE WHEN labor_score < 0 THEN 1 ELSE 0 END
                     + CASE WHEN growth_score < 0 THEN 1 ELSE 0 END) AS negative_categories,
                    (
                        coalesce(inflation_score, 0)
                        + coalesce(labor_score, 0)
                        + coalesce(growth_score, 0)
                    ) / nullif(
                        (CASE WHEN inflation_score IS NOT NULL THEN 1 ELSE 0 END
                         + CASE WHEN labor_score IS NOT NULL THEN 1 ELSE 0 END
                         + CASE WHEN growth_score IS NOT NULL THEN 1 ELSE 0 END),
                        0
                    ) AS average_score,
                    (
                        coalesce(inflation_direction, 0)
                        + coalesce(labor_direction, 0)
                        + coalesce(growth_direction, 0)
                    ) / nullif(
                        (CASE WHEN inflation_direction IS NOT NULL THEN 1 ELSE 0 END
                         + CASE WHEN labor_direction IS NOT NULL THEN 1 ELSE 0 END
                         + CASE WHEN growth_direction IS NOT NULL THEN 1 ELSE 0 END),
                        0
                    ) AS recent_direction_score
                FROM rolling
            ),
            final_scores AS (
                SELECT
                    *,
                    greatest(positive_categories, negative_categories) AS aligned_category_count,
                    CASE
                        WHEN categories_available = 0 THEN 0
                        WHEN positive_categories >= negative_categories THEN positive_categories::numeric / categories_available
                        ELSE -negative_categories::numeric / categories_available
                    END AS persistence_score,
                    (
                        coalesce(average_score, 0) * 0.70
                        + coalesce(recent_direction_score, 0) * 0.20
                        + CASE
                            WHEN categories_available = 0 THEN 0
                            WHEN positive_categories >= negative_categories THEN (positive_categories::numeric / categories_available) * 0.10
                            ELSE (-negative_categories::numeric / categories_available) * 0.10
                          END
                    ) AS overall_stance_score
                FROM scored
                WHERE categories_available > 0
            )
            INSERT INTO processed.currency_stance (
                date, country_code, currency, window_months, inflation_score,
                labor_score, growth_score, inflation_direction, labor_direction,
                growth_direction, categories_available, positive_categories,
                negative_categories, aligned_category_count, persistence_score,
                recent_direction_score, overall_stance_score, overall_stance_label,
                trend_label, confidence, meter_color, inflation_meter_color,
                labor_meter_color, growth_meter_color, contribution_breakdown, no_lookahead
            )
            SELECT
                date,
                country_code,
                currency,
                window_months,
                inflation_score,
                labor_score,
                growth_score,
                inflation_direction,
                labor_direction,
                growth_direction,
                categories_available,
                positive_categories,
                negative_categories,
                aligned_category_count,
                persistence_score,
                coalesce(recent_direction_score, 0),
                overall_stance_score,
                stance_label(overall_stance_score),
                trend_label(coalesce(recent_direction_score, 0)),
                confidence_label(categories_available, aligned_category_count, inflation_obs + labor_obs + growth_obs),
                meter_color(overall_stance_score),
                meter_color(inflation_score),
                meter_color(labor_score),
                meter_color(growth_score),
                jsonb_build_object(
                    'inflation', jsonb_build_object(
                        'average_score', inflation_score,
                        'direction', inflation_direction,
                        'observations', inflation_obs,
                        'meter_color', meter_color(inflation_score)
                    ),
                    'labor', jsonb_build_object(
                        'average_score', labor_score,
                        'direction', labor_direction,
                        'observations', labor_obs,
                        'meter_color', meter_color(labor_score)
                    ),
                    'growth', jsonb_build_object(
                        'average_score', growth_score,
                        'direction', growth_direction,
                        'observations', growth_obs,
                        'meter_color', meter_color(growth_score)
                    ),
                    'persistence_score', persistence_score,
                    'aligned_category_count', aligned_category_count
                ),
                latest_hist_date <= date
            FROM final_scores
            """
            .replace("stance_label(overall_stance_score)", stance_label_case("overall_stance_score", config))
            .replace("trend_label(coalesce(recent_direction_score, 0))", trend_label_case("coalesce(recent_direction_score, 0)", config))
            .replace(
                "confidence_label(categories_available, aligned_category_count, inflation_obs + labor_obs + growth_obs)",
                confidence_label_case("categories_available", "aligned_category_count", "inflation_obs + labor_obs + growth_obs"),
            )
            .replace("meter_color(overall_stance_score)", meter_color_case("overall_stance_score"))
            .replace("meter_color(inflation_score)", meter_color_case("inflation_score"))
            .replace("meter_color(labor_score)", meter_color_case("labor_score"))
            .replace("meter_color(growth_score)", meter_color_case("growth_score"))
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX idx_currency_stance_date_window
            ON processed.currency_stance (date, window_months, overall_stance_score DESC)
            """
        )
    )


def stance_label_case(column: str, config: CurrencyStanceConfig) -> str:
    return f"""
                CASE
                    WHEN {column} >= {config.bullish_threshold} THEN 'bullish'
                    WHEN {column} >= {config.mildly_bullish_threshold} THEN 'mildly_bullish'
                    WHEN {column} <= {config.bearish_threshold} THEN 'bearish'
                    WHEN {column} <= {config.mildly_bearish_threshold} THEN 'mildly_bearish'
                    ELSE 'neutral'
                END
    """


def trend_label_case(column: str, config: CurrencyStanceConfig) -> str:
    return f"""
                CASE
                    WHEN {column} > {config.trend_flat_threshold} THEN 'improving'
                    WHEN {column} < -{config.trend_flat_threshold} THEN 'weakening'
                    ELSE 'flat'
                END
    """


def confidence_label_case(
    categories_column: str, aligned_column: str, observation_column: str
) -> str:
    return f"""
                CASE
                    WHEN {categories_column} = 3 AND {aligned_column} >= 2 AND {observation_column} >= 30 THEN 'high'
                    WHEN {categories_column} >= 2 AND {observation_column} >= 12 THEN 'medium'
                    ELSE 'low'
                END
    """


def meter_color_case(column: str) -> str:
    return f"""
                CASE
                    WHEN {column} IS NULL THEN 'orange'
                    WHEN {column} > 0.25 THEN 'green'
                    WHEN {column} < -0.25 THEN 'red'
                    ELSE 'orange'
                END
    """


async def build_currency_stance_rankings(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            INSERT INTO processed.currency_stance_rankings (
                date, window_months, currency, country_code, overall_stance_score,
                overall_stance_label, trend_label, confidence, rank_strongest,
                rank_weakest, percentile_rank, meter_color
            )
            SELECT
                date,
                window_months,
                currency,
                country_code,
                overall_stance_score,
                overall_stance_label,
                trend_label,
                confidence,
                dense_rank() OVER (
                    PARTITION BY date, window_months
                    ORDER BY overall_stance_score DESC
                ),
                dense_rank() OVER (
                    PARTITION BY date, window_months
                    ORDER BY overall_stance_score ASC
                ),
                percent_rank() OVER (
                    PARTITION BY date, window_months
                    ORDER BY overall_stance_score
                ),
                meter_color
            FROM processed.currency_stance
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX idx_currency_stance_rankings_lookup
            ON processed.currency_stance_rankings (date, window_months, rank_strongest)
            """
        )
    )


async def build_stance_summary(session: AsyncSession) -> dict[str, Any]:
    summary = await fetch_one(
        session,
        """
        SELECT
            (SELECT count(*) FROM processed.currency_stance) AS stance_rows,
            (SELECT count(*) FROM processed.currency_stance_rankings) AS ranking_rows,
            (SELECT min(date) FROM processed.currency_stance) AS first_date,
            (SELECT max(date) FROM processed.currency_stance) AS last_date
        """
    )
    latest = await fetch_all(
        session,
        """
        WITH latest_dates AS (
            SELECT window_months, max(date) AS date
            FROM processed.currency_stance
            GROUP BY window_months
        )
        SELECT r.date, r.window_months, r.currency, r.country_code,
               r.overall_stance_score, r.overall_stance_label, r.trend_label,
               r.confidence, r.rank_strongest, r.meter_color
        FROM processed.currency_stance_rankings r
        JOIN latest_dates d
            ON d.date = r.date AND d.window_months = r.window_months
        ORDER BY r.window_months, r.rank_strongest
        """
    )
    distribution = await fetch_all(
        session,
        """
        SELECT window_months, overall_stance_label, count(*) AS rows
        FROM processed.currency_stance
        GROUP BY window_months, overall_stance_label
        ORDER BY window_months, overall_stance_label
        """
    )
    confidence = await fetch_all(
        session,
        """
        SELECT window_months, confidence, count(*) AS rows
        FROM processed.currency_stance
        GROUP BY window_months, confidence
        ORDER BY window_months, confidence
        """
    )
    return {
        "summary": summary,
        "latest_rankings": latest,
        "stance_distribution": distribution,
        "confidence_distribution": confidence,
        "generated_at": datetime.utcnow().isoformat() + "Z",
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


def write_readme(output_path: Path) -> None:
    output_path.joinpath("README.md").write_text(
        "\n".join(
            [
                "# Currency Stance",
                "",
                "Generated by `python -m scripts.build_currency_stance`.",
                "",
                "This layer creates rolling 1M/2M/3M fundamental currency stance rows and dashboard-ready rankings.",
                "Meters use green for positive, orange for neutral/missing, and red for negative.",
                "",
                "Primary tables: `processed.currency_stance` and `processed.currency_stance_rankings`.",
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
