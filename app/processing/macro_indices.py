"""Build transparent macro pressure weights and theme-level indices."""

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


INDEX_TABLES = (
    "processed.relationship_weights",
    "processed.low_confidence_relationships",
    "processed.theme_index_components",
    "processed.theme_indices",
)


@dataclass(frozen=True)
class IndexBuildConfig:
    min_sample_size: int = 24
    min_abs_correlation: float = 0.40
    high_confidence_min_indicators: int = 5
    medium_confidence_min_indicators: int = 3
    high_confidence_min_corr: float = 0.55
    medium_confidence_min_corr: float = 0.40


async def build_macro_indices(
    output_dir: Path | str = Path("data/indices"),
    config: IndexBuildConfig = IndexBuildConfig(),
) -> dict[str, Any]:
    """Build relationship weights and country-level macro pressure indices."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    async with session_scope() as session:
        await create_index_schema(session)
        await build_relationship_weights(session, config)
        await build_low_confidence_relationships(session, config)
        await build_theme_index_components(session)
        await build_theme_indices(session, config)
        summary = await build_index_summary(session, config)

        for table_name in INDEX_TABLES:
            await export_table_csv(
                session,
                table_name,
                output_path / f"{table_name.split('.')[-1]}.csv",
            )
        write_json(output_path / "macro_indices_report.json", summary)
        write_readme(output_path)

    return {
        "output_dir": str(output_path),
        "tables": list(INDEX_TABLES),
        "summary": summary,
    }


async def create_index_schema(session: AsyncSession) -> None:
    statements = [
        "CREATE SCHEMA IF NOT EXISTS processed",
        "DROP TABLE IF EXISTS processed.theme_indices",
        "DROP TABLE IF EXISTS processed.theme_index_components",
        "DROP TABLE IF EXISTS processed.low_confidence_relationships",
        "DROP TABLE IF EXISTS processed.relationship_weights",
        """
        CREATE TABLE processed.relationship_weights (
            weight_id bigserial PRIMARY KEY,
            country_code varchar(2) NOT NULL,
            currency_code varchar(3) NOT NULL,
            macro_theme text NOT NULL,
            headline_target_type text NOT NULL,
            headline_indicator_id integer NOT NULL,
            headline_indicator_key text NOT NULL,
            candidate_indicator_id integer NOT NULL,
            candidate_indicator_key text NOT NULL,
            candidate_indicator_name text NOT NULL,
            lag_months integer NOT NULL,
            observation_pairs integer NOT NULL,
            correlation numeric(20,6) NOT NULL,
            abs_correlation numeric(20,6) NOT NULL,
            sample_adjustment numeric(20,6) NOT NULL,
            multicollinearity_penalty numeric(20,6) NOT NULL,
            raw_weight numeric(20,6) NOT NULL,
            assigned_weight numeric(20,6) NOT NULL,
            signal_direction numeric(20,6) NOT NULL,
            confidence_bucket text NOT NULL,
            processed_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (country_code, macro_theme, headline_target_type, candidate_indicator_id)
        )
        """,
        """
        CREATE TABLE processed.low_confidence_relationships (
            relationship_id bigserial PRIMARY KEY,
            country_code varchar(2) NOT NULL,
            currency_code varchar(3) NOT NULL,
            headline_target_type text NOT NULL,
            headline_indicator_key text NOT NULL,
            candidate_indicator_id integer NOT NULL,
            candidate_indicator_key text NOT NULL,
            candidate_indicator_name text NOT NULL,
            candidate_macro_theme text NOT NULL,
            lag_months integer NOT NULL,
            observation_pairs integer NOT NULL,
            correlation numeric(20,6),
            abs_correlation numeric(20,6),
            rejection_reason text NOT NULL,
            processed_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE processed.theme_index_components (
            component_id bigserial PRIMARY KEY,
            index_date date NOT NULL,
            country_code varchar(2) NOT NULL,
            currency_code varchar(3) NOT NULL,
            macro_theme text NOT NULL,
            headline_target_type text NOT NULL,
            candidate_indicator_id integer NOT NULL,
            candidate_indicator_key text NOT NULL,
            feature_release_timestamp_utc timestamptz NOT NULL,
            normalized_signal numeric(20,6),
            directional_signal numeric(20,6),
            assigned_weight numeric(20,6) NOT NULL,
            weighted_signal numeric(20,6),
            observation_pairs integer NOT NULL,
            abs_correlation numeric(20,6) NOT NULL,
            component_available boolean NOT NULL,
            processed_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE processed.theme_indices (
            index_id bigserial PRIMARY KEY,
            index_date date NOT NULL,
            country_code varchar(2) NOT NULL,
            currency_code varchar(3) NOT NULL,
            inflation_score numeric(20,6),
            labor_score numeric(20,6),
            growth_score numeric(20,6),
            inflation_contributors integer NOT NULL DEFAULT 0,
            labor_contributors integer NOT NULL DEFAULT 0,
            growth_contributors integer NOT NULL DEFAULT 0,
            inflation_avg_abs_corr numeric(20,6),
            labor_avg_abs_corr numeric(20,6),
            growth_avg_abs_corr numeric(20,6),
            inflation_completeness numeric(20,6),
            labor_completeness numeric(20,6),
            growth_completeness numeric(20,6),
            inflation_confidence text NOT NULL DEFAULT 'low',
            labor_confidence text NOT NULL DEFAULT 'low',
            growth_confidence text NOT NULL DEFAULT 'low',
            processed_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (index_date, country_code)
        )
        """,
    ]
    for statement in statements:
        await session.execute(text(statement))


async def build_relationship_weights(
    session: AsyncSession, config: IndexBuildConfig
) -> None:
    await session.execute(
        text(
            """
            WITH selected AS (
                SELECT *
                FROM (
                    SELECT
                        l.*,
                        CASE
                            WHEN l.headline_target_type = 'CPI' THEN 'Inflation'
                            WHEN l.headline_target_type = 'UNEMPLOYMENT' THEN 'Labor'
                            WHEN l.headline_target_type = 'GDP' THEN 'Growth'
                        END AS macro_theme,
                        row_number() OVER (
                            PARTITION BY
                                l.country_code,
                                CASE
                                    WHEN l.headline_target_type = 'CPI' THEN 'Inflation'
                                    WHEN l.headline_target_type = 'UNEMPLOYMENT' THEN 'Labor'
                                    WHEN l.headline_target_type = 'GDP' THEN 'Growth'
                                END,
                                l.candidate_indicator_id
                            ORDER BY l.abs_correlation DESC NULLS LAST,
                                     l.observation_pairs DESC,
                                     l.lag_months ASC
                        ) AS candidate_theme_rank
                    FROM processed.lag_analysis_results l
                    WHERE l.is_best_lag IS TRUE
                      AND l.observation_pairs >= :min_sample_size
                      AND l.abs_correlation >= :min_abs_correlation
                      AND l.headline_target_type IN ('CPI', 'UNEMPLOYMENT', 'GDP')
                ) ranked_selected
                WHERE candidate_theme_rank = 1
            ),
            penalties AS (
                SELECT
                    s.*,
                    CASE
                        WHEN EXISTS (
                            SELECT 1
                            FROM processed.multicollinearity_flags m
                            WHERE m.country_code = s.country_code
                              AND (
                                  m.indicator_id_a = s.candidate_indicator_id
                                  OR m.indicator_id_b = s.candidate_indicator_id
                              )
                        )
                        THEN 0.65::numeric
                        ELSE 1.0::numeric
                    END AS multicollinearity_penalty,
                    least(1.0, sqrt(s.observation_pairs::numeric / :min_sample_size)) AS sample_adjustment
                FROM selected s
            ),
            raw_weights AS (
                SELECT
                    p.*,
                    (p.abs_correlation * p.sample_adjustment * p.multicollinearity_penalty) AS raw_weight
                FROM penalties p
            ),
            normalized AS (
                SELECT
                    r.*,
                    r.raw_weight / nullif(
                        sum(r.raw_weight) OVER (PARTITION BY r.country_code, r.macro_theme),
                        0
                    ) AS assigned_weight
                FROM raw_weights r
            )
            INSERT INTO processed.relationship_weights (
                country_code, currency_code, macro_theme, headline_target_type,
                headline_indicator_id, headline_indicator_key, candidate_indicator_id,
                candidate_indicator_key, candidate_indicator_name, lag_months,
                observation_pairs, correlation, abs_correlation, sample_adjustment,
                multicollinearity_penalty, raw_weight, assigned_weight,
                signal_direction, confidence_bucket
            )
            SELECT
                country_code,
                currency_code,
                macro_theme,
                headline_target_type,
                headline_indicator_id,
                headline_indicator_key,
                candidate_indicator_id,
                candidate_indicator_key,
                candidate_indicator_name,
                lag_months,
                observation_pairs,
                correlation,
                abs_correlation,
                sample_adjustment,
                multicollinearity_penalty,
                raw_weight,
                assigned_weight,
                CASE
                    WHEN headline_target_type = 'UNEMPLOYMENT' THEN -sign(correlation)
                    ELSE sign(correlation)
                END,
                CASE
                    WHEN observation_pairs >= 48 AND abs_correlation >= 0.55 THEN 'high'
                    WHEN observation_pairs >= :min_sample_size AND abs_correlation >= :min_abs_correlation THEN 'medium'
                    ELSE 'low'
                END
            FROM normalized
            WHERE assigned_weight IS NOT NULL
            """,
        ),
        {
            "min_sample_size": config.min_sample_size,
            "min_abs_correlation": config.min_abs_correlation,
        },
    )


async def build_low_confidence_relationships(
    session: AsyncSession, config: IndexBuildConfig
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO processed.low_confidence_relationships (
                country_code, currency_code, headline_target_type, headline_indicator_key,
                candidate_indicator_id, candidate_indicator_key, candidate_indicator_name,
                candidate_macro_theme, lag_months, observation_pairs, correlation,
                abs_correlation, rejection_reason
            )
            SELECT
                country_code,
                currency_code,
                headline_target_type,
                headline_indicator_key,
                candidate_indicator_id,
                candidate_indicator_key,
                candidate_indicator_name,
                candidate_macro_theme,
                lag_months,
                observation_pairs,
                correlation,
                abs_correlation,
                CASE
                    WHEN observation_pairs < :min_sample_size AND abs_correlation < :min_abs_correlation
                    THEN 'low_sample_and_low_correlation'
                    WHEN observation_pairs < :min_sample_size THEN 'low_sample'
                    WHEN abs_correlation < :min_abs_correlation THEN 'low_correlation'
                    ELSE 'not_best_lag'
                END
            FROM processed.lag_analysis_results
            WHERE headline_target_type IN ('CPI', 'UNEMPLOYMENT', 'GDP')
              AND (
                  is_best_lag IS FALSE
                  OR observation_pairs < :min_sample_size
                  OR abs_correlation < :min_abs_correlation
              )
            """,
        ),
        {
            "min_sample_size": config.min_sample_size,
            "min_abs_correlation": config.min_abs_correlation,
        },
    )


