"""Historical validation for macro indices and policy signals."""

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


VALIDATION_TABLES = (
    "processed.validation_samples",
    "processed.macro_outcome_validation",
    "processed.policy_signal_validation",
    "processed.policy_rate_validation",
    "processed.stability_validation",
    "processed.fx_validation_metrics",
    "processed.validation_rolling_accuracy",
)


@dataclass(frozen=True)
class ValidationConfig:
    horizons_months: tuple[int, ...] = (1, 2, 3)
    minimum_pairs: int = 12
    rolling_window: int = 20


async def build_validation_layer(
    output_dir: Path | str = Path("data/validation"),
    config: ValidationConfig = ValidationConfig(),
) -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    async with session_scope() as session:
        await create_validation_schema(session)
        await build_validation_samples(session, config)
        await build_macro_outcome_validation(session, config)
        await build_policy_signal_validation(session, config)
        await build_policy_rate_validation(session, config)
        await build_stability_validation(session, config)
        await build_fx_validation_metrics(session)
        await build_rolling_accuracy(session, config)
        summary = await build_validation_summary(session)

        for table_name in VALIDATION_TABLES:
            await export_table_csv(
                session,
                table_name,
                output_path / f"{table_name.split('.')[-1]}.csv",
            )
        write_json(output_path / "historical_validation_report.json", summary)
        write_charts_html(output_path, summary)
        write_readme(output_path)

    return {
        "output_dir": str(output_path),
        "tables": list(VALIDATION_TABLES),
        "summary": summary,
    }


async def create_validation_schema(session: AsyncSession) -> None:
    statements = [
        "CREATE SCHEMA IF NOT EXISTS processed",
        "DROP TABLE IF EXISTS processed.validation_rolling_accuracy",
        "DROP TABLE IF EXISTS processed.fx_validation_metrics",
        "DROP TABLE IF EXISTS processed.stability_validation",
        "DROP TABLE IF EXISTS processed.policy_rate_validation",
        "DROP TABLE IF EXISTS processed.policy_signal_validation",
        "DROP TABLE IF EXISTS processed.macro_outcome_validation",
        "DROP TABLE IF EXISTS processed.validation_samples",
        """
        CREATE TABLE processed.validation_samples (
            sample_id bigserial PRIMARY KEY,
            signal_date date NOT NULL,
            country_code varchar(2) NOT NULL,
            currency_code varchar(3) NOT NULL,
            horizon_months integer NOT NULL,
            theme text NOT NULL,
            score numeric(20,6),
            policy_score numeric(20,6),
            policy_label text,
            policy_confidence text,
            outcome_release_timestamp_utc timestamptz NOT NULL,
            outcome_reference_period_start date,
            headline_target_type text NOT NULL,
            headline_indicator_key text NOT NULL,
            raw_outcome_delta numeric(20,6),
            adjusted_outcome_delta numeric(20,6),
            adjusted_outcome_direction smallint,
            score_direction smallint,
            policy_direction smallint,
            score_direction_correct boolean,
            policy_direction_correct boolean,
            confidence_bucket text,
            period_bucket text NOT NULL,
            no_lookahead boolean NOT NULL
        )
        """,
        """
        CREATE TABLE processed.macro_outcome_validation (
            metric_id bigserial PRIMARY KEY,
            country_code varchar(2) NOT NULL,
            theme text NOT NULL,
            headline_target_type text NOT NULL,
            horizon_months integer NOT NULL,
            sample_count integer NOT NULL,
            correlation numeric(20,6),
            directional_accuracy numeric(20,6),
            avg_score numeric(20,6),
            avg_adjusted_outcome_delta numeric(20,6),
            confidence_alignment numeric(20,6),
            performance_rating text NOT NULL
        )
        """,
        """
        CREATE TABLE processed.policy_signal_validation (
            metric_id bigserial PRIMARY KEY,
            country_code varchar(2) NOT NULL,
            horizon_months integer NOT NULL,
            sample_count integer NOT NULL,
            correlation numeric(20,6),
            directional_accuracy numeric(20,6),
            hawkish_precision numeric(20,6),
            dovish_precision numeric(20,6),
            avg_policy_score numeric(20,6),
            avg_adjusted_outcome_delta numeric(20,6),
            confidence_alignment numeric(20,6),
            performance_rating text NOT NULL
        )
        """,
        """
        CREATE TABLE processed.policy_rate_validation (
            metric_id bigserial PRIMARY KEY,
            country_code varchar(2) NOT NULL,
            horizon_months integer NOT NULL,
            sample_count integer NOT NULL,
            correlation numeric(20,6),
            directional_accuracy numeric(20,6),
            status text NOT NULL,
            notes text NOT NULL
        )
        """,
        """
        CREATE TABLE processed.stability_validation (
            metric_id bigserial PRIMARY KEY,
            validation_type text NOT NULL,
            country_code varchar(2) NOT NULL,
            theme text,
            horizon_months integer NOT NULL,
            period_bucket text NOT NULL,
            sample_count integer NOT NULL,
            correlation numeric(20,6),
            directional_accuracy numeric(20,6),
            stability_flag text NOT NULL
        )
        """,
        """
        CREATE TABLE processed.fx_validation_metrics (
            metric_id bigserial PRIMARY KEY,
            country_code varchar(2),
            currency_code varchar(3),
            status text NOT NULL,
            required_dataset text NOT NULL,
            notes text NOT NULL,
            processed_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE processed.validation_rolling_accuracy (
            row_id bigserial PRIMARY KEY,
            validation_type text NOT NULL,
            country_code varchar(2) NOT NULL,
            theme text,
            horizon_months integer NOT NULL,
            signal_date date NOT NULL,
            rolling_sample_count integer NOT NULL,
            rolling_directional_accuracy numeric(20,6)
        )
        """,
    ]
    for statement in statements:
        await session.execute(text(statement))


