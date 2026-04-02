from pathlib import Path
import pandas as pd


# =========================================================
# PATHS
# =========================================================
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_FILE = PROJECT_ROOT / "data" / "staging" / "standardized_releases.csv"
OUTPUT_FILE = PROJECT_ROOT / "data" / "processed" / "policy_impact_dataset.csv"


# =========================================================
# LOAD DATA
# =========================================================
df = pd.read_csv(DATA_FILE)

df["release_date"] = pd.to_datetime(df["release_date"], errors="coerce")
df["actual"] = pd.to_numeric(df["actual"], errors="coerce")

df = df.dropna(subset=["release_date", "actual"])


# =========================================================
# IDENTIFY POLICY DATA
# =========================================================
policy_keywords = [
    "interest rate",
    "rate decision"
]

policy_df = df[
    df["indicator"].str.lower().str.contains("|".join(policy_keywords), na=False)
].copy()

policy_df = policy_df.sort_values(["country", "release_date"])


# =========================================================
# CALCULATE RATE CHANGE
# =========================================================
policy_df["rate_change"] = policy_df.groupby("country")["actual"].diff()

def classify_direction(x):
    if pd.isna(x):
        return "Hold"
    elif x > 0:
        return "Hike"
    elif x < 0:
        return "Cut"
    else:
        return "Hold"

policy_df["direction"] = policy_df["rate_change"].apply(classify_direction)


# =========================================================
# BUILD POLICY DATASET
# =========================================================
final_rows = []

for country in policy_df["country"].unique():

    country_policy = policy_df[policy_df["country"] == country]
    country_data = df[df["country"] == country]

    for _, row in country_policy.iterrows():

        decision_date = row["release_date"]

        # Get all data BEFORE decision
        history = country_data[country_data["release_date"] <= decision_date]

        latest_values = (
            history.sort_values("release_date")
            .groupby("indicator")
            .tail(1)
        )

        pivot = latest_values.pivot_table(
            index=None,
            columns="indicator",
            values="actual"
        )

        pivot["country"] = country
        pivot["decision_date"] = decision_date
        pivot["rate"] = row["actual"]
        pivot["rate_change"] = row["rate_change"]
        pivot["direction"] = row["direction"]

        final_rows.append(pivot)

# =========================================================
# FINAL DATAFRAME
# =========================================================
final_df = pd.concat(final_rows, ignore_index=True)

# Move columns
cols = ["decision_date", "country", "rate", "rate_change", "direction"]
other_cols = [c for c in final_df.columns if c not in cols]

final_df = final_df[cols + other_cols]

# Save
OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
final_df.to_csv(OUTPUT_FILE, index=False)

print("✅ Policy dataset created:")
print(OUTPUT_FILE)