async def build_theme_index_components(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            WITH feature_days AS (
                SELECT DISTINCT release_date_utc AS index_date, country_code, currency_code
                FROM processed.indicator_features
            ),
            component_snapshot AS (
                SELECT
                    d.index_date,
                    d.country_code,
                    d.currency_code,
                    w.macro_theme,
                    w.headline_target_type,
                    w.candidate_indicator_id,
                    w.candidate_indicator_key,
                    f.release_timestamp_utc AS feature_release_timestamp_utc,
                    coalesce(
                        f.standardized_surprise_score,
                        f.expanding_z_score,
                        f.momentum,
                        f.mom_change
                    ) AS normalized_signal,
                    w.assigned_weight,
                    w.signal_direction,
                    w.observation_pairs,
                    w.abs_correlation,
                    row_number() OVER (
                        PARTITION BY d.index_date, d.country_code, w.macro_theme, w.candidate_indicator_id
                        ORDER BY f.release_timestamp_utc DESC, f.source_release_id DESC
                    ) AS latest_rank
                FROM feature_days d
                JOIN processed.relationship_weights w
                    ON w.country_code = d.country_code
                JOIN processed.indicator_features f
                    ON f.indicator_id = w.candidate_indicator_id
                    AND f.release_date_utc <= d.index_date
                    AND f.release_timestamp_utc < (d.index_date + 1)::timestamptz
            )
            INSERT INTO processed.theme_index_components (
                index_date, country_code, currency_code, macro_theme,
                headline_target_type, candidate_indicator_id, candidate_indicator_key,
                feature_release_timestamp_utc, normalized_signal, directional_signal,
                assigned_weight, weighted_signal, observation_pairs, abs_correlation,
                component_available
            )
            SELECT
                index_date,
                country_code,
                currency_code,
                macro_theme,
                headline_target_type,
                candidate_indicator_id,
                candidate_indicator_key,
                feature_release_timestamp_utc,
                normalized_signal,
                normalized_signal * signal_direction,
                assigned_weight,
                assigned_weight * normalized_signal * signal_direction,
                observation_pairs,
                abs_correlation,
                normalized_signal IS NOT NULL
            FROM component_snapshot
            WHERE latest_rank = 1
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX idx_theme_index_components_lookup
            ON processed.theme_index_components (country_code, index_date, macro_theme)
            """
        )
    )


async def build_theme_indices(
    session: AsyncSession, config: IndexBuildConfig
) -> None:
    await session.execute(
        text(
            """
            WITH index_days AS (
                SELECT DISTINCT index_date, country_code, currency_code
                FROM processed.theme_index_components
            ),
            theme_totals AS (
                SELECT country_code, macro_theme, count(*) AS expected_components
                FROM processed.relationship_weights
                GROUP BY country_code, macro_theme
            ),
            theme_scores AS (
                SELECT
                    c.index_date,
                    c.country_code,
                    c.currency_code,
                    c.macro_theme,
                    sum(c.weighted_signal) FILTER (WHERE c.component_available) AS theme_score,
                    count(*) FILTER (WHERE c.component_available) AS contributors,
                    avg(c.abs_correlation) FILTER (WHERE c.component_available) AS avg_abs_corr,
                    count(*) FILTER (WHERE c.component_available)::numeric
                        / nullif(max(t.expected_components), 0) AS completeness
                FROM processed.theme_index_components c
                JOIN theme_totals t
                    ON t.country_code = c.country_code
                    AND t.macro_theme = c.macro_theme
                GROUP BY c.index_date, c.country_code, c.currency_code, c.macro_theme
            ),
            headline_snapshots AS (
                SELECT
                    index_date,
                    country_code,
                    currency_code,
                    macro_theme,
                    headline_score,
                    row_number() OVER (
                        PARTITION BY index_date, country_code, macro_theme
                        ORDER BY release_timestamp_utc DESC, source_release_id DESC
                    ) AS latest_rank
                FROM (
                    SELECT
                        d.index_date,
                        f.country_code,
                        f.currency_code,
                        CASE
                            WHEN fmap.headline_target_type = 'CPI' THEN 'Inflation'
                            WHEN fmap.headline_target_type = 'UNEMPLOYMENT' THEN 'Labor'
                            WHEN fmap.headline_target_type = 'GDP' THEN 'Growth'
                        END AS macro_theme,
                        (
                            coalesce(
                                f.standardized_surprise_score,
                                f.momentum,
                                f.mom_change,
                                f.expanding_z_score
                            )
                            * CASE
                                WHEN fmap.headline_target_type = 'UNEMPLOYMENT' THEN -1
                                ELSE 1
                              END
                        ) AS headline_score,
                        f.release_timestamp_utc,
                        f.source_release_id
                    FROM index_days d
                    JOIN processed.indicator_features f
                        ON f.country_code = d.country_code
                        AND f.release_date_utc <= d.index_date
                        AND f.release_timestamp_utc < (d.index_date + 1)::timestamptz
                    JOIN processed.indicator_feature_map fmap
                        ON fmap.indicator_id = f.indicator_id
                        AND fmap.headline_target_type IN ('CPI', 'UNEMPLOYMENT', 'GDP')
                    WHERE coalesce(
                        f.standardized_surprise_score,
                        f.momentum,
                        f.mom_change,
                        f.expanding_z_score
                    ) IS NOT NULL
                ) headline_candidates
            ),
            headline_latest AS (
                SELECT *
                FROM headline_snapshots
                WHERE latest_rank = 1
            ),
            blended_theme_scores AS (
                SELECT
                    coalesce(t.index_date, h.index_date) AS index_date,
                    coalesce(t.country_code, h.country_code) AS country_code,
                    coalesce(t.currency_code, h.currency_code) AS currency_code,
                    coalesce(t.macro_theme, h.macro_theme) AS macro_theme,
                    CASE
                        WHEN h.headline_score IS NULL THEN t.theme_score
                        WHEN t.theme_score IS NULL THEN h.headline_score
                        ELSE (t.theme_score * 0.30) + (h.headline_score * 0.70)
                    END AS theme_score,
                    coalesce(t.contributors, 0)
                        + CASE WHEN h.headline_score IS NULL THEN 0 ELSE 1 END AS contributors,
                    coalesce(t.avg_abs_corr, 1.0) AS avg_abs_corr,
                    greatest(
                        coalesce(t.completeness, 0),
                        CASE WHEN h.headline_score IS NULL THEN 0 ELSE 1 END
                    ) AS completeness
                FROM theme_scores t
                FULL OUTER JOIN headline_latest h
                    ON h.index_date = t.index_date
                    AND h.country_code = t.country_code
                    AND h.macro_theme = t.macro_theme
            ),
            pivoted AS (
                SELECT
                    index_date,
                    country_code,
                    max(currency_code) AS currency_code,
                    max(theme_score) FILTER (WHERE macro_theme = 'Inflation') AS inflation_score,
                    max(theme_score) FILTER (WHERE macro_theme = 'Labor') AS labor_score,
                    max(theme_score) FILTER (WHERE macro_theme = 'Growth') AS growth_score,
                    coalesce(max(contributors) FILTER (WHERE macro_theme = 'Inflation'), 0) AS inflation_contributors,
                    coalesce(max(contributors) FILTER (WHERE macro_theme = 'Labor'), 0) AS labor_contributors,
                    coalesce(max(contributors) FILTER (WHERE macro_theme = 'Growth'), 0) AS growth_contributors,
                    max(avg_abs_corr) FILTER (WHERE macro_theme = 'Inflation') AS inflation_avg_abs_corr,
                    max(avg_abs_corr) FILTER (WHERE macro_theme = 'Labor') AS labor_avg_abs_corr,
                    max(avg_abs_corr) FILTER (WHERE macro_theme = 'Growth') AS growth_avg_abs_corr,
                    max(completeness) FILTER (WHERE macro_theme = 'Inflation') AS inflation_completeness,
                    max(completeness) FILTER (WHERE macro_theme = 'Labor') AS labor_completeness,
                    max(completeness) FILTER (WHERE macro_theme = 'Growth') AS growth_completeness
                FROM blended_theme_scores
                GROUP BY index_date, country_code
            )
            INSERT INTO processed.theme_indices (
                index_date, country_code, currency_code, inflation_score, labor_score,
                growth_score, inflation_contributors, labor_contributors,
                growth_contributors, inflation_avg_abs_corr, labor_avg_abs_corr,
                growth_avg_abs_corr, inflation_completeness, labor_completeness,
                growth_completeness, inflation_confidence, labor_confidence,
                growth_confidence
            )
            SELECT
                index_date,
                country_code,
                currency_code,
                inflation_score,
                labor_score,
                growth_score,
                inflation_contributors,
                labor_contributors,
                growth_contributors,
                inflation_avg_abs_corr,
                labor_avg_abs_corr,
                growth_avg_abs_corr,
                inflation_completeness,
                labor_completeness,
                growth_completeness,
                confidence_label(
                    inflation_contributors, inflation_avg_abs_corr, inflation_completeness
                ),
                confidence_label(
                    labor_contributors, labor_avg_abs_corr, labor_completeness
                ),
                confidence_label(
                    growth_contributors, growth_avg_abs_corr, growth_completeness
                )
            FROM pivoted
            """.replace(
                "confidence_label(\n                    inflation_contributors, inflation_avg_abs_corr, inflation_completeness\n                )",
                confidence_case(
                    "inflation_contributors",
                    "inflation_avg_abs_corr",
                    "inflation_completeness",
                    config,
                ),
            )
            .replace(
                "confidence_label(\n                    labor_contributors, labor_avg_abs_corr, labor_completeness\n                )",
                confidence_case(
                    "labor_contributors",
                    "labor_avg_abs_corr",
                    "labor_completeness",
                    config,
                ),
            )
            .replace(
                "confidence_label(\n                    growth_contributors, growth_avg_abs_corr, growth_completeness\n                )",
                confidence_case(
                    "growth_contributors",
                    "growth_avg_abs_corr",
                    "growth_completeness",
                    config,
                ),
            )
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX idx_theme_indices_country_date
            ON processed.theme_indices (country_code, index_date)
            """
        )
    )


