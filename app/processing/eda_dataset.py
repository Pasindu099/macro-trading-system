"""Create an EDA-ready macro dataset from canonical release data."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope


CENTRAL_BANK_COUNTRIES = {
    "US": "FED",
    "CA": "BOC",
    "EU": "ECB",
    "UK": "BOE",
    "CH": "SNB",
    "AU": "RBA",
    "NZ": "RBNZ",
    "JP": "BOJ",
}

EDA_TABLES = ("processed.eda_observations", "processed.eda_profile")


def json_default(value: Any) -> str | float | int | bool | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


async def build_eda_dataset(
    output_dir: Path | str = Path("data/eda"),
    *,
    scaling: str = "both",
) -> dict[str, Any]:
    """Build cleaned EDA observations and persist them to DB and CSV/JSON files.

    The raw collection tables stay untouched. This step keeps latest mapped
    releases with real numeric actual values, filters to the central banks used
    by the dashboard, and adds normalized values for cross-indicator analysis.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    async with session_scope() as session:
        raw_rows = await load_raw_release_rows(session)
        raw_df = pd.DataFrame(raw_rows)
        cleaned_df, profile = preprocess_for_eda(raw_df, scaling=scaling)
        await recreate_eda_tables(session)
        await write_eda_observations(session, cleaned_df)
        await write_eda_profile(session, profile)

    csv_path = output_path / "eda_observations.csv"
    report_path = output_path / "eda_profile.json"
    readme_path = output_path / "README.md"
    cleaned_df.to_csv(csv_path, index=False)
    report_path.write_text(
        json.dumps(profile, default=json_default, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_readme(readme_path)

    return {
        "output_dir": str(output_path),
        "tables": list(EDA_TABLES),
        "files": [csv_path.name, report_path.name, readme_path.name],
        "rows": int(len(cleaned_df)),
        "indicators": int(cleaned_df["indicator_key"].nunique()) if not cleaned_df.empty else 0,
        "central_banks": (
            sorted(cleaned_df["central_bank_code"].dropna().unique().tolist())
            if not cleaned_df.empty
            else []
        ),
    }


async def load_raw_release_rows(session: AsyncSession) -> list[dict[str, Any]]:
    """Load mapped release rows through SQLAlchemy before pandas cleaning."""
    result = await session.execute(
        text(
            """
            SELECT
                r.id AS source_release_id,
                coalesce(r.period_start_date, (r.released_at AT TIME ZONE 'UTC')::date) AS date,
                i.display_name AS indicator,
                lower(i.canonical_name) AS indicator_key,
                r.actual AS value,
                r.estimate AS estimate_value,
                r.previous AS previous_value,
                r.surprise AS surprise_value,
                lower(i.frequency) AS frequency,
                upper(i.country_code) AS country,
                upper(c.currency_code) AS currency_code,
                c.name AS country_name,
                c.central_bank,
                i.primary_category,
                i.importance,
                r.period AS period_label,
                r.released_at AS release_timestamp_utc,
                r.retrieved_at AS retrieved_at_utc
            FROM indicator_releases r
            JOIN indicators i ON i.id = r.indicator_id
            JOIN countries c ON c.code = i.country_code
            WHERE
                r.indicator_id IS NOT NULL
                AND r.is_latest IS TRUE
            ORDER BY
                i.country_code,
                i.canonical_name,
                coalesce(r.period_start_date, (r.released_at AT TIME ZONE 'UTC')::date),
                r.released_at
            """
        )
    )
    return [dict(row) for row in result.mappings().all()]


def preprocess_for_eda(raw_df: pd.DataFrame, *, scaling: str = "both") -> tuple[pd.DataFrame, dict[str, Any]]:
    """Clean, type, filter, dedupe, and normalize macro observations."""
    started_at = datetime.utcnow().isoformat() + "Z"
    original_rows = len(raw_df)

    if raw_df.empty:
        return raw_df, {
            "generated_at": started_at,
            "input_rows": 0,
            "output_rows": 0,
            "notes": ["No source rows were available."],
        }

    df = raw_df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["release_timestamp_utc"] = pd.to_datetime(
        df["release_timestamp_utc"], errors="coerce", utc=True
    )
    df["retrieved_at_utc"] = pd.to_datetime(df["retrieved_at_utc"], errors="coerce", utc=True)

    numeric_columns = ["value", "estimate_value", "previous_value", "surprise_value"]
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    missing_date_rows = int(df["date"].isna().sum())
    missing_value_rows = int(df["value"].isna().sum())
    unsupported_country_rows = int((~df["country"].isin(CENTRAL_BANK_COUNTRIES)).sum())
    future_observation_rows = int(
        (pd.to_datetime(df["date"], errors="coerce").dt.date > datetime.utcnow().date()).sum()
    )

    df["central_bank_code"] = df["country"].map(CENTRAL_BANK_COUNTRIES)
    df["frequency"] = df.apply(infer_frequency, axis=1)

    df = df.dropna(subset=["date", "value", "central_bank_code"])
    df = df[df["date"] <= datetime.utcnow().date()]
    df = df.sort_values(
        ["central_bank_code", "indicator_key", "date", "release_timestamp_utc", "source_release_id"]
    )
    duplicate_rows = int(
        df.duplicated(subset=["central_bank_code", "indicator_key", "date"]).sum()
    )
    df = df.drop_duplicates(
        subset=["central_bank_code", "indicator_key", "date"],
        keep="last",
    )

    df["year"] = pd.to_datetime(df["date"]).dt.year
    df["month"] = pd.to_datetime(df["date"]).dt.month
    df["quarter"] = pd.to_datetime(df["date"]).dt.quarter
    df["value_zscore"] = grouped_zscore(df)
    df["value_minmax"] = grouped_minmax(df)
    df["is_outlier_iqr"] = grouped_iqr_outlier(df)

    if scaling == "zscore":
        df["value_normalized"] = df["value_zscore"]
    elif scaling == "minmax":
        df["value_normalized"] = df["value_minmax"]
    else:
        df["value_normalized"] = df["value_zscore"]

    output_columns = [
        "source_release_id",
        "date",
        "country",
        "central_bank_code",
        "currency_code",
        "country_name",
        "central_bank",
        "indicator",
        "indicator_key",
        "primary_category",
        "importance",
        "frequency",
        "period_label",
        "release_timestamp_utc",
        "retrieved_at_utc",
        "value",
        "estimate_value",
        "previous_value",
        "surprise_value",
        "value_zscore",
        "value_minmax",
        "value_normalized",
        "is_outlier_iqr",
        "year",
        "quarter",
        "month",
    ]
    df = df[output_columns].sort_values(["date", "central_bank_code", "indicator_key"])

    profile = build_profile(
        df,
        generated_at=started_at,
        input_rows=original_rows,
        missing_date_rows=missing_date_rows,
        missing_value_rows=missing_value_rows,
        unsupported_country_rows=unsupported_country_rows,
        future_observation_rows=future_observation_rows,
        duplicate_rows=duplicate_rows,
        scaling=scaling,
    )
    return df, profile


def infer_frequency(row: pd.Series) -> str:
    """Use mapped metadata first, then infer from indicator/period text."""
    raw_frequency = str(row.get("frequency") or "").strip().lower()
    if raw_frequency in {"daily", "weekly", "monthly", "quarterly", "annual", "yearly"}:
        return "yearly" if raw_frequency == "annual" else raw_frequency

    indicator = str(row.get("indicator") or "").lower()
    period = str(row.get("period_label") or "").lower()
    if "quarter" in indicator or period.startswith("q"):
        return "quarterly"
    if "year" in indicator or "annual" in indicator:
        return "yearly"
    return "monthly"


def grouped_zscore(df: pd.DataFrame) -> pd.Series:
    grouped = df.groupby(["central_bank_code", "indicator_key"])["value"]
    mean = grouped.transform("mean")
    std = grouped.transform("std").replace(0, pd.NA)
    return ((df["value"] - mean) / std).fillna(0.0)


def grouped_minmax(df: pd.DataFrame) -> pd.Series:
    grouped = df.groupby(["central_bank_code", "indicator_key"])["value"]
    min_value = grouped.transform("min")
    max_value = grouped.transform("max")
    spread = (max_value - min_value).replace(0, pd.NA)
    return ((df["value"] - min_value) / spread).fillna(0.5)


def grouped_iqr_outlier(df: pd.DataFrame) -> pd.Series:
    grouped = df.groupby(["central_bank_code", "indicator_key"])["value"]
    q1 = grouped.transform(lambda values: values.quantile(0.25))
    q3 = grouped.transform(lambda values: values.quantile(0.75))
    iqr = q3 - q1
    lower = q1 - (3 * iqr)
    upper = q3 + (3 * iqr)
    return (df["value"] < lower) | (df["value"] > upper)


def build_profile(
    df: pd.DataFrame,
    *,
    generated_at: str,
    input_rows: int,
    missing_date_rows: int,
    missing_value_rows: int,
    unsupported_country_rows: int,
    future_observation_rows: int,
    duplicate_rows: int,
    scaling: str,
) -> dict[str, Any]:
    return {
        "generated_at": generated_at,
        "scaling": scaling,
        "input_rows": input_rows,
        "output_rows": int(len(df)),
        "dropped_or_excluded": {
            "missing_date_rows": missing_date_rows,
            "missing_value_rows": missing_value_rows,
            "unsupported_country_rows": unsupported_country_rows,
            "future_observation_rows": future_observation_rows,
            "duplicate_country_indicator_date_rows": duplicate_rows,
        },
        "central_bank_counts": df["central_bank_code"].value_counts().sort_index().to_dict(),
        "frequency_counts": df["frequency"].value_counts().sort_index().to_dict(),
        "category_counts": df["primary_category"].value_counts().sort_index().to_dict(),
        "date_range": {
            "start": df["date"].min().isoformat() if not df.empty else None,
            "end": df["date"].max().isoformat() if not df.empty else None,
        },
        "outlier_iqr_count": int(df["is_outlier_iqr"].sum()) if not df.empty else 0,
        "indicator_count": int(df["indicator_key"].nunique()) if not df.empty else 0,
    }


async def recreate_eda_tables(session: AsyncSession) -> None:
    statements = [
        "CREATE SCHEMA IF NOT EXISTS processed",
        "DROP TABLE IF EXISTS processed.eda_observations",
        "DROP TABLE IF EXISTS processed.eda_profile",
        """
        CREATE TABLE processed.eda_observations (
            eda_observation_id bigserial PRIMARY KEY,
            source_release_id bigint NOT NULL UNIQUE,
            date date NOT NULL,
            country varchar(2) NOT NULL,
            central_bank_code text NOT NULL,
            currency_code varchar(3) NOT NULL,
            country_name text NOT NULL,
            central_bank text NOT NULL,
            indicator text NOT NULL,
            indicator_key text NOT NULL,
            primary_category text NOT NULL,
            importance smallint NOT NULL,
            frequency text NOT NULL,
            period_label text,
            release_timestamp_utc timestamptz,
            retrieved_at_utc timestamptz,
            value double precision NOT NULL,
            estimate_value double precision,
            previous_value double precision,
            surprise_value double precision,
            value_zscore double precision NOT NULL,
            value_minmax double precision NOT NULL,
            value_normalized double precision NOT NULL,
            is_outlier_iqr boolean NOT NULL,
            year integer NOT NULL,
            quarter integer NOT NULL,
            month integer NOT NULL,
            processed_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE processed.eda_profile (
            profile_id bigserial PRIMARY KEY,
            profile_json jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE INDEX idx_eda_observations_series
        ON processed.eda_observations (central_bank_code, indicator_key, date)
        """,
        """
        CREATE INDEX idx_eda_observations_date
        ON processed.eda_observations (date, central_bank_code)
        """,
    ]
    for statement in statements:
        await session.execute(text(statement))


async def write_eda_observations(session: AsyncSession, df: pd.DataFrame) -> None:
    if df.empty:
        return

    records = [
        {key: normalize_db_value(value) for key, value in row.items()}
        for row in df.to_dict(orient="records")
    ]
    await session.execute(
        text(
            """
            INSERT INTO processed.eda_observations (
                source_release_id, date, country, central_bank_code, currency_code,
                country_name, central_bank, indicator, indicator_key, primary_category,
                importance, frequency, period_label, release_timestamp_utc,
                retrieved_at_utc, value, estimate_value, previous_value,
                surprise_value, value_zscore, value_minmax, value_normalized,
                is_outlier_iqr, year, quarter, month
            )
            VALUES (
                :source_release_id, :date, :country, :central_bank_code, :currency_code,
                :country_name, :central_bank, :indicator, :indicator_key, :primary_category,
                :importance, :frequency, :period_label, :release_timestamp_utc,
                :retrieved_at_utc, :value, :estimate_value, :previous_value,
                :surprise_value, :value_zscore, :value_minmax, :value_normalized,
                :is_outlier_iqr, :year, :quarter, :month
            )
            """
        ),
        records,
    )


async def write_eda_profile(session: AsyncSession, profile: dict[str, Any]) -> None:
    await session.execute(
        text(
            """
            INSERT INTO processed.eda_profile (profile_json)
            VALUES (CAST(:profile AS jsonb))
            """
        ),
        {"profile": json.dumps(profile, default=json_default)},
    )


def normalize_db_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if hasattr(value, "item"):
        return value.item()
    return value


def write_readme(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "# EDA Macro Dataset",
                "",
                "Generated by `python -m scripts.build_eda_dataset`.",
                "",
                "This dataset is intended for exploratory analysis. It keeps latest mapped",
                "observations with numeric actual values for FED, BOC, ECB, BOE, SNB, RBA,",
                "RBNZ, and BOJ countries/central banks.",
                "",
                "Primary table: `processed.eda_observations`.",
                "",
                "Useful columns:",
                "- `date`, `central_bank_code`, `indicator_key`, `indicator`, `value`",
                "- `frequency`, `primary_category`, `year`, `quarter`, `month`",
                "- `value_zscore`, `value_minmax`, `value_normalized`",
                "- `is_outlier_iqr` for quick review before charting/modeling",
                "",
            ]
        ),
        encoding="utf-8",
    )
