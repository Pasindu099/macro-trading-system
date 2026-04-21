"""Feature engineering and lead-lag mapping for the macro modeling layer."""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope
from app.processing.macro_dataset import csv_value, json_default


FEATURE_TABLES = (
    "processed.indicator_feature_map",
    "processed.indicator_features",
    "processed.headline_targets",
    "processed.lag_analysis_results",
    "processed.multicollinearity_flags",
    "processed.modeling_feature_base",
)


async def build_feature_layer(output_dir: Path | str = Path("data/features")) -> dict[str, Any]:
    """Build feature engineering tables and file exports from processed observations."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    async with session_scope() as session:
        await create_feature_schema(session)
        await build_indicator_feature_map(session)
        await build_indicator_features(session)
        await build_headline_targets(session)
        await build_lag_analysis(session)
        await build_multicollinearity_flags(session)
        await build_modeling_feature_base(session)
        summary = await build_feature_summary(session)

        for table_name in FEATURE_TABLES:
            await export_table_csv(
                session,
                table_name,
                output_path / f"{table_name.split('.')[-1]}.csv",
            )
        write_json(output_path / "feature_engineering_report.json", summary)
        write_readme(output_path)

    return {
        "output_dir": str(output_path),
        "tables": list(FEATURE_TABLES),
        "summary": summary,
    }


async def create_feature_schema(session: AsyncSession) -> None:
    statements = [
        "CREATE SCHEMA IF NOT EXISTS processed",
        "DROP TABLE IF EXISTS processed.modeling_feature_base",
        "DROP TABLE IF EXISTS processed.multicollinearity_flags",
        "DROP TABLE IF EXISTS processed.lag_analysis_results",
        "DROP TABLE IF EXISTS processed.headline_targets",
        "DROP TABLE IF EXISTS processed.indicator_features",
        "DROP TABLE IF EXISTS processed.indicator_feature_map",
        """
        CREATE TABLE processed.indicator_feature_map (
            indicator_id integer PRIMARY KEY,
            country_code varchar(2) NOT NULL,
            currency_code varchar(3) NOT NULL,
            indicator_key text NOT NULL,
            indicator_name text NOT NULL,
            macro_theme text NOT NULL,
            primary_category text NOT NULL,
            frequency text NOT NULL,
            release_lag_days_median numeric(10,2),
            release_lag_days_avg numeric(10,2),
            is_headline_indicator boolean NOT NULL,
            is_sub_indicator boolean NOT NULL,
            headline_target_type text,
            candidate_for_targets text[] NOT NULL,
            actual_count integer NOT NULL,
            first_release_at_utc timestamptz,
            last_release_at_utc timestamptz,
            processed_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE processed.indicator_features (
            feature_row_id bigserial PRIMARY KEY,
            source_release_id bigint NOT NULL,
            indicator_id integer NOT NULL,
            country_code varchar(2) NOT NULL,
            currency_code varchar(3) NOT NULL,
            indicator_key text NOT NULL,
            indicator_name text NOT NULL,
            macro_theme text NOT NULL,
            frequency text NOT NULL,
            is_headline_indicator boolean NOT NULL,
            is_sub_indicator boolean NOT NULL,
            reference_period_start date,
            release_timestamp_utc timestamptz NOT NULL,
            release_date_utc date NOT NULL,
            actual_value numeric(20,6),
            previous_observed_value numeric(20,6),
            mom_change numeric(20,6),
            yoy_change numeric(20,6),
            rolling_avg_3 numeric(20,6),
            rolling_avg_6 numeric(20,6),
            expanding_z_score numeric(20,6),
            momentum numeric(20,6),
            trend_value numeric(20,6),
            deviation_from_trend numeric(20,6),
            surprise_vs_rolling_mean numeric(20,6),
            surprise_vs_previous numeric(20,6),
            standardized_surprise_score numeric(20,6),
            feature_quality_flags text[] NOT NULL,
            processed_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (source_release_id)
        )
        """,
        """
        CREATE TABLE processed.headline_targets (
            target_row_id bigserial PRIMARY KEY,
            source_release_id bigint NOT NULL,
            indicator_id integer NOT NULL,
            country_code varchar(2) NOT NULL,
            currency_code varchar(3) NOT NULL,
            headline_target_type text NOT NULL,
            indicator_key text NOT NULL,
            indicator_name text NOT NULL,
            reference_period_start date,
            release_timestamp_utc timestamptz NOT NULL,
            actual_value numeric(20,6),
            prior_actual_value numeric(20,6),
            direction_target smallint,
            direction_label text,
            momentum_value numeric(20,6),
            prior_momentum_value numeric(20,6),
            acceleration_target smallint,
            acceleration_label text,
            binary_up_target smallint,
            target_quality_flags text[] NOT NULL,
            processed_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (source_release_id)
        )
        """,
        """
        CREATE TABLE processed.lag_analysis_results (
            lag_result_id bigserial PRIMARY KEY,
            country_code varchar(2) NOT NULL,
            currency_code varchar(3) NOT NULL,
            headline_target_type text NOT NULL,
            headline_indicator_id integer NOT NULL,
            headline_indicator_key text NOT NULL,
            candidate_indicator_id integer NOT NULL,
            candidate_indicator_key text NOT NULL,
            candidate_indicator_name text NOT NULL,
            candidate_macro_theme text NOT NULL,
            lag_months integer NOT NULL,
            observation_pairs integer NOT NULL,
            correlation numeric(20,6),
            abs_correlation numeric(20,6),
            is_best_lag boolean NOT NULL DEFAULT false,
            relationship_strength text NOT NULL,
            processed_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE processed.multicollinearity_flags (
            flag_id bigserial PRIMARY KEY,
            country_code varchar(2) NOT NULL,
            indicator_id_a integer NOT NULL,
            indicator_key_a text NOT NULL,
            indicator_id_b integer NOT NULL,
            indicator_key_b text NOT NULL,
            feature_name text NOT NULL,
            observation_pairs integer NOT NULL,
            correlation numeric(20,6) NOT NULL,
            abs_correlation numeric(20,6) NOT NULL,
            recommended_action text NOT NULL,
            processed_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE processed.modeling_feature_base (
            modeling_row_id bigserial PRIMARY KEY,
            target_row_id bigint NOT NULL,
            target_release_timestamp_utc timestamptz NOT NULL,
            target_reference_period_start date,
            country_code varchar(2) NOT NULL,
            currency_code varchar(3) NOT NULL,
            headline_target_type text NOT NULL,
            headline_indicator_key text NOT NULL,
            candidate_indicator_id integer NOT NULL,
            candidate_indicator_key text NOT NULL,
            candidate_macro_theme text NOT NULL,
            best_lag_months integer,
            lag_correlation numeric(20,6),
            feature_release_timestamp_utc timestamptz NOT NULL,
            feature_reference_period_start date,
            actual_value numeric(20,6),
            mom_change numeric(20,6),
            yoy_change numeric(20,6),
            rolling_avg_3 numeric(20,6),
            rolling_avg_6 numeric(20,6),
            expanding_z_score numeric(20,6),
            momentum numeric(20,6),
            deviation_from_trend numeric(20,6),
            standardized_surprise_score numeric(20,6),
            direction_target smallint,
            acceleration_target smallint,
            binary_up_target smallint,
            no_lookahead boolean NOT NULL,
            processed_at timestamptz NOT NULL DEFAULT now()
        )
        """,
    ]
    for statement in statements:
        await session.execute(text(statement))


async def build_indicator_feature_map(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            INSERT INTO processed.indicator_feature_map (
                indicator_id, country_code, currency_code, indicator_key, indicator_name,
                macro_theme, primary_category, frequency, release_lag_days_median,
                release_lag_days_avg, is_headline_indicator, is_sub_indicator,
                headline_target_type, candidate_for_targets, actual_count,
                first_release_at_utc, last_release_at_utc
            )
            SELECT
                m.indicator_id,
                m.country_code,
                m.currency_code,
                m.indicator_key,
                m.indicator_name,
                CASE
                    WHEN m.primary_category = 'inflation'
                         OR m.secondary_categories && ARRAY['Inflation']::text[]
                    THEN 'Inflation'
                    WHEN m.primary_category = 'labor'
                         OR m.secondary_categories && ARRAY['Labor']::text[]
                    THEN 'Labor'
                    WHEN m.primary_category IN ('growth', 'housing', 'trade', 'sentiment')
                         OR m.secondary_categories && ARRAY['Growth', 'Housing', 'Trade', 'Sentiment']::text[]
                    THEN 'Growth'
                    ELSE 'Other'
                END,
                m.primary_category,
                m.frequency,
                lag_stats.release_lag_days_median,
                lag_stats.release_lag_days_avg,
                m.is_headline_indicator,
                m.is_sub_indicator,
                CASE
                    WHEN m.indicator_key IN ('cpi_headline_yoy', 'cpi_headline_mom', 'cpi_headline_qoq')
                    THEN 'CPI'
                    WHEN m.indicator_key = 'unemployment_rate'
                    THEN 'UNEMPLOYMENT'
                    WHEN m.indicator_key IN ('gdp_qoq', 'gdp_yoy', 'gdp_mom')
                         OR m.indicator_key LIKE 'gdp_%'
                    THEN 'GDP'
                    ELSE NULL
                END,
                CASE
                    WHEN m.primary_category = 'inflation'
                         OR m.secondary_categories && ARRAY['Inflation']::text[]
                    THEN ARRAY['CPI']::text[]
                    WHEN m.primary_category = 'labor'
                         OR m.secondary_categories && ARRAY['Labor']::text[]
                    THEN ARRAY['UNEMPLOYMENT', 'CPI']::text[]
                    WHEN m.primary_category IN ('growth', 'housing', 'trade', 'sentiment')
                         OR m.secondary_categories && ARRAY['Growth', 'Housing', 'Trade', 'Sentiment']::text[]
                    THEN ARRAY['GDP', 'CPI', 'UNEMPLOYMENT']::text[]
                    ELSE ARRAY[]::text[]
                END,
                m.actual_count,
                m.first_release_at_utc,
                m.last_release_at_utc
            FROM processed.indicator_metadata m
            LEFT JOIN LATERAL (
                SELECT
                    percentile_cont(0.5) WITHIN GROUP (
                        ORDER BY (o.release_date_utc - o.reference_period_start)
                    )::numeric AS release_lag_days_median,
                    avg(o.release_date_utc - o.reference_period_start)::numeric AS release_lag_days_avg
                FROM processed.macro_observations o
                WHERE o.indicator_id = m.indicator_id
                  AND o.reference_period_start IS NOT NULL
            ) lag_stats ON true
            """
        )
    )


async def build_indicator_features(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            WITH ordered AS (
                SELECT
                    o.*,
                    fmap.macro_theme,
                    row_number() OVER (
                        PARTITION BY o.indicator_id
                        ORDER BY o.reference_period_start NULLS LAST, o.release_timestamp_utc, o.source_release_id
                    ) AS rn,
                    CASE
                        WHEN o.frequency = 'weekly' THEN 52
                        WHEN o.frequency = 'quarterly' THEN 4
                        WHEN o.frequency IN ('annual', 'yearly') THEN 1
                        WHEN o.frequency = 'daily' THEN 252
                        ELSE 12
                    END AS seasonal_lag,
                    lag(o.actual_value) OVER (
                        PARTITION BY o.indicator_id
                        ORDER BY o.reference_period_start NULLS LAST, o.release_timestamp_utc, o.source_release_id
                    ) AS lag_1_value,
                    lag(o.actual_value) OVER (
                        PARTITION BY o.indicator_id
                        ORDER BY o.reference_period_start NULLS LAST, o.release_timestamp_utc, o.source_release_id
                    ) AS previous_observed_value,
                    avg(o.actual_value) OVER (
                        PARTITION BY o.indicator_id
                        ORDER BY o.reference_period_start NULLS LAST, o.release_timestamp_utc, o.source_release_id
                        ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
                    ) AS rolling_avg_3,
                    avg(o.actual_value) OVER (
                        PARTITION BY o.indicator_id
                        ORDER BY o.reference_period_start NULLS LAST, o.release_timestamp_utc, o.source_release_id
                        ROWS BETWEEN 5 PRECEDING AND CURRENT ROW
                    ) AS rolling_avg_6,
                    avg(o.actual_value) OVER (
                        PARTITION BY o.indicator_id
                        ORDER BY o.reference_period_start NULLS LAST, o.release_timestamp_utc, o.source_release_id
                        ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING
                    ) AS prior_rolling_avg_6,
                    avg(o.actual_value) OVER (
                        PARTITION BY o.indicator_id
                        ORDER BY o.reference_period_start NULLS LAST, o.release_timestamp_utc, o.source_release_id
                        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                    ) AS prior_expanding_avg,
                    stddev_samp(o.actual_value) OVER (
                        PARTITION BY o.indicator_id
                        ORDER BY o.reference_period_start NULLS LAST, o.release_timestamp_utc, o.source_release_id
                        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                    ) AS prior_expanding_stddev,
                    stddev_samp(o.actual_value) OVER (
                        PARTITION BY o.indicator_id
                        ORDER BY o.reference_period_start NULLS LAST, o.release_timestamp_utc, o.source_release_id
                        ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING
                    ) AS prior_rolling_stddev_6
                FROM processed.macro_observations o
                JOIN processed.indicator_feature_map fmap ON fmap.indicator_id = o.indicator_id
                WHERE o.actual_value IS NOT NULL
                  AND o.is_latest IS TRUE
            ),
            enriched AS (
                SELECT
                    o.*,
                    y.actual_value AS lag_yoy_value,
                    (o.actual_value - o.lag_1_value) AS mom_change,
                    (o.actual_value - y.actual_value) AS yoy_change
                FROM ordered o
                LEFT JOIN ordered y
                    ON y.indicator_id = o.indicator_id
                    AND y.rn = o.rn - o.seasonal_lag
            ),
            final_features AS (
                SELECT
                    e.*,
                    lag(e.mom_change) OVER (
                        PARTITION BY e.indicator_id
                        ORDER BY e.reference_period_start NULLS LAST, e.release_timestamp_utc, e.source_release_id
                    ) AS prior_mom_change
                FROM enriched e
            )
            INSERT INTO processed.indicator_features (
                source_release_id, indicator_id, country_code, currency_code, indicator_key,
                indicator_name, macro_theme, frequency, is_headline_indicator, is_sub_indicator,
                reference_period_start, release_timestamp_utc, release_date_utc, actual_value,
                previous_observed_value, mom_change, yoy_change, rolling_avg_3, rolling_avg_6,
                expanding_z_score, momentum, trend_value, deviation_from_trend,
                surprise_vs_rolling_mean, surprise_vs_previous, standardized_surprise_score,
                feature_quality_flags
            )
            SELECT
                source_release_id,
                indicator_id,
                country_code,
                currency_code,
                indicator_key,
                indicator_name,
                macro_theme,
                frequency,
                is_headline_indicator,
                is_sub_indicator,
                reference_period_start,
                release_timestamp_utc,
                release_date_utc,
                actual_value,
                previous_observed_value,
                mom_change,
                yoy_change,
                rolling_avg_3,
                rolling_avg_6,
                CASE
                    WHEN prior_expanding_stddev IS NOT NULL AND prior_expanding_stddev <> 0
                    THEN (actual_value - prior_expanding_avg) / prior_expanding_stddev
                END,
                mom_change - prior_mom_change,
                prior_rolling_avg_6,
                actual_value - prior_rolling_avg_6,
                actual_value - prior_rolling_avg_6,
                actual_value - previous_observed_value,
                CASE
                    WHEN prior_rolling_stddev_6 IS NOT NULL AND prior_rolling_stddev_6 <> 0
                    THEN ((actual_value - prior_rolling_avg_6) / prior_rolling_stddev_6)
                    WHEN prior_expanding_stddev IS NOT NULL AND prior_expanding_stddev <> 0
                    THEN ((actual_value - prior_expanding_avg) / prior_expanding_stddev)
                END,
                array_remove(ARRAY[
                    CASE WHEN previous_observed_value IS NULL THEN 'insufficient_prior_history' END,
                    CASE WHEN lag_yoy_value IS NULL THEN 'insufficient_seasonal_history' END,
                    CASE WHEN prior_expanding_stddev IS NULL OR prior_expanding_stddev = 0 THEN 'z_score_unavailable' END,
                    CASE WHEN prior_rolling_avg_6 IS NULL THEN 'surprise_proxy_short_history' END
                ], NULL)::text[]
            FROM final_features
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX idx_indicator_features_lookup
            ON processed.indicator_features (
                country_code, indicator_key, release_timestamp_utc, reference_period_start
            )
            """
        )
    )


async def build_headline_targets(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            WITH headline_features AS (
                SELECT f.*, fmap.headline_target_type
                FROM processed.indicator_features f
                JOIN processed.indicator_feature_map fmap ON fmap.indicator_id = f.indicator_id
                WHERE fmap.headline_target_type IN ('CPI', 'UNEMPLOYMENT', 'GDP')
            ),
            target_calc AS (
                SELECT
                    h.*,
                    lag(h.actual_value) OVER (
                        PARTITION BY h.indicator_id
                        ORDER BY h.reference_period_start NULLS LAST, h.release_timestamp_utc, h.source_release_id
                    ) AS prior_actual_value,
                    lag(h.momentum) OVER (
                        PARTITION BY h.indicator_id
                        ORDER BY h.reference_period_start NULLS LAST, h.release_timestamp_utc, h.source_release_id
                    ) AS prior_momentum_value
                FROM headline_features h
            )
            INSERT INTO processed.headline_targets (
                source_release_id, indicator_id, country_code, currency_code,
                headline_target_type, indicator_key, indicator_name,
                reference_period_start, release_timestamp_utc, actual_value,
                prior_actual_value, direction_target, direction_label,
                momentum_value, prior_momentum_value, acceleration_target,
                acceleration_label, binary_up_target, target_quality_flags
            )
            SELECT
                source_release_id,
                indicator_id,
                country_code,
                currency_code,
                headline_target_type,
                indicator_key,
                indicator_name,
                reference_period_start,
                release_timestamp_utc,
                actual_value,
                prior_actual_value,
                CASE
                    WHEN prior_actual_value IS NULL THEN NULL
                    WHEN actual_value > prior_actual_value THEN 1
                    WHEN actual_value < prior_actual_value THEN -1
                    ELSE 0
                END,
                CASE
                    WHEN prior_actual_value IS NULL THEN NULL
                    WHEN actual_value > prior_actual_value THEN 'up'
                    WHEN actual_value < prior_actual_value THEN 'down'
                    ELSE 'flat'
                END,
                momentum,
                prior_momentum_value,
                CASE
                    WHEN prior_momentum_value IS NULL OR momentum IS NULL THEN NULL
                    WHEN momentum > prior_momentum_value THEN 1
                    WHEN momentum < prior_momentum_value THEN -1
                    ELSE 0
                END,
                CASE
                    WHEN prior_momentum_value IS NULL OR momentum IS NULL THEN NULL
                    WHEN momentum > prior_momentum_value THEN 'accelerating'
                    WHEN momentum < prior_momentum_value THEN 'decelerating'
                    ELSE 'flat'
                END,
                CASE
                    WHEN prior_actual_value IS NULL THEN NULL
                    WHEN actual_value > prior_actual_value THEN 1
                    ELSE 0
                END,
                array_remove(ARRAY[
                    CASE WHEN prior_actual_value IS NULL THEN 'direction_target_short_history' END,
                    CASE WHEN momentum IS NULL OR prior_momentum_value IS NULL THEN 'acceleration_target_short_history' END
                ], NULL)::text[]
            FROM target_calc
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX idx_headline_targets_lookup
            ON processed.headline_targets (
                country_code, headline_target_type, release_timestamp_utc
            )
            """
        )
    )


