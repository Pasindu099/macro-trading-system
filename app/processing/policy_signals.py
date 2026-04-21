"""Policy bias and policy shift engine built from macro pressure indices."""

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


POLICY_TABLES = (
    "processed.policy_weight_config",
    "processed.policy_signals",
)


@dataclass(frozen=True)
class PolicyBuildConfig:
    inflation_weight: float = 1.0
    labor_weight: float = 1.0
    growth_weight: float = 1.0
    strongly_hawkish_threshold: float = 1.0
    mildly_hawkish_threshold: float = 0.35
    mildly_dovish_threshold: float = -0.35
    strongly_dovish_threshold: float = -1.0
    flat_momentum_threshold: float = 0.10


async def build_policy_signals(
    output_dir: Path | str = Path("data/policy"),
    config: PolicyBuildConfig = PolicyBuildConfig(),
) -> dict[str, Any]:
    """Build transparent policy stance and shift signals."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    async with session_scope() as session:
        await create_policy_schema(session)
        await seed_policy_weights(session, config)
        await build_policy_signal_table(session, config)
        summary = await build_policy_summary(session)

        for table_name in POLICY_TABLES:
            await export_table_csv(
                session,
                table_name,
                output_path / f"{table_name.split('.')[-1]}.csv",
            )
        write_json(output_path / "policy_signals_report.json", summary)
        write_readme(output_path)

    return {
        "output_dir": str(output_path),
        "tables": list(POLICY_TABLES),
        "summary": summary,
    }


async def create_policy_schema(session: AsyncSession) -> None:
    statements = [
        "CREATE SCHEMA IF NOT EXISTS processed",
        "DROP TABLE IF EXISTS processed.policy_signals",
        "DROP TABLE IF EXISTS processed.policy_weight_config",
        """
        CREATE TABLE processed.policy_weight_config (
            country_code varchar(2) PRIMARY KEY,
            inflation_weight numeric(20,6) NOT NULL,
            labor_weight numeric(20,6) NOT NULL,
            growth_weight numeric(20,6) NOT NULL,
            notes text,
            processed_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE processed.policy_signals (
            signal_id bigserial PRIMARY KEY,
            signal_date date NOT NULL,
            country_code varchar(2) NOT NULL,
            currency_code varchar(3) NOT NULL,
            policy_score numeric(20,6),
            policy_label text NOT NULL,
            momentum_1m numeric(20,6),
            momentum_3m numeric(20,6),
            acceleration numeric(20,6),
            momentum_label text NOT NULL,
            confidence text NOT NULL,
            confidence_score numeric(20,6) NOT NULL,
            key_driver text,
            inflation_score numeric(20,6),
            labor_score numeric(20,6),
            growth_score numeric(20,6),
            inflation_contribution numeric(20,6),
            labor_contribution numeric(20,6),
            growth_contribution numeric(20,6),
            inflation_confidence text NOT NULL,
            labor_confidence text NOT NULL,
            growth_confidence text NOT NULL,
            inflation_contributors integer NOT NULL,
            labor_contributors integer NOT NULL,
            growth_contributors integer NOT NULL,
            contribution_breakdown jsonb NOT NULL,
            signal_flags text[] NOT NULL,
            no_lookahead boolean NOT NULL DEFAULT true,
            processed_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (signal_date, country_code)
        )
        """,
    ]
    for statement in statements:
        await session.execute(text(statement))


async def seed_policy_weights(
    session: AsyncSession, config: PolicyBuildConfig
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO processed.policy_weight_config (
                country_code, inflation_weight, labor_weight, growth_weight, notes
            )
            SELECT DISTINCT
                country_code,
                CAST(:inflation_weight AS numeric),
                CAST(:labor_weight AS numeric),
                CAST(:growth_weight AS numeric),
                'Default equal policy weights. Override by country after review.'
            FROM processed.theme_indices
            ORDER BY country_code
            """
        ),
        {
            "inflation_weight": config.inflation_weight,
            "labor_weight": config.labor_weight,
            "growth_weight": config.growth_weight,
        },
    )