async def build_validation_samples(
    session: AsyncSession, config: ValidationConfig
) -> None:
    values = ", ".join(f"({h})" for h in config.horizons_months)
    await session.execute(
        text(
            f"""
            WITH horizons(horizon_months) AS (VALUES {values}),
            theme_scores AS (
                SELECT
                    t.index_date AS signal_date,
                    t.country_code,
                    t.currency_code,
                    h.horizon_months,
                    v.theme,
                    v.score,
                    CASE
                        WHEN v.theme = 'Inflation' THEN 'CPI'
                        WHEN v.theme = 'Labor' THEN 'UNEMPLOYMENT'
                        WHEN v.theme = 'Growth' THEN 'GDP'
                    END AS headline_target_type
                FROM processed.theme_indices t
                CROSS JOIN horizons h
                CROSS JOIN LATERAL (
                    VALUES
                        ('Inflation', t.inflation_score),
                        ('Labor', t.labor_score),
                        ('Growth', t.growth_score)
                ) AS v(theme, score)
                WHERE v.score IS NOT NULL
            ),
            matched AS (
                SELECT
                    s.*,
                    p.policy_score,
                    p.policy_label,
                    p.confidence AS policy_confidence,
                    ht.release_timestamp_utc AS outcome_release_timestamp_utc,
                    ht.reference_period_start AS outcome_reference_period_start,
                    ht.indicator_key AS headline_indicator_key,
                    (ht.actual_value - ht.prior_actual_value) AS raw_outcome_delta,
                    row_number() OVER (
                        PARTITION BY s.signal_date, s.country_code, s.theme, s.horizon_months
                        ORDER BY ht.release_timestamp_utc
                    ) AS rn
                FROM theme_scores s
                JOIN processed.policy_signals p
                    ON p.country_code = s.country_code
                    AND p.signal_date = s.signal_date
                JOIN processed.headline_targets ht
                    ON ht.country_code = s.country_code
                    AND ht.headline_target_type = s.headline_target_type
                    AND ht.release_timestamp_utc > s.signal_date::timestamptz
                    AND ht.release_timestamp_utc <= (
                        s.signal_date::timestamptz
                        + (s.horizon_months || ' months')::interval
                        + interval '45 days'
                    )
                    AND ht.prior_actual_value IS NOT NULL
            )
            INSERT INTO processed.validation_samples (
                signal_date, country_code, currency_code, horizon_months, theme,
                score, policy_score, policy_label, policy_confidence,
                outcome_release_timestamp_utc, outcome_reference_period_start,
                headline_target_type, headline_indicator_key, raw_outcome_delta,
                adjusted_outcome_delta, adjusted_outcome_direction, score_direction,
                policy_direction, score_direction_correct, policy_direction_correct,
                confidence_bucket, period_bucket, no_lookahead
            )
            SELECT
                signal_date,
                country_code,
                currency_code,
                horizon_months,
                theme,
                score,
                policy_score,
                policy_label,
                policy_confidence,
                outcome_release_timestamp_utc,
                outcome_reference_period_start,
                headline_target_type,
                headline_indicator_key,
                raw_outcome_delta,
                CASE
                    WHEN theme = 'Labor' THEN -raw_outcome_delta
                    ELSE raw_outcome_delta
                END,
                sign(CASE WHEN theme = 'Labor' THEN -raw_outcome_delta ELSE raw_outcome_delta END)::smallint,
                sign(score)::smallint,
                sign(policy_score)::smallint,
                sign(score)::smallint = sign(CASE WHEN theme = 'Labor' THEN -raw_outcome_delta ELSE raw_outcome_delta END)::smallint,
                sign(policy_score)::smallint = sign(CASE WHEN theme = 'Labor' THEN -raw_outcome_delta ELSE raw_outcome_delta END)::smallint,
                policy_confidence,
                CASE
                    WHEN signal_date < date '2020-01-01' THEN 'pre_2020'
                    ELSE 'post_2020'
                END,
                outcome_release_timestamp_utc > signal_date::timestamptz
            FROM matched
            WHERE rn = 1
              AND raw_outcome_delta IS NOT NULL
              AND sign(score) <> 0
              AND sign(policy_score) <> 0
              AND sign(CASE WHEN theme = 'Labor' THEN -raw_outcome_delta ELSE raw_outcome_delta END) <> 0
            """
        )
    )


