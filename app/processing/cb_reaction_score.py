"""CB reaction function score — mandate-weighted currency strength.

Applies each central bank's mandate weights to the existing theme indices,
producing a cross-currency z-score grounded in the Taylor Principle:

  - High inflation  → CB raises rates → currency strengthens  (+)
  - Tight labour    → CB raises rates → currency strengthens  (+)
  - Strong growth   → CB raises rates → currency strengthens  (+)

theme_indices already apply direction corrections (high unemployment = negative
labour_score), so the three components are additive throughout.

Pipeline:
    processed.theme_indices
        → CB mandate weights (per central bank)
        → cb_pressure_normalized   (weighted avg, approx [-1, 1])
        → cb_strength_score        (cross-currency z-score per date + window)
        → processed.cb_strength_score
        → processed.cb_strength_rankings
"""

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


CB_STRENGTH_TABLES = (
    "processed.cb_weight_config",
    "processed.cb_strength_score",
    "processed.cb_strength_rankings",
)


@dataclass(frozen=True)
class CBWeightEntry:
    country_code: str
    currency_code: str
    mandate_type: str
    inflation_weight: float
    labor_weight: float
    growth_weight: float
    notes: str


# Inflation coefficient >= 1.5 encodes the Taylor Principle: the CB must raise
# nominal rates by more than the inflation overshoot to anchor real rates.
# Labor weight reflects whether the CB carries an explicit employment mandate.
# Growth weight captures output-gap sensitivity in rate decisions.
DEFAULT_CB_WEIGHTS: list[CBWeightEntry] = [
    CBWeightEntry("US", "USD", "dual",      1.50, 1.00, 0.50,
                  "Fed: equal inflation+employment mandate; Taylor baseline"),
    CBWeightEntry("EU", "EUR", "single",    2.00, 0.50, 0.50,
                  "ECB: primary price stability; employment secondary"),
    CBWeightEntry("UK", "GBP", "inflation", 1.80, 0.70, 0.50,
                  "BoE: 2% CPI target primary; employment implicit"),
    CBWeightEntry("JP", "JPY", "dual",      1.80, 0.50, 0.80,
                  "BoJ: price stability + financial stability; deflation history raises growth weight"),
    CBWeightEntry("AU", "AUD", "dual",      1.50, 1.00, 0.50,
                  "RBA: inflation + full employment dual mandate"),
    CBWeightEntry("NZ", "NZD", "dual",      1.50, 1.00, 0.50,
                  "RBNZ: inflation + maximum sustainable employment"),
    CBWeightEntry("CA", "CAD", "inflation", 1.80, 0.70, 0.50,
                  "BoC: 2% midpoint inflation target primary; employment secondary"),
    CBWeightEntry("CH", "CHF", "single",    2.00, 0.30, 0.50,
                  "SNB: price stability primary; exchange rate as secondary channel"),
]


@dataclass(frozen=True)
class CBStrengthConfig:
    windows_months: tuple[int, ...] = (1, 2, 3)
    strong_threshold: float = 1.00
    mild_threshold: float = 0.35
    trend_flat_threshold: float = 0.15


async def build_cb_strength_score(
    output_dir: Path | str = Path("data/cb_strength"),
    config: CBStrengthConfig = CBStrengthConfig(),
    weights: list[CBWeightEntry] = DEFAULT_CB_WEIGHTS,
) -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    async with session_scope() as session:
        await _create_schema(session)
        await _seed_weights(session, weights)
        await _build_scores(session, config)
        await _build_rankings(session)
        summary = await _build_summary(session)

        for table_name in CB_STRENGTH_TABLES:
            await _export_csv(
                session,
                table_name,
                output_path / f"{table_name.split('.')[-1]}.csv",
            )
        _write_json(output_path / "cb_strength_report.json", summary)

    return {
        "output_dir": str(output_path),
        "tables": list(CB_STRENGTH_TABLES),
        "summary": summary,
    }


