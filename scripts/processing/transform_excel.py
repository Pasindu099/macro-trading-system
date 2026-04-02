import pandas as pd
import re
from pathlib import Path

from scripts.processing.value_cleaner import clean_numeric_value


def standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Rename columns to standard names.
    """
    column_map = {
        "release date": "release_date",
        "date": "date",
        "actual": "actual_value",
        "forecast": "forecast_value",
        "previous": "previous_value",
    }

    df.columns = [str(col).strip().lower() for col in df.columns]
    df = df.rename(columns=column_map)

    return df


def normalize_single_date(value):
    """
    Convert different date formats into pandas datetime.
    Handles:
    - normal date strings
    - pandas timestamps
    - numpy/pandas numeric timestamps
    """
    if pd.isna(value):
        return pd.NaT

    # Already datetime-like
    if isinstance(value, pd.Timestamp):
        return value

    # Numeric values (including numpy float/int)
    if isinstance(value, (int, float)):
        # Try Excel serial date first
        try:
            if 20000 < value < 60000:
                return pd.to_datetime(value, unit="D", origin="1899-12-30")
        except Exception:
            pass

        # Try nanoseconds timestamp
        try:
            return pd.to_datetime(value)
        except Exception:
            return pd.NaT

    # Strings
    try:
        return pd.to_datetime(value)
    except Exception:
        return pd.NaT


def extract_dates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Extract release_date and period_date from combined release_date column.
    Example: 'Mar 11, 2026 (Feb)'
    """

    def parse_row(value):
        if pd.isna(value):
            return pd.NaT, pd.NaT

        text = str(value)

        # Extract release date
        release_match = re.search(r"[A-Za-z]{3} \d{1,2}, \d{4}", text)
        release_date = pd.to_datetime(release_match.group()) if release_match else pd.NaT

        # Extract period month inside brackets
        period_match = re.search(r"\((.*?)\)", text)

        if period_match and pd.notna(release_date):
            month_str = period_match.group(1).strip()

            try:
                period_date = pd.to_datetime(f"{month_str} {release_date.year}")

                # Handle Dec released in Jan, etc.
                if period_date > release_date:
                    period_date = period_date - pd.DateOffset(years=1)

            except Exception:
                period_date = pd.NaT
        else:
            period_date = pd.NaT

        return release_date, period_date

    df[["release_date", "period_date"]] = df["release_date"].apply(
        lambda x: pd.Series(parse_row(x))
    )

    return df


def clean_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    numeric_cols = [
        "actual_value",
        "forecast_value",
        "previous_value",
    ]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].apply(clean_numeric_value)

    return df


def normalize_date_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure period_date and release_date are proper datetimes.
    """
    if "period_date" in df.columns:
        df["period_date"] = df["period_date"].apply(normalize_single_date)

    if "release_date" in df.columns:
        df["release_date"] = df["release_date"].apply(normalize_single_date)

    return df


def transform_excel(file_path: Path) -> pd.DataFrame:
    df = pd.read_excel(file_path)

    df = standardize_columns(df)

    if "release_date" in df.columns:
        df = extract_dates(df)
    else:
        if "date" in df.columns:
            df["period_date"] = df["date"]
            df["release_date"] = pd.NaT
        else:
            raise ValueError("No valid date column found")

    df = normalize_date_columns(df)
    df = clean_numeric_columns(df)

    # If period_date is missing but release_date exists, use release_date
    if "period_date" in df.columns and "release_date" in df.columns:
        df["period_date"] = df["period_date"].fillna(df["release_date"])

    # Ensure expected columns exist even if missing in source file
    for col in ["actual_value", "forecast_value", "previous_value"]:
        if col not in df.columns:
            df[col] = None

    df = df[
        [
            "period_date",
            "release_date",
            "actual_value",
            "forecast_value",
            "previous_value",
        ]
    ]

    return df


if __name__ == "__main__":
    file_path = Path(
        r"C:\Users\wpmpo\OneDrive\Documents\macro_trading_system\data\raw\US Data\Inflation data\U.S. Consumer Price Index (CPI) MoM.xlsx"
    )

    df = transform_excel(file_path)

    print("\n=== TRANSFORMED DATA ===")
    print(df.head())

    print("\n=== DATA TYPES ===")
    print(df.dtypes)