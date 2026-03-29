from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = PROJECT_ROOT / "data" / "staging" / "standardized_releases.csv"
OUTPUT_FILE = PROJECT_ROOT / "data" / "processed" / "macro_time_series.csv"

def main():
    df = pd.read_csv(INPUT_FILE)

    # Use standardized names
    df = df.copy()

    # Convert date
    df["release_date"] = pd.to_datetime(df["release_date"])

    # Pivot to wide format
    pivot_df = df.pivot_table(
        index=["release_date", "country"],
        columns="indicator",
        values="actual",
        aggfunc="last"
    ).reset_index()

    # Sort
    pivot_df = pivot_df.sort_values(["country", "release_date"])

    # Save
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    pivot_df.to_csv(OUTPUT_FILE, index=False)

    print("✅ Time series dataset created!")

if __name__ == "__main__":
    main()