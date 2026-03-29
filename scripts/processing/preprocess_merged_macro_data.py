from pathlib import Path
import pandas as pd


# -----------------------------
# Paths
# -----------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_FILE = PROJECT_ROOT / "data" / "staging" / "merged_raw_releases.csv"
OUTPUT_DIR = PROJECT_ROOT / "data" / "staging"
OUTPUT_FILE = OUTPUT_DIR / "standardized_releases.csv"


# -----------------------------
# Helper functions
# -----------------------------
def clean_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """Strip spaces from column names."""
    df.columns = [col.strip() for col in df.columns]
    return df


def drop_junk_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Drop empty/junk columns often created during Excel export."""
    cols_to_drop = []

    for col in df.columns:
        col_lower = col.lower().strip()

        # Drop unnamed columns
        if col_lower.startswith("unnamed"):
            cols_to_drop.append(col)

    df = df.drop(columns=cols_to_drop, errors="ignore")

    # Drop columns that are entirely empty
    df = df.dropna(axis=1, how="all")

    return df


def unify_release_date(df: pd.DataFrame) -> pd.DataFrame:
    """
    Create one clean release_date column using the best available date field.
    Priority:
    1. Date
    2. Release date
    3. elease date
    """
    date_candidates = []

    for candidate in ["Date", "Release date", "elease date"]:
        if candidate in df.columns:
            date_candidates.append(pd.to_datetime(df[candidate], errors="coerce"))

    if not date_candidates:
        df["release_date"] = pd.NaT
        return df

    release_date = date_candidates[0].copy()

    for series in date_candidates[1:]:
        release_date = release_date.fillna(series)

    df["release_date"] = release_date.dt.date
    return df


def clean_numeric_column(series: pd.Series) -> pd.Series:
    """
    Convert a mixed text numeric column into float.
    Handles:
    - commas
    - percent signs
    - empty strings
    """
    cleaned = (
        series.astype(str)
        .str.strip()
        .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
    )
    return pd.to_numeric(cleaned, errors="coerce")


def standardize_category(value: str) -> str:
    """Standardize raw category names."""
    if pd.isna(value):
        return value

    value = str(value).strip()

    mapping = {
        "Employment Data": "Employment",
        "Employment": "Employment",
        "Inflation Data": "Inflation",
        "Inflation data": "Inflation",
        "Inflation": "Inflation",
        "Monetory Policy": "Monetary Policy",
        "Monetary Policy": "Monetary Policy",
        "Economic Activity": "Economic Activity",
    }

    return mapping.get(value, value)


def standardize_country(value: str) -> str:
    """Standardize raw country folder names."""
    if pd.isna(value):
        return value

    value = str(value).strip()

    mapping = {
        "US Data": "United States",
        "UK Data": "United Kingdom",
        "Australia Data": "Australia",
        "Canada Data": "Canada",
        "Swiss Data": "Switzerland",
    }

    return mapping.get(value, value)


def map_currency(country: str) -> str:
    """Map standardized country to currency code."""
    mapping = {
        "United States": "USD",
        "United Kingdom": "GBP",
        "Australia": "AUD",
        "Canada": "CAD",
        "Switzerland": "CHF",
    }
    return mapping.get(country, pd.NA)


def clean_text_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Strip whitespace from key text columns."""
    for col in ["country_raw", "category_raw", "indicator_raw", "source_file"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
            df[col] = df[col].replace({"nan": pd.NA, "None": pd.NA})
    return df


def remove_bad_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keep rows that have essential metadata.
    Do NOT remove rows just because numeric values are missing.
    """
    
    required_cols = ["release_date", "indicator_raw", "country_raw", "category_raw"]
    
    existing_required_cols = [col for col in required_cols if col in df.columns]

    if existing_required_cols:
        df = df.dropna(subset=existing_required_cols)

    return df


# -----------------------------
# Main pipeline
# -----------------------------
def main() -> None:
    print(f"Reading input file: {INPUT_FILE}")

    df = pd.read_csv(INPUT_FILE)
    print(f"Initial shape: {df.shape}")

    # 1. Clean column names
    df = clean_column_names(df)

    # 2. Drop junk columns
    df = drop_junk_columns(df)
    print(f"After dropping junk columns: {df.shape}")

    # 3. Clean text columns
    df = clean_text_columns(df)

    # 4. Create unified release date
    df = unify_release_date(df)

    # 5. Standardize numeric columns
    if "Actual" in df.columns:
        df["actual"] = clean_numeric_column(df["Actual"])
    else:
        df["actual"] = pd.NA

    if "Forecast" in df.columns:
        df["forecast"] = clean_numeric_column(df["Forecast"])
    else:
        df["forecast"] = pd.NA

    if "Previous" in df.columns:
        df["previous"] = clean_numeric_column(df["Previous"])
    else:
        df["previous"] = pd.NA

    # 6. Standardize metadata
    if "country_raw" in df.columns:
        df["country"] = df["country_raw"].apply(standardize_country)
        df["currency"] = df["country"].apply(map_currency)
    else:
        df["country"] = pd.NA
        df["currency"] = pd.NA

    if "category_raw" in df.columns:
        df["category"] = df["category_raw"].apply(standardize_category)
    else:
        df["category"] = pd.NA

    if "indicator_raw" in df.columns:
        df["indicator"] = df["indicator_raw"]
    else:
        df["indicator"] = pd.NA

    # 7. Remove bad rows
    df = remove_bad_rows(df)
    print(f"After removing bad rows: {df.shape}")

    # 8. Create helper analytics fields
    df["surprise"] = df["actual"] - df["forecast"]
    df["change_from_previous"] = df["actual"] - df["previous"]
    df["forecast_available"] = df["forecast"].notna().astype(int)

    # 9. Keep final columns in clean order
    final_columns = [
        "release_date",
        "actual",
        "forecast",
        "previous",
        "surprise",
        "change_from_previous",
        "forecast_available",
        "country_raw",
        "country",
        "currency",
        "category_raw",
        "category",
        "indicator_raw",
        "indicator",
        "source_file",
    ]

    final_columns = [col for col in final_columns if col in df.columns]
    df = df[final_columns].sort_values(by=["country", "category", "indicator", "release_date"])

    # 10. Save output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_FILE, index=False)

    print(f"Saved cleaned file to: {OUTPUT_FILE}")
    print("Preprocessing complete.")


if __name__ == "__main__":
    main()