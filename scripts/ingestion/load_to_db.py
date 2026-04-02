from pathlib import Path

import pandas as pd
from sqlalchemy import text

from scripts.utils.db_connection import get_engine
from scripts.processing.transform_excel import transform_excel
from scripts.ingestion.inspect_excel import extract_metadata


def get_country_id(conn, country_name):
    result = conn.execute(
        text("SELECT country_id FROM countries WHERE country_name = :name"),
        {"name": country_name},
    ).fetchone()

    if result is None:
        raise ValueError(f"Country not found in database: {country_name}")

    return result[0]


def get_category_id(conn, category_name):
    result = conn.execute(
        text("SELECT category_id FROM categories WHERE category_name = :name"),
        {"name": category_name},
    ).fetchone()

    if result is None:
        raise ValueError(f"Category not found in database: {category_name}")

    return result[0]


def get_or_create_indicator(conn, country_id, category_id, raw_name):
    result = conn.execute(
        text("""
            SELECT indicator_id
            FROM indicators
            WHERE country_id = :country_id
              AND category_id = :category_id
              AND original_indicator_name = :raw_name
        """),
        {
            "country_id": country_id,
            "category_id": category_id,
            "raw_name": raw_name,
        },
    ).fetchone()

    if result:
        return result[0]

    result = conn.execute(
        text("""
            INSERT INTO indicators (
                country_id,
                category_id,
                indicator_name,
                original_indicator_name
            )
            VALUES (
                :country_id,
                :category_id,
                :indicator_name,
                :original_indicator_name
            )
            RETURNING indicator_id
        """),
        {
            "country_id": country_id,
            "category_id": category_id,
            "indicator_name": raw_name,
            "original_indicator_name": raw_name,
        },
    )

    return result.fetchone()[0]


def to_python_null(value):
    if pd.isna(value):
        return None

    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()

    return value


def insert_observations(conn, df, indicator_id):
    for _, row in df.iterrows():
        if (
            pd.isna(row["period_date"])
            and pd.isna(row["release_date"])
            and pd.isna(row["actual_value"])
            and pd.isna(row["forecast_value"])
            and pd.isna(row["previous_value"])
        ):
            continue

        if pd.isna(row["period_date"]):
            continue

        conn.execute(
            text("""
                INSERT INTO observations (
                    indicator_id,
                    period_date,
                    release_date,
                    actual_value,
                    forecast_value,
                    previous_value
                )
                VALUES (
                    :indicator_id,
                    :period_date,
                    :release_date,
                    :actual,
                    :forecast,
                    :previous
                )
                ON CONFLICT DO NOTHING
            """),
            {
                "indicator_id": indicator_id,
                "period_date": to_python_null(row["period_date"]),
                "release_date": to_python_null(row["release_date"]),
                "actual": to_python_null(row["actual_value"]),
                "forecast": to_python_null(row["forecast_value"]),
                "previous": to_python_null(row["previous_value"]),
            },
        )


def load_file_to_db(file_path: Path):
    engine = get_engine()

    metadata = extract_metadata(file_path)
    df = transform_excel(file_path)

    with engine.begin() as conn:
        country_id = get_country_id(conn, metadata["country_name"])
        category_id = get_category_id(conn, metadata["category_name"])

        indicator_id = get_or_create_indicator(
            conn,
            country_id,
            category_id,
            metadata["raw_indicator_name"],
        )

        insert_observations(conn, df, indicator_id)

    print(f"Loaded: {file_path.name}")


def load_all_files(base_folder: Path):
    all_files = list(base_folder.rglob("*.xlsx"))

    print(f"Found {len(all_files)} Excel files")

    failures = []
    success_count = 0

    for file_path in all_files:
        try:
            load_file_to_db(file_path)
            success_count += 1
        except Exception as e:
            print(f"❌ Failed: {file_path.name}")
            print("Error:", e)

            failures.append(
                {
                    "file_name": file_path.name,
                    "full_path": str(file_path),
                    "error": str(e),
                }
            )

    print(f"\nSuccessful files: {success_count}")
    print(f"Failed files: {len(failures)}")

    if failures:
        failures_df = pd.DataFrame(failures)
        output_path = Path("data/processed/load_failures.csv")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        failures_df.to_csv(output_path, index=False)

        print(f"Failure report saved to: {output_path}")


if __name__ == "__main__":
    base_folder = Path(
        r"C:\Users\wpmpo\OneDrive\Documents\macro_trading_system\data\raw"
    )

    load_all_files(base_folder)