async def _create_schema(session: AsyncSession) -> None:
    statements = [
        "CREATE SCHEMA IF NOT EXISTS processed",
        "DROP TABLE IF EXISTS processed.cb_strength_rankings",
        "DROP TABLE IF EXISTS processed.cb_strength_score",
        "DROP TABLE IF EXISTS processed.cb_weight_config",
        """
        CREATE TABLE processed.cb_weight_config (
            country_code  varchar(2)   PRIMARY KEY,
            currency_code varchar(3)   NOT NULL,
            mandate_type  text         NOT NULL,
            inflation_weight numeric(10,4) NOT NULL,
            labor_weight     numeric(10,4) NOT NULL,
            growth_weight    numeric(10,4) NOT NULL,
            notes            text,
            processed_at     timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE processed.cb_strength_score (
            score_id            bigserial    PRIMARY KEY,
            date                date         NOT NULL,
            country_code        varchar(2)   NOT NULL,
            currency            varchar(3)   NOT NULL,
            window_months       integer      NOT NULL,
            inflation_score     numeric(20,6),
            labor_score         numeric(20,6),
            growth_score        numeric(20,6),
            inflation_weight    numeric(10,4) NOT NULL,
            labor_weight        numeric(10,4) NOT NULL,
            growth_weight       numeric(10,4) NOT NULL,
            mandate_type        text         NOT NULL,
            cb_pressure_raw     numeric(20,6),
            cb_pressure_normalized numeric(20,6),
            cb_strength_score   numeric(20,6) NOT NULL,
            direction_score     numeric(20,6),
            strength_label      text         NOT NULL,
            trend_label         text         NOT NULL,
            confidence          text         NOT NULL,
            categories_available integer    NOT NULL,
            no_lookahead        boolean      NOT NULL DEFAULT true,
            processed_at        timestamptz  NOT NULL DEFAULT now(),
            UNIQUE (date, currency, window_months)
        )
        """,
        """
        CREATE TABLE processed.cb_strength_rankings (
            ranking_id          bigserial    PRIMARY KEY,
            date                date         NOT NULL,
            window_months       integer      NOT NULL,
            currency            varchar(3)   NOT NULL,
            country_code        varchar(2)   NOT NULL,
            cb_strength_score   numeric(20,6) NOT NULL,
            cb_pressure_normalized numeric(20,6),
            strength_label      text         NOT NULL,
            trend_label         text         NOT NULL,
            confidence          text         NOT NULL,
            rank_strongest      integer      NOT NULL,
            rank_weakest        integer      NOT NULL,
            percentile_rank     numeric(20,6),
            processed_at        timestamptz  NOT NULL DEFAULT now(),
            UNIQUE (date, window_months, currency)
        )
        """,
    ]
    for stmt in statements:
        await session.execute(text(stmt))


async def _seed_weights(
    session: AsyncSession, weights: list[CBWeightEntry]
) -> None:
    for w in weights:
        await session.execute(
            text(
                """
                INSERT INTO processed.cb_weight_config
                    (country_code, currency_code, mandate_type,
                     inflation_weight, labor_weight, growth_weight, notes)
                VALUES
                    (:country_code, :currency_code, :mandate_type,
                     :inflation_weight, :labor_weight, :growth_weight, :notes)
                ON CONFLICT (country_code) DO UPDATE SET
                    currency_code    = EXCLUDED.currency_code,
                    mandate_type     = EXCLUDED.mandate_type,
                    inflation_weight = EXCLUDED.inflation_weight,
                    labor_weight     = EXCLUDED.labor_weight,
                    growth_weight    = EXCLUDED.growth_weight,
                    notes            = EXCLUDED.notes,
                    processed_at     = now()
                """
            ),
            {
                "country_code":    w.country_code,
                "currency_code":   w.currency_code,
                "mandate_type":    w.mandate_type,
                "inflation_weight": w.inflation_weight,
                "labor_weight":    w.labor_weight,
                "growth_weight":   w.growth_weight,
                "notes":           w.notes,
            },
        )