async def build_macro_outcome_validation(
    session: AsyncSession, config: ValidationConfig
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO processed.macro_outcome_validation (
                country_code, theme, headline_target_type, horizon_months,
                sample_count, correlation, directional_accuracy, avg_score,
                avg_adjusted_outcome_delta, confidence_alignment, performance_rating
            )
            SELECT
                country_code,
                theme,
                headline_target_type,
                horizon_months,
                count(*)::integer,
                corr(score, adjusted_outcome_delta)::numeric,
                avg(CASE WHEN score_direction_correct THEN 1.0 ELSE 0.0 END)::numeric,
                avg(score)::numeric,
                avg(adjusted_outcome_delta)::numeric,
                avg(
                    CASE
                        WHEN confidence_bucket = 'high' AND score_direction_correct THEN 1.0
                        WHEN confidence_bucket = 'medium' AND score_direction_correct THEN 0.6
                        WHEN confidence_bucket = 'low' AND score_direction_correct THEN 0.25
                        WHEN confidence_bucket = 'high' THEN -1.0
                        WHEN confidence_bucket = 'medium' THEN -0.6
                        ELSE -0.25
                    END
                )::numeric,
                rating_label(count(*)::integer, corr(score, adjusted_outcome_delta), avg(CASE WHEN score_direction_correct THEN 1.0 ELSE 0.0 END))
            FROM processed.validation_samples
            GROUP BY country_code, theme, headline_target_type, horizon_months
            HAVING count(*) >= :minimum_pairs
            """
            .replace("rating_label(count(*)::integer, corr(score, adjusted_outcome_delta), avg(CASE WHEN score_direction_correct THEN 1.0 ELSE 0.0 END))", rating_case())
        ),
        {"minimum_pairs": config.minimum_pairs},
    )


async def build_policy_signal_validation(
    session: AsyncSession, config: ValidationConfig
) -> None:
    await session.execute(
        text(
            """
            WITH composite AS (
                SELECT
                    signal_date,
                    country_code,
                    horizon_months,
                    avg(policy_score) AS policy_score,
                    avg(adjusted_outcome_delta) AS composite_outcome_delta,
                    sign(avg(adjusted_outcome_delta))::smallint AS composite_outcome_direction,
                    sign(avg(policy_score))::smallint AS policy_direction,
                    max(policy_confidence) AS confidence_bucket
                FROM processed.validation_samples
                GROUP BY signal_date, country_code, horizon_months
                HAVING sign(avg(adjusted_outcome_delta)) <> 0
                   AND sign(avg(policy_score)) <> 0
            )
            INSERT INTO processed.policy_signal_validation (
                country_code, horizon_months, sample_count, correlation,
                directional_accuracy, hawkish_precision, dovish_precision,
                avg_policy_score, avg_adjusted_outcome_delta, confidence_alignment,
                performance_rating
            )
            SELECT
                country_code,
                horizon_months,
                count(*)::integer,
                corr(policy_score, composite_outcome_delta)::numeric,
                avg(CASE WHEN policy_direction = composite_outcome_direction THEN 1.0 ELSE 0.0 END)::numeric,
                avg(CASE WHEN policy_direction > 0 THEN CASE WHEN composite_outcome_direction > 0 THEN 1.0 ELSE 0.0 END END)::numeric,
                avg(CASE WHEN policy_direction < 0 THEN CASE WHEN composite_outcome_direction < 0 THEN 1.0 ELSE 0.0 END END)::numeric,
                avg(policy_score)::numeric,
                avg(composite_outcome_delta)::numeric,
                avg(
                    CASE
                        WHEN confidence_bucket = 'high' AND policy_direction = composite_outcome_direction THEN 1.0
                        WHEN confidence_bucket = 'medium' AND policy_direction = composite_outcome_direction THEN 0.6
                        WHEN confidence_bucket = 'low' AND policy_direction = composite_outcome_direction THEN 0.25
                        WHEN confidence_bucket = 'high' THEN -1.0
                        WHEN confidence_bucket = 'medium' THEN -0.6
                        ELSE -0.25
                    END
                )::numeric,
                rating_label(count(*)::integer, corr(policy_score, composite_outcome_delta), avg(CASE WHEN policy_direction = composite_outcome_direction THEN 1.0 ELSE 0.0 END))
            FROM composite
            GROUP BY country_code, horizon_months
            HAVING count(*) >= :minimum_pairs
            """
            .replace(
                "rating_label(count(*)::integer, corr(policy_score, composite_outcome_delta), avg(CASE WHEN policy_direction = composite_outcome_direction THEN 1.0 ELSE 0.0 END))",
                policy_rating_case(),
            )
        ),
        {"minimum_pairs": config.minimum_pairs},
    )


async def build_policy_rate_validation(
    session: AsyncSession, config: ValidationConfig
) -> None:
    values = ", ".join(f"({h})" for h in config.horizons_months)
    await session.execute(
        text(
            f"""
            WITH horizons(horizon_months) AS (VALUES {values}),
            policy_rate_rows AS (
                SELECT
                    o.country_code,
                    o.release_timestamp_utc,
                    o.actual_value,
                    o.actual_value - lag(o.actual_value) OVER (
                        PARTITION BY o.country_code, o.indicator_key
                        ORDER BY o.release_timestamp_utc
                    ) AS rate_delta
                FROM processed.macro_observations o
                WHERE o.is_latest IS TRUE
                  AND o.actual_value IS NOT NULL
                  AND o.primary_category = 'monetary policy'
                  AND (
                      o.indicator_key IN ('cash_rate', 'official_cash_rate', 'overnight_rate', 'policy_rate', 'fed_funds_rate')
                      OR o.indicator_key LIKE '%interest_rate%'
                      OR o.indicator_key LIKE '%policy_rate%'
                  )
            ),
            matched AS (
                SELECT
                    p.country_code,
                    h.horizon_months,
                    p.policy_score,
                    r.rate_delta,
                    row_number() OVER (
                        PARTITION BY p.signal_id, h.horizon_months
                        ORDER BY r.release_timestamp_utc
                    ) AS rn
                FROM processed.policy_signals p
                CROSS JOIN horizons h
                JOIN policy_rate_rows r
                    ON r.country_code = p.country_code
                    AND r.release_timestamp_utc > p.signal_date::timestamptz
                    AND r.release_timestamp_utc <= (
                        p.signal_date::timestamptz
                        + (h.horizon_months || ' months')::interval
                        + interval '45 days'
                    )
                WHERE r.rate_delta IS NOT NULL
                  AND sign(p.policy_score) <> 0
                  AND sign(r.rate_delta) <> 0
            )
            INSERT INTO processed.policy_rate_validation (
                country_code, horizon_months, sample_count, correlation,
                directional_accuracy, status, notes
            )
            SELECT
                country_code,
                horizon_months,
                count(*)::integer,
                corr(policy_score, rate_delta)::numeric,
                avg(CASE WHEN sign(policy_score) = sign(rate_delta) THEN 1.0 ELSE 0.0 END)::numeric,
                CASE WHEN count(*) >= :minimum_pairs THEN 'evaluated' ELSE 'insufficient_rate_change_samples' END,
                'Policy-rate validation uses stored monetary-policy rate releases only; countries without rate-change samples are not evaluated.'
            FROM matched
            WHERE rn = 1
            GROUP BY country_code, horizon_months
            HAVING count(*) > 0
            """
        ),
        {"minimum_pairs": config.minimum_pairs},
    )
    await session.execute(
        text(
            """
            INSERT INTO processed.policy_rate_validation (
                country_code, horizon_months, sample_count, status, notes
            )
            SELECT c.country_code, h.horizon_months, 0,
                   'no_policy_rate_history',
                   'No stored policy-rate change samples available for this country/horizon.'
            FROM (SELECT DISTINCT country_code FROM processed.policy_signals) c
            CROSS JOIN (SELECT DISTINCT horizon_months FROM processed.validation_samples) h
            LEFT JOIN processed.policy_rate_validation v
                ON v.country_code = c.country_code
                AND v.horizon_months = h.horizon_months
            WHERE v.metric_id IS NULL
            """
        )
    )


async def build_stability_validation(
    session: AsyncSession, config: ValidationConfig
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO processed.stability_validation (
                validation_type, country_code, theme, horizon_months, period_bucket,
                sample_count, correlation, directional_accuracy, stability_flag
            )
            SELECT
                'macro_outcome',
                country_code,
                theme,
                horizon_months,
                period_bucket,
                count(*)::integer,
                corr(score, adjusted_outcome_delta)::numeric,
                avg(CASE WHEN score_direction_correct THEN 1.0 ELSE 0.0 END)::numeric,
                CASE
                    WHEN period_bucket = 'pre_2020' AND count(*) = 0 THEN 'no_pre_2020_data'
                    WHEN count(*) < :minimum_pairs THEN 'insufficient_samples'
                    ELSE 'available'
                END
            FROM processed.validation_samples
            GROUP BY country_code, theme, horizon_months, period_bucket
            """
        ),
        {"minimum_pairs": config.minimum_pairs},
    )
    await session.execute(
        text(
            """
            INSERT INTO processed.stability_validation (
                validation_type, country_code, theme, horizon_months, period_bucket,
                sample_count, stability_flag
            )
            SELECT
                'macro_outcome',
                c.country_code,
                x.theme,
                h.horizon_months,
                'pre_2020',
                0,
                'no_pre_2020_data'
            FROM (SELECT DISTINCT country_code FROM processed.policy_signals) c
            CROSS JOIN (VALUES ('Inflation'), ('Labor'), ('Growth')) AS x(theme)
            CROSS JOIN (SELECT DISTINCT horizon_months FROM processed.validation_samples) h
            LEFT JOIN processed.stability_validation s
                ON s.country_code = c.country_code
                AND s.theme = x.theme
                AND s.horizon_months = h.horizon_months
                AND s.period_bucket = 'pre_2020'
            WHERE s.metric_id IS NULL
            """
        )
    )


