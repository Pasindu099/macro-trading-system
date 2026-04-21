"""Build analysis-ready macro datasets from the collected raw tables."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope


RAW_TABLES = ("countries", "indicators", "indicator_releases", "ingestion_runs")
PROCESSED_TABLES = (
    "processed.indicator_metadata",
    "processed.macro_observations",
    "processed.data_quality_issues",
    "processed.dataset_profile",
)


def json_default(value: Any) -> str | float | int | bool | None:
    """Serialize DB-native values into stable JSON report values."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def csv_value(value: Any) -> str | int | float | bool | None:
    """Write arrays and JSON values into CSV without losing structure."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=json_default, sort_keys=True)
    return value


def history_threshold_for_frequency(frequency: str | None) -> int:
    """Minimum usable history before an indicator should be manually reviewed."""
    normalized = (frequency or "").lower()
    if normalized in {"daily", "weekly", "monthly"}:
        return 24
    if normalized == "quarterly":
        return 12
    if normalized in {"annual", "yearly"}:
        return 5
    return 8


async def build_processed_dataset(output_dir: Path | str = Path("data/processed")) -> dict[str, Any]:
    """Create processed DB tables plus file-based quality and metadata outputs."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    async with session_scope() as session:
        await create_processed_schema(session)
        raw_profile = await inspect_raw_tables(session)
        await rebuild_indicator_metadata(session)
        await rebuild_macro_observations(session)
        await rebuild_quality_issues(session)
        report = await build_quality_report(session, raw_profile)
        await write_dataset_profile(session, raw_profile, report)

        await export_table_csv(session, "processed.indicator_metadata", output_path / "indicator_metadata.csv")
        await export_table_csv(session, "processed.macro_observations", output_path / "macro_observations.csv")
        await export_table_csv(session, "processed.data_quality_issues", output_path / "data_quality_issues.csv")
        write_json(output_path / "raw_structure_report.json", raw_profile)
        write_json(output_path / "data_quality_report.json", report)
        write_readme(output_path)

    return {
        "output_dir": str(output_path),
        "tables": list(PROCESSED_TABLES),
        "files": [
            "indicator_metadata.csv",
            "macro_observations.csv",
            "data_quality_issues.csv",
            "raw_structure_report.json",
            "data_quality_report.json",
            "README.md",
        ],
    }