async def build_lag_analysis(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            WITH candidate_pairs AS (
                SELECT
                    t.country_code,
                    t.currency_code,
                    t.headline_target_type,
                    t.indicator_id AS headline_indicator_id,
                    t.indicator_key AS headline_indicator_key,
                    f.indicator_id AS candidate_indicator_id,
                    f.indicator_key AS candidate_indicator_key,
                    f.indicator_name AS candidate_indicator_name,
                    f.macro_theme AS candidate_macro_theme,
                    floor(
                        (
                            (date_part('year', t.reference_period_start)::int - date_part('year', f.reference_period_start)::int) * 12
                            + (date_part('month', t.reference_period_start)::int - date_part('month', f.reference_period_start)::int)
                        )
                    )::int AS lag_months,
                    f.standardized_surprise_score AS candidate_signal,
                    t.actual_value AS headline_value
                FROM processed.headline_targets t
                JOIN processed.indicator_features f
                    ON f.country_code = t.country_code
                    AND f.indicator_id <> t.indicator_id
                    AND f.release_timestamp_utc <= t.release_timestamp_utc
                    AND f.reference_period_start IS NOT NULL
                    AND t.reference_period_start IS NOT NULL
                JOIN processed.indicator_feature_map fmap
                    ON fmap.indicator_id = f.indicator_id
                    AND t.headline_target_type = ANY(fmap.candidate_for_targets)
                WHERE t.actual_value IS NOT NULL
                  AND f.standardized_surprise_score IS NOT NULL
            ),
            correlations AS (
                SELECT
                    country_code,
                    currency_code,
                    headline_target_type,
                    headline_indicator_id,
                    headline_indicator_key,
                    candidate_indicator_id,
                    candidate_indicator_key,
                    candidate_indicator_name,
                    candidate_macro_theme,
                    lag_months,
                    count(*)::integer AS observation_pairs,
                    corr(candidate_signal, headline_value)::numeric AS correlation,
                    abs(corr(candidate_signal, headline_value))::numeric AS abs_correlation
                FROM candidate_pairs
                WHERE lag_months BETWEEN 0 AND 6
                GROUP BY country_code, currency_code, headline_target_type,
                         headline_indicator_id, headline_indicator_key,
                         candidate_indicator_id, candidate_indicator_key,
                         candidate_indicator_name, candidate_macro_theme, lag_months
                HAVING count(*) >= 8
            ),
            ranked AS (
                SELECT
                    c.*,
                    row_number() OVER (
                        PARTITION BY country_code, headline_target_type,
                                     headline_indicator_id, candidate_indicator_id
                        ORDER BY abs_correlation DESC NULLS LAST, observation_pairs DESC
                    ) AS rank_for_pair
                FROM correlations c
                WHERE correlation IS NOT NULL
            )
            INSERT INTO processed.lag_analysis_results (
                country_code, currency_code, headline_target_type, headline_indicator_id,
                headline_indicator_key, candidate_indicator_id, candidate_indicator_key,
                candidate_indicator_name, candidate_macro_theme, lag_months,
                observation_pairs, correlation, abs_correlation, is_best_lag,
                relationship_strength
            )
            SELECT
                country_code,
                currency_code,
                headline_target_type,
                headline_indicator_id,
                headline_indicator_key,
                candidate_indicator_id,
                candidate_indicator_key,
                candidate_indicator_name,
                candidate_macro_theme,
                lag_months,
                observation_pairs,
                correlation,
                abs_correlation,
                rank_for_pair = 1,
                CASE
                    WHEN abs_correlation >= 0.70 THEN 'strong'
                    WHEN abs_correlation >= 0.45 THEN 'moderate'
                    WHEN abs_correlation >= 0.25 THEN 'weak'
                    ELSE 'very_weak'
                END
            FROM ranked
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX idx_lag_analysis_best
            ON processed.lag_analysis_results (
                country_code, headline_target_type, is_best_lag, abs_correlation DESC
            )
            """
        )
    )


async def build_multicollinearity_flags(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            WITH paired AS (
                SELECT
                    a.country_code,
                    a.indicator_id AS indicator_id_a,
                    a.indicator_key AS indicator_key_a,
                    b.indicator_id AS indicator_id_b,
                    b.indicator_key AS indicator_key_b,
                    a.release_date_utc,
                    a.standardized_surprise_score AS value_a,
                    b.standardized_surprise_score AS value_b
                FROM processed.indicator_features a
                JOIN processed.indicator_features b
                    ON b.country_code = a.country_code
                    AND b.release_date_utc = a.release_date_utc
                    AND b.indicator_id > a.indicator_id
                WHERE a.standardized_surprise_score IS NOT NULL
                  AND b.standardized_surprise_score IS NOT NULL
            ),
            correlations AS (
                SELECT
                    country_code,
                    indicator_id_a,
                    indicator_key_a,
                    indicator_id_b,
                    indicator_key_b,
                    'standardized_surprise_score' AS feature_name,
                    count(*)::integer AS observation_pairs,
                    corr(value_a, value_b)::numeric AS correlation,
                    abs(corr(value_a, value_b))::numeric AS abs_correlation
                FROM paired
                GROUP BY country_code, indicator_id_a, indicator_key_a,
                         indicator_id_b, indicator_key_b
                HAVING count(*) >= 12 AND abs(corr(value_a, value_b)) >= 0.85
            )
            INSERT INTO processed.multicollinearity_flags (
                country_code, indicator_id_a, indicator_key_a, indicator_id_b,
                indicator_key_b, feature_name, observation_pairs, correlation,
                abs_correlation, recommended_action
            )
            SELECT
                country_code,
                indicator_id_a,
                indicator_key_a,
                indicator_id_b,
                indicator_key_b,
                feature_name,
                observation_pairs,
                correlation,
                abs_correlation,
                'review_before_modeling_keep_higher_importance_or_broader_history'
            FROM correlations
            ORDER BY abs_correlation DESC
            """
        )
    )


