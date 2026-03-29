from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_FILE = PROJECT_ROOT / "data" / "staging" / "standardized_releases.csv"
OUTPUT_FILE = PROJECT_ROOT / "config" / "indicator_mapping_template.csv"

def main() -> None:
    df = pd.read_csv(INPUT_FILE)

    indicators = (
        df["indicator"]
        .dropna()
        .astype(str)
        .str.strip()
        .drop_duplicates()
        .sort_values()
        .reset_index(drop=True)
    )

    mapping_df = pd.DataFrame({
        "raw_indicator_name": indicators,
        "standard_indicator_name": "",
        "macro_bucket": "",
        "policy_channel": "",
        "better_direction": "",
        "weight": "",
        "notes": "",
    })

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    mapping_df.to_csv(OUTPUT_FILE, index=False)
    print(f"Saved template to: {OUTPUT_FILE}")
    print(f"Unique indicators: {len(mapping_df)}")

if __name__ == "__main__":
    main()