async def create_processed_schema(session: AsyncSession) -> None:
    """Recreate only the processed layer; raw collection tables are untouched."""
    statements = [
        "CREATE SCHEMA IF NOT EXISTS processed",
        "DROP TABLE IF EXISTS processed.data_quality_issues",
        "DROP TABLE IF EXISTS processed.macro_observations",
        "DROP TABLE IF EXISTS processed.indicator_metadata",
        "DROP TABLE IF EXISTS processed.dataset_profile",
        """
        CREATE TABLE processed.indicator_metadata (
            indicator_id integer PRIMARY KEY,
            country_code varchar(2) NOT NULL,
            country_name text NOT NULL,
            currency_code varchar(3) NOT NULL,
            central_bank text NOT NULL,
            inflation_target numeric(4,2),
            mandate_type text NOT NULL,
            country_timezone text NOT NULL,
            indicator_key text NOT NULL,
            indicator_name text NOT NULL,
            primary_category text NOT NULL,
            secondary_categories text[] NOT NULL,
            comparison text,
            frequency text NOT NULL,
            unit text,
            is_higher_better_for_currency boolean NOT NULL,
            importance smallint NOT NULL,
            is_headline_indicator boolean NOT NULL,
            is_sub_indicator boolean NOT NULL,
            first_release_at_utc timestamptz,
            last_release_at_utc timestamptz,
            first_reference_period date,
            last_reference_period date,
            release_count integer NOT NULL,
            actual_count integer NOT NULL,
            missing_actual_count integer NOT NULL,
            exact_duplicate_candidate_count integer NOT NULL,
            insufficient_history boolean NOT NULL,
            notes text,
            processed_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (country_code, indicator_key)
        )
        """,
        """
        CREATE TABLE processed.macro_observations (
            observation_id bigserial PRIMARY KEY,
            source_release_id bigint NOT NULL,
            indicator_id integer NOT NULL,
            country_code varchar(2) NOT NULL,
            currency_code varchar(3) NOT NULL,
            country_name text NOT NULL,
            indicator_key text NOT NULL,
            indicator_name text NOT NULL,
            primary_category text NOT NULL,
            secondary_categories text[] NOT NULL,
            is_headline_indicator boolean NOT NULL,
            is_sub_indicator boolean NOT NULL,
            frequency text NOT NULL,
            unit text,
            period_label text,
            reference_period_start date,
            release_timestamp_utc timestamptz NOT NULL,
            release_date_utc date NOT NULL,
            actual_value numeric(20,6),
            estimate_value numeric(20,6),
            previous_value numeric(20,6),
            surprise_value numeric(20,6),
            change_value numeric(20,6),
            change_percentage_value numeric(20,6),
            is_latest boolean NOT NULL,
            revision_count integer NOT NULL,
            has_revision_history boolean NOT NULL,
            data_quality_flags text[] NOT NULL,
            raw_payload jsonb,
            processed_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (source_release_id)
        )
        """,
        """
        CREATE TABLE processed.data_quality_issues (
            issue_id bigserial PRIMARY KEY,
            severity text NOT NULL,
            issue_type text NOT NULL,
            table_name text NOT NULL,
            source_id text,
            country_code varchar(2),
            currency_code varchar(3),
            indicator_id integer,
            indicator_key text,
            period_label text,
            reference_period_start date,
            release_timestamp_utc timestamptz,
            details jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE processed.dataset_profile (
            profile_id bigserial PRIMARY KEY,
            object_layer text NOT NULL,
            object_name text NOT NULL,
            profile_json jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """,
    ]
    for statement in statements:
        await session.execute(text(statement))


async def inspect_raw_tables(session: AsyncSession) -> dict[str, Any]:
    """Profile source tables: columns, types, coverage, missingness, duplicates."""
    tables: dict[str, Any] = {}
    for table_name in RAW_TABLES:
        columns = await fetch_all(
            session,
            """
            SELECT column_name, data_type, udt_name, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table_name
            ORDER BY ordinal_position
            """,
            {"table_name": table_name},
        )
        row_count = await fetch_scalar(session, f"SELECT count(*) FROM {table_name}")
        missingness = {}
        for column in columns:
            column_name = column["column_name"]
            missing_count = await fetch_scalar(
                session,
                f"SELECT count(*) FROM {table_name} WHERE {column_name} IS NULL",
            )
            missingness[column_name] = {
                "missing_count": missing_count,
                "missing_pct": round((missing_count / row_count * 100), 2) if row_count else 0,
            }

        tables[table_name] = {
            "row_count": row_count,
            "columns": columns,
            "missingness": missingness,
            "date_coverage": await date_coverage(session, table_name),
            "duplicates": await duplicate_profile(session, table_name),
            "frequency": await frequency_profile(session, table_name),
            "obvious_inconsistencies": await inconsistency_profile(session, table_name),
        }
    return {"raw_tables": tables, "generated_at": datetime.utcnow().isoformat() + "Z"}


async def date_coverage(session: AsyncSession, table_name: str) -> dict[str, Any]:
    if table_name == "indicator_releases":
        return await fetch_one(
            session,
            """
            SELECT
                min(released_at) AS first_release_at_utc,
                max(released_at) AS last_release_at_utc,
                min(period_start_date) AS first_reference_period,
                max(period_start_date) AS last_reference_period
            FROM indicator_releases
            """,
        )
    if table_name == "ingestion_runs":
        return await fetch_one(
            session,
            """
            SELECT min(started_at) AS first_started_at, max(finished_at) AS last_finished_at
            FROM ingestion_runs
            """,
        )
    if table_name == "indicators":
        return await fetch_one(
            session,
            """
            SELECT min(r.released_at) AS first_release_at_utc, max(r.released_at) AS last_release_at_utc
            FROM indicators i
            LEFT JOIN indicator_releases r ON r.indicator_id = i.id
            """,
        )
    return {}