async def build_modeling_feature_base(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            WITH best_lag AS (
                SELECT *
                FROM processed.lag_analysis_results
                WHERE is_best_lag IS TRUE
            ),
            candidate AS (
                SELECT
                    t.target_row_id,
                    t.release_timestamp_utc AS target_release_timestamp_utc,
                    t.reference_period_start AS target_reference_period_start,
                    t.country_code,
                    t.currency_code,
                    t.headline_target_type,
                    t.indicator_key AS headline_indicator_key,
                    f.indicator_id AS candidate_indicator_id,
                    f.indicator_key AS candidate_indicator_key,
                    f.macro_theme AS candidate_macro_theme,
                    bl.lag_months AS best_lag_months,
                    bl.correlation AS lag_correlation,
                    f.release_timestamp_utc AS feature_release_timestamp_utc,
                    f.reference_period_start AS feature_reference_period_start,
                    f.actual_value,
                    f.mom_change,
                    f.yoy_change,
                    f.rolling_avg_3,
                    f.rolling_avg_6,
                    f.expanding_z_score,
                    f.momentum,
                    f.deviation_from_trend,
                    f.standardized_surprise_score,
                    t.direction_target,
                    t.acceleration_target,
                    t.binary_up_target,
                    row_number() OVER (
                        PARTITION BY t.target_row_id, f.indicator_id
                        ORDER BY f.release_timestamp_utc DESC, f.source_release_id DESC
                    ) AS latest_available_rank
                FROM processed.headline_targets t
                JOIN best_lag bl
                    ON bl.country_code = t.country_code
                    AND bl.headline_target_type = t.headline_target_type
                    AND bl.headline_indicator_id = t.indicator_id
                JOIN processed.indicator_features f
                    ON f.indicator_id = bl.candidate_indicator_id
                    AND f.release_timestamp_utc < t.release_timestamp_utc
                WHERE t.direction_target IS NOT NULL
            )
            INSERT INTO processed.modeling_feature_base (
                target_row_id, target_release_timestamp_utc, target_reference_period_start,
                country_code, currency_code, headline_target_type, headline_indicator_key,
                candidate_indicator_id, candidate_indicator_key, candidate_macro_theme,
                best_lag_months, lag_correlation, feature_release_timestamp_utc,
                feature_reference_period_start, actual_value, mom_change, yoy_change,
                rolling_avg_3, rolling_avg_6, expanding_z_score, momentum,
                deviation_from_trend, standardized_surprise_score, direction_target,
                acceleration_target, binary_up_target, no_lookahead
            )
            SELECT
                target_row_id,
                target_release_timestamp_utc,
                target_reference_period_start,
                country_code,
                currency_code,
                headline_target_type,
                headline_indicator_key,
                candidate_indicator_id,
                candidate_indicator_key,
                candidate_macro_theme,
                best_lag_months,
                lag_correlation,
                feature_release_timestamp_utc,
                feature_reference_period_start,
                actual_value,
                mom_change,
                yoy_change,
                rolling_avg_3,
                rolling_avg_6,
                expanding_z_score,
                momentum,
                deviation_from_trend,
                standardized_surprise_score,
                direction_target,
                acceleration_target,
                binary_up_target,
                feature_release_timestamp_utc < target_release_timestamp_utc
            FROM candidate
            WHERE latest_available_rank = 1
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX idx_modeling_feature_base_target
            ON processed.modeling_feature_base (
                country_code, headline_target_type, target_release_timestamp_utc
            )
            """
        )
    )


async def build_feature_summary(session: AsyncSession) -> dict[str, Any]:
    summary = await fetch_one(
        session,
        """
        SELECT
            (SELECT count(*) FROM processed.indicator_feature_map) AS mapped_indicators,
            (SELECT count(*) FROM processed.indicator_features) AS indicator_feature_rows,
            (SELECT count(*) FROM processed.headline_targets) AS headline_target_rows,
            (SELECT count(*) FROM processed.lag_analysis_results) AS lag_result_rows,
            (SELECT count(*) FROM processed.lag_analysis_results WHERE is_best_lag) AS best_lag_rows,
            (SELECT count(*) FROM processed.multicollinearity_flags) AS multicollinearity_flags,
            (SELECT count(*) FROM processed.modeling_feature_base) AS modeling_feature_rows
        """
    )
    themes = await fetch_all(
        session,
        """
        SELECT country_code, macro_theme, count(*) AS indicators
        FROM processed.indicator_feature_map
        GROUP BY country_code, macro_theme
        ORDER BY country_code, macro_theme
        """,
    )
    strongest = await fetch_all(
        session,
        """
        SELECT country_code, headline_target_type, headline_indicator_key,
               candidate_indicator_key, candidate_indicator_name, candidate_macro_theme,
               lag_months, observation_pairs, correlation, abs_correlation,
               relationship_strength
        FROM processed.lag_analysis_results
        WHERE is_best_lag IS TRUE
        ORDER BY abs_correlation DESC NULLS LAST, observation_pairs DESC
        LIMIT 30
        """,
    )
    feature_coverage = await fetch_all(
        session,
        """
        SELECT macro_theme,
               count(*) AS feature_rows,
               count(mom_change) AS mom_change_rows,
               count(yoy_change) AS yoy_change_rows,
               count(expanding_z_score) AS z_score_rows,
               count(standardized_surprise_score) AS surprise_score_rows
        FROM processed.indicator_features
        GROUP BY macro_theme
        ORDER BY macro_theme
        """,
    )
    limitations = await fetch_all(
        session,
        """
        SELECT flag, count(*) AS rows
        FROM processed.indicator_features f
        CROSS JOIN LATERAL unnest(f.feature_quality_flags) AS flag
        GROUP BY flag
        ORDER BY rows DESC
        """,
    )
    return {
        "summary": summary,
        "theme_counts": themes,
        "strongest_predictor_relationships": strongest,
        "feature_coverage": feature_coverage,
        "feature_limitations": limitations,
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
    (output_path / "README.md").write_text(
        "\n".join(
            [
                "# Macro Feature Layer",
                "",
                "Generated by `python -m scripts.build_feature_layer`.",
                "",
                "This layer creates time-series features, headline targets, lead-lag mappings, and a leakage-aware long modeling base.",
                "No predictive model is trained here.",
                "",
                "Primary next-step table: `processed.modeling_feature_base`.",
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