async def build_fx_validation_metrics(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            INSERT INTO processed.fx_validation_metrics (
                country_code, currency_code, status, required_dataset, notes
            )
            SELECT DISTINCT
                country_code,
                currency_code,
                'blocked_missing_fx_returns',
                'Daily FX returns indexed by date and currency or currency pair',
                'No FX/market return table or file exists in the current project. Validation cannot compare hawkish/dovish signals to currency strength until FX prices are ingested.'
            FROM processed.policy_signals
            ORDER BY country_code
            """
        )
    )


async def build_rolling_accuracy(
    session: AsyncSession, config: ValidationConfig
) -> None:
    statement = """
            WITH macro_rows AS (
                SELECT
                    'macro_outcome' AS validation_type,
                    country_code,
                    theme,
                    horizon_months,
                    signal_date,
                    score_direction_correct AS is_correct
                FROM processed.validation_samples
            ),
            numbered AS (
                SELECT
                    *,
                    count(*) OVER (
                        PARTITION BY validation_type, country_code, theme, horizon_months
                        ORDER BY signal_date
                        ROWS BETWEEN __ROLLING_PRECEDING__ PRECEDING AND CURRENT ROW
                    ) AS rolling_sample_count,
                    avg(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END) OVER (
                        PARTITION BY validation_type, country_code, theme, horizon_months
                        ORDER BY signal_date
                        ROWS BETWEEN __ROLLING_PRECEDING__ PRECEDING AND CURRENT ROW
                    ) AS rolling_directional_accuracy
                FROM macro_rows
            )
            INSERT INTO processed.validation_rolling_accuracy (
                validation_type, country_code, theme, horizon_months, signal_date,
                rolling_sample_count, rolling_directional_accuracy
            )
            SELECT
                validation_type,
                country_code,
                theme,
                horizon_months,
                signal_date,
                rolling_sample_count,
                rolling_directional_accuracy
            FROM numbered
            WHERE rolling_sample_count >= least(:window_size, 5)
            """.replace("__ROLLING_PRECEDING__", str(max(config.rolling_window - 1, 0)))
    await session.execute(
        text(statement),
        {"window_size": config.rolling_window},
    )


def rating_case() -> str:
    return """
                CASE
                    WHEN count(*) < 12 THEN 'insufficient'
                    WHEN corr(score, adjusted_outcome_delta) IS NOT NULL
                         AND abs(corr(score, adjusted_outcome_delta)) >= 0.35
                         AND avg(CASE WHEN score_direction_correct THEN 1.0 ELSE 0.0 END) >= 0.55
                    THEN 'strong'
                    WHEN avg(CASE WHEN score_direction_correct THEN 1.0 ELSE 0.0 END) >= 0.52
                    THEN 'promising'
                    ELSE 'weak'
                END
    """


def policy_rating_case() -> str:
    return """
                CASE
                    WHEN count(*) < 12 THEN 'insufficient'
                    WHEN corr(policy_score, composite_outcome_delta) IS NOT NULL
                         AND abs(corr(policy_score, composite_outcome_delta)) >= 0.35
                         AND avg(CASE WHEN policy_direction = composite_outcome_direction THEN 1.0 ELSE 0.0 END) >= 0.55
                    THEN 'strong'
                    WHEN avg(CASE WHEN policy_direction = composite_outcome_direction THEN 1.0 ELSE 0.0 END) >= 0.52
                    THEN 'promising'
                    ELSE 'weak'
                END
    """


async def build_validation_summary(session: AsyncSession) -> dict[str, Any]:
    summary = await fetch_one(
        session,
        """
        SELECT
            (SELECT count(*) FROM processed.validation_samples) AS validation_samples,
            (SELECT count(*) FROM processed.macro_outcome_validation) AS macro_metric_rows,
            (SELECT count(*) FROM processed.policy_signal_validation) AS policy_metric_rows,
            (SELECT count(*) FROM processed.policy_rate_validation WHERE status = 'evaluated') AS policy_rate_metric_rows,
            (SELECT count(*) FROM processed.fx_validation_metrics WHERE status = 'blocked_missing_fx_returns') AS fx_blocked_rows
        """
    )
    best_macro = await fetch_all(
        session,
        """
        SELECT country_code, theme, headline_target_type, horizon_months,
               sample_count, correlation, directional_accuracy, performance_rating
        FROM processed.macro_outcome_validation
        ORDER BY directional_accuracy DESC NULLS LAST, abs(correlation) DESC NULLS LAST
        LIMIT 30
        """,
    )
    policy_metrics = await fetch_all(
        session,
        """
        SELECT country_code, horizon_months, sample_count, correlation,
               directional_accuracy, hawkish_precision, dovish_precision,
               confidence_alignment, performance_rating
        FROM processed.policy_signal_validation
        ORDER BY directional_accuracy DESC NULLS LAST, abs(correlation) DESC NULLS LAST
        """,
    )
    stability = await fetch_all(
        session,
        """
        SELECT period_bucket, stability_flag, count(*) AS rows
        FROM processed.stability_validation
        GROUP BY period_bucket, stability_flag
        ORDER BY period_bucket, stability_flag
        """,
    )
    confidence_vs_performance = await fetch_all(
        session,
        """
        SELECT confidence_bucket, count(*) AS samples,
               avg(CASE WHEN policy_direction_correct THEN 1.0 ELSE 0.0 END) AS policy_directional_accuracy,
               avg(CASE WHEN score_direction_correct THEN 1.0 ELSE 0.0 END) AS macro_directional_accuracy
        FROM processed.validation_samples
        GROUP BY confidence_bucket
        ORDER BY confidence_bucket
        """,
    )
    fx_status = await fetch_all(
        session,
        """
        SELECT country_code, currency_code, status, required_dataset, notes
        FROM processed.fx_validation_metrics
        ORDER BY country_code
        """,
    )
    recommendation = await build_recommendation(session)
    return {
        "summary": summary,
        "best_macro_outcome_metrics": best_macro,
        "policy_signal_metrics": policy_metrics,
        "stability_summary": stability,
        "confidence_vs_performance": confidence_vs_performance,
        "fx_validation_status": fx_status,
        "recommendation": recommendation,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


async def build_recommendation(session: AsyncSession) -> dict[str, Any]:
    row = await fetch_one(
        session,
        """
        SELECT
            avg(directional_accuracy) AS avg_policy_accuracy,
            count(*) FILTER (WHERE performance_rating IN ('strong', 'promising')) AS usable_policy_metrics,
            count(*) AS total_policy_metrics,
            (SELECT count(*) FROM processed.fx_validation_metrics WHERE status = 'blocked_missing_fx_returns') AS fx_blockers,
            (SELECT count(*) FROM processed.stability_validation WHERE period_bucket = 'pre_2020' AND stability_flag = 'no_pre_2020_data') AS pre_2020_gaps
        FROM processed.policy_signal_validation
        """
    )
    ready = (
        row.get("fx_blockers", 0) == 0
        and row.get("usable_policy_metrics", 0) >= max(1, row.get("total_policy_metrics", 0) // 2)
    )
    return {
        "ready_for_trading_layer": ready,
        "status": "needs_improvement_before_trading" if not ready else "ready_for_trading_research",
        "reason": (
            "FX return history is missing and pre-2020 stability cannot be tested from the current dataset."
            if not ready
            else "Validation metrics and FX checks passed minimum readiness gates."
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
        "best_macro_outcome_metrics": summary["best_macro_outcome_metrics"][:15],
        "policy_signal_metrics": summary["policy_signal_metrics"],
        "confidence_vs_performance": summary["confidence_vs_performance"],
    }
    output_path.joinpath("validation_charts.html").write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Macro Validation Charts</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #18212f; }}
    h1, h2 {{ margin-bottom: 8px; }}
    .bar {{ display: flex; align-items: center; gap: 8px; margin: 6px 0; }}
    .label {{ width: 220px; font-size: 12px; }}
    .track {{ width: 360px; height: 14px; background: #e7ecf3; }}
    .fill {{ height: 14px; background: #2d6cdf; }}
    pre {{ background: #f6f8fb; padding: 12px; overflow: auto; }}
  </style>
</head>
<body>
  <h1>Macro Validation Charts</h1>
  <p>Lightweight validation view generated from processed validation tables.</p>
  <h2>Best Macro Directional Accuracy</h2>
  <div id="macro-bars"></div>
  <h2>Policy Metrics</h2>
  <div id="policy-bars"></div>
  <h2>Raw Data</h2>
  <pre id="raw"></pre>
  <script>
    const data = {json.dumps(chart_data, default=json_default)};
    function renderBars(target, rows, labelFn, valueKey) {{
      const el = document.getElementById(target);
      rows.forEach(row => {{
        const value = Number(row[valueKey] || 0);
        const pct = Math.max(0, Math.min(1, value));
        const div = document.createElement('div');
        div.className = 'bar';
        div.innerHTML = `<span class="label">${{labelFn(row)}}</span><span class="track"><span class="fill" style="width:${{pct * 100}}%"></span></span><span>${{(pct * 100).toFixed(1)}}%</span>`;
        el.appendChild(div);
      }});
    }}
    renderBars('macro-bars', data.best_macro_outcome_metrics, r => `${{r.country_code}} ${{r.theme}} ${{r.horizon_months}}M`, 'directional_accuracy');
    renderBars('policy-bars', data.policy_signal_metrics, r => `${{r.country_code}} ${{r.horizon_months}}M`, 'directional_accuracy');
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
                "# Historical Validation",
                "",
                "Generated by `python -m scripts.build_validation_layer`.",
                "",
                "This layer evaluates macro indices and policy signals against future macro outcomes using only prior signal dates.",
                "FX validation is explicitly blocked until FX return history is ingested.",
                "",
                "Primary report: `historical_validation_report.json`.",
                "Charts: `validation_charts.html`.",
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