async def duplicate_profile(session: AsyncSession, table_name: str) -> dict[str, Any]:
    if table_name == "countries":
        return {"primary_key_duplicates": 0}
    if table_name == "indicators":
        value = await fetch_scalar(
            session,
            """
            SELECT count(*)
            FROM (
                SELECT canonical_name, country_code
                FROM indicators
                GROUP BY canonical_name, country_code
                HAVING count(*) > 1
            ) d
            """,
        )
        return {"canonical_country_duplicate_groups": value}
    if table_name == "indicator_releases":
        value = await fetch_scalar(
            session,
            """
            SELECT coalesce(sum(group_count - 1), 0)
            FROM (
                SELECT count(*) AS group_count
                FROM indicator_releases
                GROUP BY indicator_id, period, period_start_date, released_at,
                         actual, previous, estimate, change, change_percentage
                HAVING count(*) > 1
            ) d
            """,
        )
        return {"exact_duplicate_candidate_rows": value}
    if table_name == "ingestion_runs":
        return {"duplicate_run_ids": 0}
    return {}


async def frequency_profile(session: AsyncSession, table_name: str) -> dict[str, Any]:
    if table_name == "indicators":
        rows = await fetch_all(
            session,
            """
            SELECT country_code, frequency, count(*) AS indicator_count
            FROM indicators
            GROUP BY country_code, frequency
            ORDER BY country_code, frequency
            """,
        )
        return {"indicator_frequency_counts": rows}
    if table_name == "indicator_releases":
        rows = await fetch_all(
            session,
            """
            SELECT i.country_code, i.frequency, count(r.id) AS release_count
            FROM indicator_releases r
            LEFT JOIN indicators i ON i.id = r.indicator_id
            GROUP BY i.country_code, i.frequency
            ORDER BY i.country_code, i.frequency
            """,
        )
        return {"release_frequency_counts": rows}
    return {}


async def inconsistency_profile(session: AsyncSession, table_name: str) -> dict[str, Any]:
    if table_name != "indicator_releases":
        return {}
    return await fetch_one(
        session,
        """
        SELECT
            count(*) FILTER (WHERE indicator_id IS NULL) AS unmapped_releases,
            count(*) FILTER (WHERE actual IS NULL) AS missing_actual,
            count(*) FILTER (WHERE period_start_date IS NULL) AS missing_reference_period,
            count(*) FILTER (WHERE period_start_date > (released_at AT TIME ZONE 'UTC')::date + 45) AS future_reference_period,
            count(*) FILTER (WHERE released_at < period_start_date::timestamptz) AS release_before_reference_period,
            count(*) FILTER (WHERE is_latest IS NULL) AS missing_latest_flag
        FROM indicator_releases
        """,
    )