def confidence_case(
    contributors_column: str,
    corr_column: str,
    completeness_column: str,
    config: IndexBuildConfig,
) -> str:
    return f"""
                CASE
                    WHEN {contributors_column} >= {config.high_confidence_min_indicators}
                         AND coalesce({corr_column}, 0) >= {config.high_confidence_min_corr}
                         AND coalesce({completeness_column}, 0) >= 0.75
                    THEN 'high'
                    WHEN {contributors_column} >= {config.medium_confidence_min_indicators}
                         AND coalesce({corr_column}, 0) >= {config.medium_confidence_min_corr}
                         AND coalesce({completeness_column}, 0) >= 0.50
                    THEN 'medium'
                    ELSE 'low'
                END
    """


async def build_index_summary(
    session: AsyncSession, config: IndexBuildConfig
) -> dict[str, Any]:
    summary = await fetch_one(
        session,
        """
        SELECT
            (SELECT count(*) FROM processed.relationship_weights) AS selected_relationships,
            (SELECT count(*) FROM processed.low_confidence_relationships) AS low_confidence_relationships,
            (SELECT count(*) FROM processed.theme_index_components) AS index_components,
            (SELECT count(*) FROM processed.theme_indices) AS theme_index_rows
        """
    )
    weights = await fetch_all(
        session,
        """
        SELECT country_code, macro_theme, count(*) AS indicators,
               round(sum(assigned_weight), 6) AS weight_sum,
               round(max(assigned_weight), 6) AS max_weight,
               round(avg(assigned_weight), 6) AS avg_weight
        FROM processed.relationship_weights
        GROUP BY country_code, macro_theme
        ORDER BY country_code, macro_theme
        """
    )
    top_contributors = await fetch_all(
        session,
        """
        SELECT country_code, macro_theme, headline_target_type, candidate_indicator_key,
               candidate_indicator_name, lag_months, observation_pairs,
               correlation, assigned_weight, confidence_bucket
        FROM processed.relationship_weights
        ORDER BY macro_theme, country_code, assigned_weight DESC
        LIMIT 60
        """
    )
    confidence_distribution = await fetch_all(
        session,
        """
        SELECT confidence, count(*) AS theme_days
        FROM (
            SELECT inflation_confidence AS confidence FROM processed.theme_indices
            UNION ALL
            SELECT labor_confidence FROM processed.theme_indices
            UNION ALL
            SELECT growth_confidence FROM processed.theme_indices
        ) s
        GROUP BY confidence
        ORDER BY confidence
        """
    )
    coverage_gaps = await fetch_all(
        session,
        """
        SELECT c.country_code, x.theme
        FROM (SELECT DISTINCT country_code FROM processed.indicator_feature_map) c
        CROSS JOIN (VALUES ('Inflation'), ('Labor'), ('Growth')) AS x(theme)
        LEFT JOIN processed.relationship_weights w
            ON w.country_code = c.country_code
            AND w.macro_theme = x.theme
        GROUP BY c.country_code, x.theme
        HAVING count(w.weight_id) = 0
        ORDER BY c.country_code, x.theme
        """
    )
    low_confidence_reasons = await fetch_all(
        session,
        """
        SELECT rejection_reason, count(*) AS relationships
        FROM processed.low_confidence_relationships
        GROUP BY rejection_reason
        ORDER BY relationships DESC
        """
    )
    return {
        "config": {
            "min_sample_size": config.min_sample_size,
            "min_abs_correlation": config.min_abs_correlation,
            "high_confidence_min_indicators": config.high_confidence_min_indicators,
            "medium_confidence_min_indicators": config.medium_confidence_min_indicators,
        },
        "summary": summary,
        "weight_distribution": weights,
        "top_contributors": top_contributors,
        "confidence_distribution": confidence_distribution,
        "coverage_gaps": coverage_gaps,
        "low_confidence_reasons": low_confidence_reasons,
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
                "# Macro Pressure Indices",
                "",
                "Generated by `python -m scripts.build_macro_indices`.",
                "",
                "This layer converts feature relationships into transparent weights and country-level inflation, labor, and growth pressure scores.",
                "No predictive model is trained here.",
                "",
                "Primary next-step tables: `processed.theme_indices` and `processed.relationship_weights`.",
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
