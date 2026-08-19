"""Walk-forward backtest: predict next US ISM Manufacturing PMI print.

Features are prior-month (t-1) values of the indicators that showed the
strongest indicator(t-1) -> PMI(t) correlation in indicator_correlation_study,
excluding ISM's own sub-components and competing PMI gauges (correlating
with those would be tautological, not predictive). Each walk-forward step
fits only on months strictly before the prediction target, so nothing from
the future leaks into the fit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.db.session import session_scope
from app.processing.indicator_correlation_study import (
    load_country_observations,
    to_monthly_panel,
)

TARGET_KEY = "ism_manufacturing_pmi"
FEATURE_KEYS = [
    "retail_sales_yoy",
    "adp_employment_change",
    "average_weekly_hours",
    "export_prices_yoy",
    "import_prices_yoy",
]
MIN_TRAIN_MONTHS = 18
RIDGE_ALPHA = 5.0


def build_feature_frame(panel: pd.DataFrame) -> pd.DataFrame:
    """target(t) predicted from other indicators' values as of month t-1."""
    frame = pd.DataFrame(index=panel.index)
    frame["target"] = panel[TARGET_KEY]
    for key in FEATURE_KEYS:
        frame[f"{key}_lag1"] = panel[key].shift(1)
    return frame


def walk_forward_backtest(frame: pd.DataFrame) -> pd.DataFrame:
    feature_cols = [c for c in frame.columns if c != "target"]
    usable = frame.dropna(subset=["target"])

    rows = []
    for i in range(MIN_TRAIN_MONTHS, len(usable)):
        train = usable.iloc[:i].dropna(subset=feature_cols)
        test_row = usable.iloc[[i]]
        if len(train) < MIN_TRAIN_MONTHS or test_row[feature_cols].isna().any(axis=1).iloc[0]:
            continue

        model = make_pipeline(StandardScaler(), Ridge(alpha=RIDGE_ALPHA))
        model.fit(train[feature_cols], train["target"])
        prediction = float(model.predict(test_row[feature_cols])[0])

        naive_last = float(usable["target"].iloc[i - 1])
        naive_avg3 = float(usable["target"].iloc[max(0, i - 3) : i].mean())

        rows.append(
            {
                "period": str(usable.index[i]),
                "actual": float(test_row["target"].iloc[0]),
                "model_prediction": round(prediction, 3),
                "naive_last_value": naive_last,
                "naive_trailing_avg3": round(naive_avg3, 3),
                "n_train_months": len(train),
            }
        )
    return pd.DataFrame(rows)


def score_backtest(results: pd.DataFrame) -> dict[str, Any]:
    def mae(col: str) -> float:
        return float((results["actual"] - results[col]).abs().mean())

    def rmse(col: str) -> float:
        return float(np.sqrt(((results["actual"] - results[col]) ** 2).mean()))

    return {
        "n_predictions": int(len(results)),
        "model_mae": round(mae("model_prediction"), 3),
        "model_rmse": round(rmse("model_prediction"), 3),
        "naive_last_value_mae": round(mae("naive_last_value"), 3),
        "naive_last_value_rmse": round(rmse("naive_last_value"), 3),
        "naive_trailing_avg3_mae": round(mae("naive_trailing_avg3"), 3),
        "naive_trailing_avg3_rmse": round(rmse("naive_trailing_avg3"), 3),
    }


async def build_pmi_forecast_backtest(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    async with session_scope() as session:
        obs = await load_country_observations(session, "US")

    panel = to_monthly_panel(obs)
    frame = build_feature_frame(panel)
    results = walk_forward_backtest(frame)

    if results.empty:
        raise RuntimeError(
            "No walk-forward predictions produced — insufficient overlapping data "
            "across the chosen feature set."
        )

    scores = score_backtest(results)

    csv_path = output_dir / "us_ism_manufacturing_pmi_walkforward.csv"
    results.to_csv(csv_path, index=False)
    summary_path = output_dir / "us_ism_manufacturing_pmi_walkforward_summary.json"
    summary_path.write_text(json.dumps(scores, indent=2))

    return {"csv_path": str(csv_path), "summary_path": str(summary_path), **scores}