async def rebuild_indicator_metadata(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            WITH exact_dupes AS (
                SELECT indicator_id, coalesce(sum(group_count - 1), 0)::integer AS duplicate_count
                FROM (
                    SELECT indicator_id, count(*) AS group_count
                    FROM indicator_releases
                    WHERE indicator_id IS NOT NULL
                    GROUP BY indicator_id, period, period_start_date, released_at,
                             actual, previous, estimate, change, change_percentage
                    HAVING count(*) > 1
                ) d
                GROUP BY indicator_id
            ),
            release_stats AS (
                SELECT
                    indicator_id,
                    min(released_at) AS first_release_at_utc,
                    max(released_at) AS last_release_at_utc,
                    min(period_start_date) AS first_reference_period,
                    max(period_start_date) AS last_reference_period,
                    count(*)::integer AS release_count,
                    count(actual)::integer AS actual_count,
                    count(*) FILTER (WHERE actual IS NULL)::integer AS missing_actual_count
                FROM indicator_releases
                WHERE indicator_id IS NOT NULL
                GROUP BY indicator_id
            )
            INSERT INTO processed.indicator_metadata (
                indicator_id, country_code, country_name, currency_code, central_bank,
                inflation_target, mandate_type, country_timezone, indicator_key,
                indicator_name, primary_category, secondary_categories, comparison,
                frequency, unit, is_higher_better_for_currency, importance,
                is_headline_indicator, is_sub_indicator, first_release_at_utc,
                last_release_at_utc, first_reference_period, last_reference_period,
                release_count, actual_count, missing_actual_count,
                exact_duplicate_candidate_count, insufficient_history, notes
            )
            SELECT
                i.id,
                upper(i.country_code),
                c.name,
                upper(c.currency_code),
                c.central_bank,
                c.cb_inflation_target,
                c.cb_mandate_type,
                c.timezone,
                lower(i.canonical_name),
                i.display_name,
                lower(i.primary_category),
                i.secondary_categories,
                i.comparison,
                lower(i.frequency),
                i.unit,
                i.is_higher_better_for_currency,
                i.importance,
                i.importance = 1,
                i.importance > 1 OR cardinality(i.secondary_categories) > 0,
                rs.first_release_at_utc,
                rs.last_release_at_utc,
                rs.first_reference_period,
                rs.last_reference_period,
                coalesce(rs.release_count, 0),
                coalesce(rs.actual_count, 0),
                coalesce(rs.missing_actual_count, 0),
                coalesce(ed.duplicate_count, 0),
                coalesce(rs.actual_count, 0) <
                    CASE lower(i.frequency)
                        WHEN 'daily' THEN 24
                        WHEN 'weekly' THEN 24
                        WHEN 'monthly' THEN 24
                        WHEN 'quarterly' THEN 12
                        WHEN 'annual' THEN 5
                        WHEN 'yearly' THEN 5
                        ELSE 8
                    END,
                i.notes
            FROM indicators i
            JOIN countries c ON c.code = i.country_code
            LEFT JOIN release_stats rs ON rs.indicator_id = i.id
            LEFT JOIN exact_dupes ed ON ed.indicator_id = i.id
            ORDER BY i.country_code, i.canonical_name
            """
        )
    )


async def rebuild_macro_observations(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            WITH release_revisions AS (
                SELECT
                    indicator_id,
                    period,
                    period_start_date,
                    count(*)::integer AS revision_count,
                    count(*) > 1 AS has_revision_history
                FROM indicator_releases
                WHERE indicator_id IS NOT NULL
                GROUP BY indicator_id, period, period_start_date
            ),
            exact_deduped AS (
                SELECT
                    r.*,
                    row_number() OVER (
                        PARTITION BY r.indicator_id, r.period, r.period_start_date,
                                     r.released_at, r.actual, r.previous, r.estimate,
                                     r.change, r.change_percentage
                        ORDER BY r.is_latest DESC, r.retrieved_at DESC, r.id DESC
                    ) AS exact_duplicate_rank
                FROM indicator_releases r
                WHERE r.indicator_id IS NOT NULL
            ),
            actual_stats AS (
                SELECT
                    indicator_id,
                    avg(actual) AS avg_actual,
                    stddev_samp(actual) AS stddev_actual,
                    (percentile_cont(0.25) WITHIN GROUP (ORDER BY actual))::numeric AS q1_actual,
                    (percentile_cont(0.75) WITHIN GROUP (ORDER BY actual))::numeric AS q3_actual
                FROM indicator_releases
                WHERE indicator_id IS NOT NULL AND actual IS NOT NULL
                GROUP BY indicator_id
            )
            INSERT INTO processed.macro_observations (
                source_release_id, indicator_id, country_code, currency_code, country_name,
                indicator_key, indicator_name, primary_category, secondary_categories,
                is_headline_indicator, is_sub_indicator, frequency, unit, period_label,
                reference_period_start, release_timestamp_utc, release_date_utc,
                actual_value, estimate_value, previous_value, surprise_value, change_value,
                change_percentage_value, is_latest, revision_count, has_revision_history,
                data_quality_flags, raw_payload
            )
            SELECT
                r.id,
                i.id,
                upper(i.country_code),
                upper(c.currency_code),
                c.name,
                lower(i.canonical_name),
                i.display_name,
                lower(i.primary_category),
                i.secondary_categories,
                i.importance = 1,
                i.importance > 1 OR cardinality(i.secondary_categories) > 0,
                lower(i.frequency),
                i.unit,
                r.period,
                r.period_start_date,
                r.released_at,
                (r.released_at AT TIME ZONE 'UTC')::date,
                r.actual,
                r.estimate,
                r.previous,
                r.surprise,
                r.change,
                r.change_percentage,
                r.is_latest,
                rr.revision_count,
                rr.has_revision_history,
                array_remove(ARRAY[
                    CASE WHEN r.actual IS NULL THEN 'missing_actual' END,
                    CASE WHEN r.estimate IS NULL THEN 'missing_estimate' END,
                    CASE WHEN r.period_start_date IS NULL THEN 'missing_reference_period' END,
                    CASE WHEN r.period_start_date > (r.released_at AT TIME ZONE 'UTC')::date + 45 THEN 'future_reference_period' END,
                    CASE WHEN r.released_at < r.period_start_date::timestamptz THEN 'release_before_reference_period' END,
                    CASE
                        WHEN s.stddev_actual IS NOT NULL AND s.stddev_actual > 0
                             AND abs((r.actual - s.avg_actual) / s.stddev_actual) > 6
                        THEN 'statistical_outlier_candidate'
                    END,
                    CASE
                        WHEN s.q1_actual IS NOT NULL
                             AND (s.q3_actual - s.q1_actual) > 0
                             AND (
                                r.actual < s.q1_actual - (6 * (s.q3_actual - s.q1_actual))
                                OR r.actual > s.q3_actual + (6 * (s.q3_actual - s.q1_actual))
                             )
                        THEN 'iqr_outlier_candidate'
                    END
                ], NULL)::text[],
                r.raw_payload
            FROM exact_deduped r
            JOIN indicators i ON i.id = r.indicator_id
            JOIN countries c ON c.code = i.country_code
            LEFT JOIN release_revisions rr
                ON rr.indicator_id = r.indicator_id
                AND rr.period IS NOT DISTINCT FROM r.period
                AND rr.period_start_date IS NOT DISTINCT FROM r.period_start_date
            LEFT JOIN actual_stats s ON s.indicator_id = r.indicator_id
            WHERE r.exact_duplicate_rank = 1
            ORDER BY (r.released_at AT TIME ZONE 'UTC')::date, i.country_code, i.canonical_name
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX idx_macro_observations_model_key
            ON processed.macro_observations (
                release_date_utc, country_code, currency_code, indicator_key
            )
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX idx_macro_observations_reference_period
            ON processed.macro_observations (
                country_code, indicator_key, reference_period_start
            )
            """
        )
    )