async def build_policy_signal_table(
    session: AsyncSession, config: PolicyBuildConfig
) -> None:
    await session.execute(
        text(
            """
            WITH scored AS (
                SELECT
                    t.index_date AS signal_date,
                    t.country_code,
                    t.currency_code,
                    t.inflation_score,
                    t.labor_score,
                    t.growth_score,
                    t.inflation_confidence,
                    t.labor_confidence,
                    t.growth_confidence,
                    t.inflation_contributors,
                    t.labor_contributors,
                    t.growth_contributors,
                    w.inflation_weight,
                    w.labor_weight,
                    w.growth_weight,
                    CASE WHEN t.inflation_score IS NOT NULL THEN w.inflation_weight ELSE 0 END AS active_inflation_weight,
                    CASE WHEN t.labor_score IS NOT NULL THEN w.labor_weight ELSE 0 END AS active_labor_weight,
                    CASE WHEN t.growth_score IS NOT NULL THEN w.growth_weight ELSE 0 END AS active_growth_weight
                FROM processed.theme_indices t
                JOIN processed.policy_weight_config w ON w.country_code = t.country_code
            ),
            policy_base AS (
                SELECT
                    *,
                    inflation_score * active_inflation_weight AS inflation_contribution,
                    labor_score * active_labor_weight AS labor_contribution,
                    growth_score * active_growth_weight AS growth_contribution,
                    active_inflation_weight + active_labor_weight + active_growth_weight AS active_weight_sum,
                    (
                        coalesce(inflation_score * active_inflation_weight, 0)
                        + coalesce(labor_score * active_labor_weight, 0)
                        + coalesce(growth_score * active_growth_weight, 0)
                    ) / nullif(
                        active_inflation_weight + active_labor_weight + active_growth_weight,
                        0
                    ) AS policy_score,
                    (
                        confidence_value(inflation_confidence) * active_inflation_weight
                        + confidence_value(labor_confidence) * active_labor_weight
                        + confidence_value(growth_confidence) * active_growth_weight
                    ) / nullif(
                        active_inflation_weight + active_labor_weight + active_growth_weight,
                        0
                    ) AS weighted_confidence_score
                FROM scored
            ),
            with_momentum AS (
                SELECT
                    p.*,
                    lag(policy_score, 1) OVER (
                        PARTITION BY country_code ORDER BY signal_date
                    ) AS prior_policy_score,
                    lag(policy_score, 3) OVER (
                        PARTITION BY country_code ORDER BY signal_date
                    ) AS prior_3_policy_score,
                    lag(policy_score - lag_policy_score_safe, 1) OVER (
                        PARTITION BY country_code ORDER BY signal_date
                    ) AS prior_policy_delta
                FROM (
                    SELECT
                        p.*,
                        lag(policy_score, 1) OVER (
                            PARTITION BY country_code ORDER BY signal_date
                        ) AS lag_policy_score_safe
                    FROM policy_base p
                ) p
            ),
            final AS (
                SELECT
                    *,
                    policy_score - prior_policy_score AS momentum_1m,
                    policy_score - prior_3_policy_score AS momentum_3m,
                    (policy_score - prior_policy_score) - prior_policy_delta AS acceleration
                FROM with_momentum
            )
            INSERT INTO processed.policy_signals (
                signal_date, country_code, currency_code, policy_score, policy_label,
                momentum_1m, momentum_3m, acceleration, momentum_label, confidence,
                confidence_score, key_driver, inflation_score, labor_score, growth_score,
                inflation_contribution, labor_contribution, growth_contribution,
                inflation_confidence, labor_confidence, growth_confidence,
                inflation_contributors, labor_contributors, growth_contributors,
                contribution_breakdown, signal_flags, no_lookahead
            )
            SELECT
                signal_date,
                country_code,
                currency_code,
                policy_score,
                CASE
                    WHEN policy_score >= :strongly_hawkish_threshold THEN 'strongly_hawkish'
                    WHEN policy_score >= :mildly_hawkish_threshold THEN 'mildly_hawkish'
                    WHEN policy_score <= :strongly_dovish_threshold THEN 'strongly_dovish'
                    WHEN policy_score <= :mildly_dovish_threshold THEN 'mildly_dovish'
                    ELSE 'neutral'
                END,
                momentum_1m,
                momentum_3m,
                acceleration,
                CASE
                    WHEN momentum_1m IS NULL THEN 'unknown'
                    WHEN momentum_1m > :flat_momentum_threshold THEN 'more_hawkish'
                    WHEN momentum_1m < -:flat_momentum_threshold THEN 'more_dovish'
                    ELSE 'flat'
                END,
                CASE
                    WHEN weighted_confidence_score >= 0.67
                         AND (active_weight_sum / nullif(inflation_weight + labor_weight + growth_weight, 0)) >= 0.67
                    THEN 'high'
                    WHEN weighted_confidence_score >= 0.40
                         AND (active_weight_sum / nullif(inflation_weight + labor_weight + growth_weight, 0)) >= 0.34
                    THEN 'medium'
                    ELSE 'low'
                END,
                coalesce(weighted_confidence_score, 0),
                CASE
                    WHEN greatest(
                        abs(coalesce(inflation_contribution, 0)),
                        abs(coalesce(labor_contribution, 0)),
                        abs(coalesce(growth_contribution, 0))
                    ) = abs(coalesce(inflation_contribution, 0)) THEN 'inflation'
                    WHEN greatest(
                        abs(coalesce(inflation_contribution, 0)),
                        abs(coalesce(labor_contribution, 0)),
                        abs(coalesce(growth_contribution, 0))
                    ) = abs(coalesce(labor_contribution, 0)) THEN 'labor'
                    WHEN greatest(
                        abs(coalesce(inflation_contribution, 0)),
                        abs(coalesce(labor_contribution, 0)),
                        abs(coalesce(growth_contribution, 0))
                    ) = abs(coalesce(growth_contribution, 0)) THEN 'growth'
                    ELSE NULL
                END,
                inflation_score,
                labor_score,
                growth_score,
                inflation_contribution / nullif(active_weight_sum, 0),
                labor_contribution / nullif(active_weight_sum, 0),
                growth_contribution / nullif(active_weight_sum, 0),
                inflation_confidence,
                labor_confidence,
                growth_confidence,
                inflation_contributors,
                labor_contributors,
                growth_contributors,
                jsonb_build_object(
                    'inflation', jsonb_build_object(
                        'score', inflation_score,
                        'weight', active_inflation_weight,
                        'weighted_contribution', inflation_contribution / nullif(active_weight_sum, 0),
                        'confidence', inflation_confidence,
                        'contributors', inflation_contributors
                    ),
                    'labor', jsonb_build_object(
                        'score', labor_score,
                        'weight', active_labor_weight,
                        'weighted_contribution', labor_contribution / nullif(active_weight_sum, 0),
                        'confidence', labor_confidence,
                        'contributors', labor_contributors
                    ),
                    'growth', jsonb_build_object(
                        'score', growth_score,
                        'weight', active_growth_weight,
                        'weighted_contribution', growth_contribution / nullif(active_weight_sum, 0),
                        'confidence', growth_confidence,
                        'contributors', growth_contributors
                    )
                ),
                array_remove(ARRAY[
                    CASE WHEN active_weight_sum = 0 THEN 'no_available_theme_scores' END,
                    CASE WHEN inflation_score IS NULL THEN 'missing_inflation_score' END,
                    CASE WHEN labor_score IS NULL THEN 'missing_labor_score' END,
                    CASE WHEN growth_score IS NULL THEN 'missing_growth_score' END,
                    CASE WHEN weighted_confidence_score < 0.40 THEN 'confidence_limited' END
                ], NULL)::text[],
                true
            FROM final
            """
            .replace("confidence_value(inflation_confidence)", confidence_value("inflation_confidence"))
            .replace("confidence_value(labor_confidence)", confidence_value("labor_confidence"))
            .replace("confidence_value(growth_confidence)", confidence_value("growth_confidence"))
        ),
        {
            "strongly_hawkish_threshold": config.strongly_hawkish_threshold,
            "mildly_hawkish_threshold": config.mildly_hawkish_threshold,
            "strongly_dovish_threshold": config.strongly_dovish_threshold,
            "mildly_dovish_threshold": config.mildly_dovish_threshold,
            "flat_momentum_threshold": config.flat_momentum_threshold,
        },
    )
    await session.execute(
        text(
            """
            CREATE INDEX idx_policy_signals_country_date
            ON processed.policy_signals (country_code, signal_date)
            """
        )
    )