async def _build_scores(
    session: AsyncSession, config: CBStrengthConfig
) -> None:
    values = ", ".join(f"({w})" for w in config.windows_months)

    sql = f"""
        WITH windows(window_months) AS (VALUES {values}),

        -- Aggregate multi-country currencies (EUR: average EU sub-countries)
        theme_by_currency AS (
            SELECT
                index_date,
                currency_code,
                CASE
                    WHEN currency_code = 'EUR' AND bool_or(country_code = 'EU') THEN 'EU'
                    ELSE min(country_code)
                END AS country_code,
                avg(inflation_score) AS inflation_score,
                avg(labor_score)     AS labor_score,
                avg(growth_score)    AS growth_score
            FROM processed.theme_indices
            GROUP BY index_date, currency_code
        ),

        -- Rolling averages for current window and prior equal-length window (direction)
        rolling AS (
            SELECT
                base.index_date           AS date,
                base.country_code,
                base.currency_code        AS currency,
                w.window_months,
                avg(hist.inflation_score) AS inflation_score,
                avg(hist.labor_score)     AS labor_score,
                avg(hist.growth_score)    AS growth_score,
                avg(hist.inflation_score) - avg(prev.inflation_score) AS inflation_direction,
                avg(hist.labor_score)     - avg(prev.labor_score)     AS labor_direction,
                avg(hist.growth_score)    - avg(prev.growth_score)    AS growth_direction,
                count(hist.inflation_score) AS inflation_obs,
                count(hist.labor_score)     AS labor_obs,
                count(hist.growth_score)    AS growth_obs
            FROM (
                SELECT DISTINCT index_date, country_code, currency_code
                FROM theme_by_currency
            ) base
            CROSS JOIN windows w
            JOIN theme_by_currency hist
                ON  hist.currency_code = base.currency_code
                AND hist.index_date   <= base.index_date
                AND hist.index_date   >  base.index_date - (w.window_months || ' months')::interval
            LEFT JOIN theme_by_currency prev
                ON  prev.currency_code = base.currency_code
                AND prev.index_date   <= base.index_date - (w.window_months || ' months')::interval
                AND prev.index_date   >  base.index_date - ((w.window_months * 2) || ' months')::interval
            GROUP BY base.index_date, base.country_code, base.currency_code, w.window_months
        ),

        -- Apply CB mandate weights
        with_weights AS (
            SELECT
                r.*,
                cw.mandate_type,
                cw.inflation_weight,
                cw.labor_weight,
                cw.growth_weight,
                cw.inflation_weight + cw.labor_weight + cw.growth_weight AS total_weight,
                (CASE WHEN r.inflation_score IS NOT NULL THEN 1 ELSE 0 END
                 + CASE WHEN r.labor_score   IS NOT NULL THEN 1 ELSE 0 END
                 + CASE WHEN r.growth_score  IS NOT NULL THEN 1 ELSE 0 END
                ) AS categories_available,
                COALESCE(r.inflation_score, 0) * cw.inflation_weight
                    + COALESCE(r.labor_score, 0)   * cw.labor_weight
                    + COALESCE(r.growth_score, 0)  * cw.growth_weight
                    AS cb_pressure_raw,
                COALESCE(r.inflation_direction, 0) * cw.inflation_weight
                    + COALESCE(r.labor_direction, 0)   * cw.labor_weight
                    + COALESCE(r.growth_direction, 0)  * cw.growth_weight
                    AS direction_raw
            FROM rolling r
            JOIN processed.cb_weight_config cw ON cw.country_code = r.country_code
        ),

        -- Normalise by total weight → approx [-1, 1]
        normalised AS (
            SELECT
                *,
                cb_pressure_raw / total_weight AS cb_pressure_normalized,
                direction_raw   / total_weight AS direction_normalized
            FROM with_weights
            WHERE categories_available > 0
        ),

        -- Cross-currency z-score (relative strength on same date + window)
        zscored AS (
            SELECT
                *,
                (cb_pressure_normalized
                 - avg(cb_pressure_normalized) OVER (PARTITION BY date, window_months))
                / NULLIF(
                    stddev(cb_pressure_normalized) OVER (PARTITION BY date, window_months),
                    0
                ) AS cb_strength_score_raw
            FROM normalised
        )

        INSERT INTO processed.cb_strength_score (
            date, country_code, currency, window_months,
            inflation_score, labor_score, growth_score,
            inflation_weight, labor_weight, growth_weight,
            mandate_type, cb_pressure_raw, cb_pressure_normalized,
            cb_strength_score, direction_score,
            strength_label, trend_label, confidence,
            categories_available, no_lookahead
        )
        SELECT
            date,
            country_code,
            currency,
            window_months,
            inflation_score,
            labor_score,
            growth_score,
            inflation_weight,
            labor_weight,
            growth_weight,
            mandate_type,
            cb_pressure_raw,
            cb_pressure_normalized,
            COALESCE(cb_strength_score_raw, 0),
            direction_normalized,
            {_strength_label_case("COALESCE(cb_strength_score_raw, 0)", config)},
            {_trend_label_case("direction_normalized", config)},
            {_confidence_case()},
            categories_available,
            TRUE
        FROM zscored
    """
    await session.execute(text(sql))
    await session.execute(
        text(
            """
            CREATE INDEX idx_cb_strength_score_lookup
            ON processed.cb_strength_score (date, window_months, cb_strength_score DESC)
            """
        )
    )