async def rebuild_quality_issues(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            INSERT INTO processed.data_quality_issues (
                severity, issue_type, table_name, source_id, country_code, currency_code,
                indicator_id, indicator_key, period_label, reference_period_start,
                release_timestamp_utc, details
            )
            SELECT
                CASE
                    WHEN flag IN ('missing_actual', 'missing_reference_period') THEN 'warning'
                    ELSE 'review'
                END,
                flag,
                'processed.macro_observations',
                source_release_id::text,
                country_code,
                currency_code,
                indicator_id,
                indicator_key,
                period_label,
                reference_period_start,
                release_timestamp_utc,
                jsonb_build_object(
                    'actual_value', actual_value,
                    'estimate_value', estimate_value,
                    'previous_value', previous_value,
                    'source_release_id', source_release_id
                )
            FROM processed.macro_observations o
            CROSS JOIN LATERAL unnest(o.data_quality_flags) AS flag
            """
        )
    )
    await session.execute(
        text(
            """
            INSERT INTO processed.data_quality_issues (
                severity, issue_type, table_name, source_id, details
            )
            SELECT
                'warning',
                'unmapped_raw_release',
                'indicator_releases',
                id::text,
                jsonb_build_object(
                    'period', period,
                    'period_start_date', period_start_date,
                    'released_at', released_at,
                    'actual', actual,
                    'raw_payload', raw_payload
                )
            FROM indicator_releases
            WHERE indicator_id IS NULL
            """
        )
    )
    await session.execute(
        text(
            """
            WITH duplicate_groups AS (
                SELECT
                    indicator_id, period, period_start_date, released_at,
                    actual, previous, estimate, change, change_percentage,
                    count(*) AS duplicate_rows,
                    array_agg(id ORDER BY id) AS source_release_ids
                FROM indicator_releases
                GROUP BY indicator_id, period, period_start_date, released_at,
                         actual, previous, estimate, change, change_percentage
                HAVING count(*) > 1
            )
            INSERT INTO processed.data_quality_issues (
                severity, issue_type, table_name, source_id, country_code, currency_code,
                indicator_id, indicator_key, period_label, reference_period_start,
                release_timestamp_utc, details
            )
            SELECT
                'info',
                'exact_duplicate_candidate',
                'indicator_releases',
                source_release_ids[1]::text,
                upper(i.country_code),
                upper(c.currency_code),
                i.id,
                lower(i.canonical_name),
                d.period,
                d.period_start_date,
                d.released_at,
                jsonb_build_object(
                    'duplicate_rows', d.duplicate_rows,
                    'source_release_ids', d.source_release_ids
                )
            FROM duplicate_groups d
            LEFT JOIN indicators i ON i.id = d.indicator_id
            LEFT JOIN countries c ON c.code = i.country_code
            """
        )
    )
    await session.execute(
        text(
            """
            INSERT INTO processed.data_quality_issues (
                severity, issue_type, table_name, country_code, currency_code,
                indicator_id, indicator_key, details
            )
            SELECT
                'warning',
                'insufficient_history',
                'processed.indicator_metadata',
                country_code,
                currency_code,
                indicator_id,
                indicator_key,
                jsonb_build_object(
                    'frequency', frequency,
                    'actual_count', actual_count,
                    'release_count', release_count,
                    'first_release_at_utc', first_release_at_utc,
                    'last_release_at_utc', last_release_at_utc
                )
            FROM processed.indicator_metadata
            WHERE insufficient_history
            """
        )
    )
    await session.execute(
        text(
            """
            WITH latest_groups AS (
                SELECT indicator_id, period, period_start_date, count(*) AS latest_rows
                FROM indicator_releases
                WHERE is_latest IS TRUE AND indicator_id IS NOT NULL
                GROUP BY indicator_id, period, period_start_date
                HAVING count(*) > 1
            )
            INSERT INTO processed.data_quality_issues (
                severity, issue_type, table_name, country_code, currency_code,
                indicator_id, indicator_key, period_label, reference_period_start, details
            )
            SELECT
                'review',
                'multiple_latest_rows',
                'indicator_releases',
                upper(i.country_code),
                upper(c.currency_code),
                i.id,
                lower(i.canonical_name),
                g.period,
                g.period_start_date,
                jsonb_build_object('latest_rows', g.latest_rows)
            FROM latest_groups g
            JOIN indicators i ON i.id = g.indicator_id
            JOIN countries c ON c.code = i.country_code
            """
        )
    )


async def build_quality_report(
    session: AsyncSession, raw_profile: Mapping[str, Any]
) -> dict[str, Any]:
    issue_counts = await fetch_all(
        session,
        """
        SELECT severity, issue_type, count(*) AS issue_count
        FROM processed.data_quality_issues
        GROUP BY severity, issue_type
        ORDER BY severity, issue_type
        """,
    )
    country_counts = await fetch_all(
        session,
        """
        SELECT country_code, count(*) AS observations, count(actual_value) AS actual_values,
               count(*) FILTER (WHERE cardinality(data_quality_flags) > 0) AS flagged_observations
        FROM processed.macro_observations
        GROUP BY country_code
        ORDER BY country_code
        """,
    )
    insufficient_history = await fetch_all(
        session,
        """
        SELECT country_code, indicator_key, indicator_name, frequency, actual_count, release_count
        FROM processed.indicator_metadata
        WHERE insufficient_history
        ORDER BY country_code, actual_count, indicator_key
        """,
    )
    release_alignment = await fetch_all(
        session,
        """
        SELECT country_code, indicator_key, issue_type, count(*) AS issue_count
        FROM processed.data_quality_issues
        WHERE issue_type IN ('future_reference_period', 'release_before_reference_period')
        GROUP BY country_code, indicator_key, issue_type
        ORDER BY issue_count DESC, country_code, indicator_key
        """,
    )
    outliers = await fetch_all(
        session,
        """
        SELECT country_code, indicator_key, period_label, reference_period_start,
               release_timestamp_utc, actual_value, data_quality_flags
        FROM processed.macro_observations
        WHERE data_quality_flags && ARRAY['statistical_outlier_candidate', 'iqr_outlier_candidate']::text[]
        ORDER BY country_code, indicator_key, release_timestamp_utc
        """,
    )
    summary = await fetch_one(
        session,
        """
        SELECT
            (SELECT count(*) FROM processed.indicator_metadata) AS indicators,
            (SELECT count(*) FROM processed.macro_observations) AS observations,
            (SELECT count(*) FROM indicator_releases) AS raw_releases,
            (SELECT count(*) FROM processed.data_quality_issues) AS quality_issues,
            (SELECT count(*) FROM processed.macro_observations WHERE cardinality(data_quality_flags) > 0) AS flagged_observations
        """
    )
    return {
        "summary": summary,
        "missingness_by_raw_table": {
            name: profile["missingness"]
            for name, profile in raw_profile["raw_tables"].items()
        },
        "issue_counts": issue_counts,
        "observation_counts_by_country": country_counts,
        "release_date_alignment_issues": release_alignment,
        "outlier_candidates": outliers,
        "indicators_with_insufficient_history": insufficient_history,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


async def write_dataset_profile(
    session: AsyncSession, raw_profile: Mapping[str, Any], report: Mapping[str, Any]
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO processed.dataset_profile (object_layer, object_name, profile_json)
            VALUES
                ('raw', 'all_source_tables', CAST(:raw_profile AS jsonb)),
                ('processed', 'quality_report', CAST(:report AS jsonb))
            """
        ),
        {
            "raw_profile": json.dumps(raw_profile, default=json_default),
            "report": json.dumps(report, default=json_default),
        },
    )


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


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, default=json_default, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def write_readme(output_path: Path) -> None:
    (output_path / "README.md").write_text(
        "\n".join(
            [
                "# Processed Macro Dataset",
                "",
                "Generated by `python -m scripts.build_processed_dataset`.",
                "",
                "Use `processed.macro_observations` or `data/processed/macro_observations.csv` for the next scoring/modeling step.",
                "The raw collection tables remain untouched.",
                "",
                "Files:",
                "- `indicator_metadata.csv`: one row per canonical indicator with coverage and history status.",
                "- `macro_observations.csv`: modeling-ready long table indexed by release date, country, currency, and indicator.",
                "- `data_quality_issues.csv`: transparent review queue for missing values, duplicates, outliers, and alignment issues.",
                "- `raw_structure_report.json`: raw table structure, missingness, coverage, frequency, and duplicate profile.",
                "- `data_quality_report.json`: rollup of quality issues and review candidates.",
                "",
            ]
        ),
        encoding="utf-8",
    )


async def fetch_all(
    session: AsyncSession, statement: str, params: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    result = await session.execute(text(statement), params or {})
    return [dict(row) for row in result.mappings().all()]


async def fetch_one(
    session: AsyncSession, statement: str, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    result = await session.execute(text(statement), params or {})
    row = result.mappings().one_or_none()
    return dict(row) if row else {}


async def fetch_scalar(
    session: AsyncSession, statement: str, params: Mapping[str, Any] | None = None
) -> Any:
    result = await session.execute(text(statement), params or {})
    return result.scalar_one()