def confidence_value(column_name: str) -> str:
    return f"""
        CASE
            WHEN {column_name} = 'high' THEN 1.0
            WHEN {column_name} = 'medium' THEN 0.6
            WHEN {column_name} = 'low' THEN 0.25
            ELSE 0.0
        END
    """


async def build_policy_summary(session: AsyncSession) -> dict[str, Any]:
    summary = await fetch_one(
        session,
        """
        SELECT
            (SELECT count(*) FROM processed.policy_signals) AS policy_signal_rows,
            (SELECT count(*) FROM processed.policy_weight_config) AS countries_weighted,
            (SELECT count(*) FROM processed.policy_signals WHERE confidence = 'low') AS low_confidence_rows
        """
    )
    current_stance = await fetch_all(
        session,
        """
        WITH latest AS (
            SELECT *, row_number() OVER (PARTITION BY country_code ORDER BY signal_date DESC) AS rn
            FROM processed.policy_signals
        )
        SELECT country_code, currency_code, signal_date, policy_score, policy_label,
               momentum_label, momentum_1m, momentum_3m, acceleration,
               confidence, confidence_score, key_driver, signal_flags
        FROM latest
        WHERE rn = 1
        ORDER BY country_code
        """,
    )
    recent_shifts = await fetch_all(
        session,
        """
        SELECT country_code, currency_code, signal_date, policy_score, policy_label,
               momentum_label, momentum_1m, momentum_3m, acceleration,
               confidence, key_driver
        FROM processed.policy_signals
        WHERE signal_date >= (
            SELECT max(signal_date) - interval '3 months'
            FROM processed.policy_signals
        )
          AND momentum_label IN ('more_hawkish', 'more_dovish')
        ORDER BY signal_date DESC, abs(momentum_1m) DESC NULLS LAST
        LIMIT 50
        """,
    )
    weak_signals = await fetch_all(
        session,
        """
        WITH latest AS (
            SELECT *, row_number() OVER (PARTITION BY country_code ORDER BY signal_date DESC) AS rn
            FROM processed.policy_signals
        )
        SELECT country_code, signal_date, policy_label, confidence, confidence_score,
               signal_flags
        FROM latest
        WHERE rn = 1 AND confidence = 'low'
        ORDER BY country_code
        """,
    )
    label_distribution = await fetch_all(
        session,
        """
        SELECT policy_label, count(*) AS rows
        FROM processed.policy_signals
        GROUP BY policy_label
        ORDER BY rows DESC
        """,
    )
    key_driver_distribution = await fetch_all(
        session,
        """
        SELECT key_driver, count(*) AS rows
        FROM processed.policy_signals
        GROUP BY key_driver
        ORDER BY rows DESC
        """,
    )
    return {
        "summary": summary,
        "current_stance": current_stance,
        "recent_shifts_last_3_months": recent_shifts,
        "weak_current_signals": weak_signals,
        "label_distribution": label_distribution,
        "key_driver_distribution": key_driver_distribution,
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
                "# Policy Bias and Policy Shift Engine",
                "",
                "Generated by `python -m scripts.build_policy_signals`.",
                "",
                "This layer combines inflation, labor, and growth pressure indices into transparent policy stance and shift signals.",
                "No predictive model is trained here.",
                "",
                "Primary table: `processed.policy_signals`.",
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