async def _build_rankings(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            INSERT INTO processed.cb_strength_rankings (
                date, window_months, currency, country_code,
                cb_strength_score, cb_pressure_normalized,
                strength_label, trend_label, confidence,
                rank_strongest, rank_weakest, percentile_rank
            )
            SELECT
                date,
                window_months,
                currency,
                country_code,
                cb_strength_score,
                cb_pressure_normalized,
                strength_label,
                trend_label,
                confidence,
                dense_rank()   OVER (PARTITION BY date, window_months ORDER BY cb_strength_score DESC),
                dense_rank()   OVER (PARTITION BY date, window_months ORDER BY cb_strength_score ASC),
                percent_rank() OVER (PARTITION BY date, window_months ORDER BY cb_strength_score)
            FROM processed.cb_strength_score
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX idx_cb_strength_rankings_lookup
            ON processed.cb_strength_rankings (date, window_months, rank_strongest)
            """
        )
    )


async def _build_summary(session: AsyncSession) -> dict[str, Any]:
    summary = await _fetch_one(
        session,
        """
        SELECT
            (SELECT count(*)   FROM processed.cb_strength_score)    AS score_rows,
            (SELECT count(*)   FROM processed.cb_strength_rankings)  AS ranking_rows,
            (SELECT min(date)  FROM processed.cb_strength_score)     AS first_date,
            (SELECT max(date)  FROM processed.cb_strength_score)     AS last_date,
            (SELECT count(DISTINCT currency) FROM processed.cb_strength_score) AS currencies
        """,
    )
    weights = await _fetch_all(
        session,
        """
        SELECT country_code, currency_code, mandate_type,
               inflation_weight, labor_weight, growth_weight
        FROM processed.cb_weight_config
        ORDER BY country_code
        """,
    )
    latest = await _fetch_all(
        session,
        """
        WITH latest AS (
            SELECT window_months, max(date) AS date
            FROM processed.cb_strength_rankings
            GROUP BY window_months
        )
        SELECT r.date, r.window_months, r.currency, r.country_code,
               r.cb_strength_score, r.cb_pressure_normalized,
               r.strength_label, r.trend_label, r.confidence,
               r.rank_strongest
        FROM processed.cb_strength_rankings r
        JOIN latest l ON l.date = r.date AND l.window_months = r.window_months
        ORDER BY r.window_months, r.rank_strongest
        """,
    )
    label_dist = await _fetch_all(
        session,
        """
        SELECT window_months, strength_label, count(*) AS rows
        FROM processed.cb_strength_score
        GROUP BY window_months, strength_label
        ORDER BY window_months, strength_label
        """,
    )
    return {
        "summary": summary,
        "cb_weights": weights,
        "latest_rankings": latest,
        "label_distribution": label_dist,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


# ── Label helpers ──────────────────────────────────────────────────────────────

def _strength_label_case(col: str, config: CBStrengthConfig) -> str:
    s = config.strong_threshold
    m = config.mild_threshold
    return f"""
        CASE
            WHEN {col} >=  {s} THEN 'strong'
            WHEN {col} >=  {m} THEN 'mild_strong'
            WHEN {col} <= -{s} THEN 'weak'
            WHEN {col} <= -{m} THEN 'mild_weak'
            ELSE 'neutral'
        END"""


def _trend_label_case(col: str, config: CBStrengthConfig) -> str:
    t = config.trend_flat_threshold
    return f"""
        CASE
            WHEN {col} >  {t} THEN 'improving'
            WHEN {col} < -{t} THEN 'weakening'
            ELSE 'flat'
        END"""


def _confidence_case() -> str:
    return """
        CASE
            WHEN categories_available = 3
                 AND (inflation_obs + labor_obs + growth_obs) >= 30 THEN 'high'
            WHEN categories_available >= 2
                 AND (inflation_obs + labor_obs + growth_obs) >= 12 THEN 'medium'
            ELSE 'low'
        END"""


# ── I/O helpers ───────────────────────────────────────────────────────────────

async def _export_csv(
    session: AsyncSession, table_name: str, path: Path
) -> None:
    rows = await _fetch_all(session, f"SELECT * FROM {table_name} ORDER BY 1")
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: csv_value(row[k]) for k in headers})


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, default=json_default, indent=2, sort_keys=True),
        encoding="utf-8",
    )


async def _fetch_all(
    session: AsyncSession,
    stmt: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    result = await session.execute(text(stmt), params or {})
    return [dict(row) for row in result.mappings().all()]


async def _fetch_one(
    session: AsyncSession,
    stmt: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = await session.execute(text(stmt), params or {})
    row = result.mappings().one_or_none()
    return dict(row) if row else {}
