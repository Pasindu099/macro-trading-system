"""Modeling workbench built on the EDA macro dataset.

The goal is not to crown a production model yet. It creates a reproducible
feature-selection, feature-engineering, time-split, and baseline-modeling
workflow that can be inspected before deeper forecasting work.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


TARGET_DEFINITIONS = {
    "inflation": {
        "category": "Inflation",
        "preferred_keys": ("cpi_headline_yoy", "core_cpi_yoy", "inflation_rate_yoy"),
    },
    "gdp_growth": {
        "category": "Growth",
        "preferred_keys": ("gdp_qoq", "gdp_mom", "gdp_yoy", "gdp_growth_rate_qoq"),
    },
    "unemployment": {
        "category": "Labor",
        "preferred_keys": ("unemployment_rate", "claimant_count_change"),
    },
}

MAX_FEATURES_PER_TARGET = 14
MAX_LAG = 6
ROLLING_WINDOWS = (3, 6)
MIN_TARGET_OBSERVATIONS = 28


@dataclass(frozen=True)
class TargetSpec:
    central_bank_code: str
    target_family: str
    target_indicator_key: str
    target_indicator: str
    target_category: str


def build_modeling_workbench(
    data_path: Path | str = Path("data/eda/eda_observations.csv"),
    analysis_dir: Path | str = Path("data/eda/analysis"),
    output_dir: Path | str = Path("data/modeling"),
    *,
    horizon: int = 1,
) -> dict[str, Any]:
    """Run feature selection, feature engineering, model comparison, and scenarios."""
    data_path = Path(data_path)
    analysis_dir = Path(analysis_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    df = load_eda_data(data_path)
    correlations = read_optional_csv(analysis_dir / "correlations.csv")
    lag_analysis = read_optional_csv(analysis_dir / "lag_analysis.csv")
    pca_loadings = read_optional_csv(analysis_dir / "pca_loadings.csv")

    targets = identify_targets(df)
    feature_selection_rows: list[dict[str, Any]] = []
    matrix_frames: list[pd.DataFrame] = []
    score_rows: list[dict[str, Any]] = []
    importance_rows: list[dict[str, Any]] = []
    forecast_rows: list[dict[str, Any]] = []
    scenario_rows: list[dict[str, Any]] = []

    for target in targets:
        selected_features = select_features_for_target(
            df,
            target,
            correlations=correlations,
            lag_analysis=lag_analysis,
            pca_loadings=pca_loadings,
        )
        feature_selection_rows.extend(selected_features)
        matrix = engineer_features(df, target, selected_features, horizon=horizon)
        if len(matrix) < MIN_TARGET_OBSERVATIONS:
            continue
        matrix_frames.append(matrix)
        result = train_models_for_target(matrix, target, horizon=horizon)
        score_rows.extend(result["scores"])
        importance_rows.extend(result["importances"])
        forecast_rows.extend(result["forecasts"])
        scenario_rows.extend(result["scenarios"])

    feature_selection_df = pd.DataFrame(feature_selection_rows)
    matrices_df = pd.concat(matrix_frames, ignore_index=True) if matrix_frames else pd.DataFrame()
    scores_df = pd.DataFrame(score_rows)
    importances_df = pd.DataFrame(importance_rows)
    forecasts_df = pd.DataFrame(forecast_rows)
    scenarios_df = pd.DataFrame(scenario_rows)

    write_outputs(
        output_path,
        feature_selection_df,
        matrices_df,
        scores_df,
        importances_df,
        forecasts_df,
        scenarios_df,
        horizon=horizon,
    )

    return {
        "output_dir": str(output_path),
        "targets": len(targets),
        "modeled_targets": int(scores_df[["central_bank_code", "target_family", "target_indicator_key"]].drop_duplicates().shape[0])
        if not scores_df.empty
        else 0,
        "feature_rows": len(feature_selection_df),
        "matrix_rows": len(matrices_df),
        "score_rows": len(scores_df),
        "files": sorted(path.name for path in output_path.iterdir() if path.is_file()),
    }


def load_eda_data(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for column in ["value", "value_normalized", "value_zscore", "value_minmax"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["date", "central_bank_code", "indicator_key", "value_normalized"])
    return df.sort_values(["central_bank_code", "indicator_key", "date"])


def read_optional_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def identify_targets(df: pd.DataFrame) -> list[TargetSpec]:
    targets: list[TargetSpec] = []
    for bank, bank_df in df.groupby("central_bank_code"):
        for family, definition in TARGET_DEFINITIONS.items():
            candidate = choose_target_indicator(bank_df, definition)
            if candidate is None:
                continue
            targets.append(
                TargetSpec(
                    central_bank_code=bank,
                    target_family=family,
                    target_indicator_key=str(candidate["indicator_key"]),
                    target_indicator=str(candidate["indicator"]),
                    target_category=str(candidate["primary_category"]),
                )
            )
    return targets


def choose_target_indicator(bank_df: pd.DataFrame, definition: dict[str, Any]) -> pd.Series | None:
    counts = (
        bank_df.groupby(["indicator_key", "indicator", "primary_category", "importance"])
        .size()
        .rename("observations")
        .reset_index()
    )
    preferred = counts[counts["indicator_key"].isin(definition["preferred_keys"])]
    if not preferred.empty:
        return preferred.sort_values(["importance", "observations"], ascending=[True, False]).iloc[0]
    category = counts[counts["primary_category"].str.casefold() == definition["category"].casefold()]
    if category.empty:
        return None
    return category.sort_values(["importance", "observations"], ascending=[True, False]).iloc[0]


def select_features_for_target(
    df: pd.DataFrame,
    target: TargetSpec,
    *,
    correlations: pd.DataFrame,
    lag_analysis: pd.DataFrame,
    pca_loadings: pd.DataFrame,
) -> list[dict[str, Any]]:
    bank_df = df[df["central_bank_code"] == target.central_bank_code]
    metadata = (
        bank_df.groupby(["indicator_key", "indicator", "primary_category", "importance"])
        .size()
        .rename("observations")
        .reset_index()
    )
    metadata = metadata[metadata["indicator_key"] != target.target_indicator_key]

    candidates: dict[str, dict[str, Any]] = {}
    for _, row in metadata.iterrows():
        same_category = row["primary_category"] == target.target_category
        candidates[row["indicator_key"]] = {
            "central_bank_code": target.central_bank_code,
            "target_family": target.target_family,
            "target_indicator_key": target.target_indicator_key,
            "target_indicator": target.target_indicator,
            "feature_indicator_key": row["indicator_key"],
            "feature_indicator": row["indicator"],
            "feature_category": row["primary_category"],
            "observations": int(row["observations"]),
            "same_category": bool(same_category),
            "lag_score": 0.0,
            "correlation_score": 0.0,
            "pca_score": 0.0,
            "selected_lag": 1,
            "selection_reason": [],
        }

    add_lag_scores(candidates, target, lag_analysis)
    add_correlation_scores(candidates, target, correlations)
    add_pca_scores(candidates, target, pca_loadings)

    for candidate in candidates.values():
        category_bonus = 0.15 if candidate["same_category"] else 0.0
        coverage_bonus = min(candidate["observations"] / 80, 1.0) * 0.1
        candidate["selection_score"] = (
            candidate["lag_score"] * 0.45
            + candidate["correlation_score"] * 0.30
            + candidate["pca_score"] * 0.15
            + category_bonus
            + coverage_bonus
        )
        if candidate["lag_score"]:
            candidate["selection_reason"].append("lag_relationship")
        if candidate["correlation_score"]:
            candidate["selection_reason"].append("correlated_with_target")
        if candidate["pca_score"]:
            candidate["selection_reason"].append("pca_driver")
        if candidate["same_category"]:
            candidate["selection_reason"].append("same_macro_theme")
        candidate["selection_reason"] = ",".join(candidate["selection_reason"] or ["coverage"])

    selected = sorted(candidates.values(), key=lambda item: item["selection_score"], reverse=True)
    selected = drop_redundant_features(selected, correlations, target.central_bank_code)
    return selected[:MAX_FEATURES_PER_TARGET]


def add_lag_scores(candidates: dict[str, dict[str, Any]], target: TargetSpec, lag_df: pd.DataFrame) -> None:
    if lag_df.empty:
        return
    rows = lag_df[
        (lag_df["central_bank_code"] == target.central_bank_code)
        & (lag_df["target_indicator_key"] == target.target_indicator_key)
    ]
    for _, row in rows.iterrows():
        key = row["candidate_indicator_key"]
        if key not in candidates:
            continue
        candidates[key]["lag_score"] = max(candidates[key]["lag_score"], float(row["abs_best_lag_correlation"]))
        candidates[key]["selected_lag"] = int(row["best_lag_periods"]) if pd.notna(row["best_lag_periods"]) else 1


def add_correlation_scores(
    candidates: dict[str, dict[str, Any]], target: TargetSpec, corr_df: pd.DataFrame
) -> None:
    if corr_df.empty:
        return
    rows = corr_df[corr_df["central_bank_code"] == target.central_bank_code]
    for _, row in rows.iterrows():
        left = row["left_indicator_key"]
        right = row["right_indicator_key"]
        other = None
        if left == target.target_indicator_key:
            other = right
        elif right == target.target_indicator_key:
            other = left
        if other in candidates:
            candidates[other]["correlation_score"] = max(
                candidates[other]["correlation_score"], float(row["abs_correlation"])
            )


def add_pca_scores(candidates: dict[str, dict[str, Any]], target: TargetSpec, pca_df: pd.DataFrame) -> None:
    if pca_df.empty:
        return
    rows = pca_df[
        (pca_df["central_bank_code"] == target.central_bank_code)
        & (pca_df["component"].isin(["PC1", "PC2", "PC3"]))
    ]
    for _, row in rows.iterrows():
        key = row["indicator_key"]
        if key in candidates:
            candidates[key]["pca_score"] = max(candidates[key]["pca_score"], float(row["abs_loading"]))


def drop_redundant_features(
    selected: list[dict[str, Any]], correlations: pd.DataFrame, central_bank_code: str
) -> list[dict[str, Any]]:
    if correlations.empty:
        return selected
    corr_lookup = {}
    bank_corr = correlations[correlations["central_bank_code"] == central_bank_code]
    for _, row in bank_corr.iterrows():
        pair = frozenset([row["left_indicator_key"], row["right_indicator_key"]])
        corr_lookup[pair] = float(row["abs_correlation"])

    kept: list[dict[str, Any]] = []
    for candidate in selected:
        is_redundant = any(
            corr_lookup.get(
                frozenset([candidate["feature_indicator_key"], kept_item["feature_indicator_key"]]),
                0.0,
            )
            >= 0.92
            for kept_item in kept
        )
        if not is_redundant:
            kept.append(candidate)
    return kept


def engineer_features(
    df: pd.DataFrame,
    target: TargetSpec,
    selected_features: list[dict[str, Any]],
    *,
    horizon: int,
) -> pd.DataFrame:
    bank_df = df[df["central_bank_code"] == target.central_bank_code]
    pivot = (
        bank_df.pivot_table(index="date", columns="indicator_key", values="value_normalized", aggfunc="mean")
        .sort_index()
        .asfreq("MS")
    )
    pivot = pivot.interpolate(limit_direction="both").ffill().bfill()
    if target.target_indicator_key not in pivot:
        return pd.DataFrame()

    feature_frame = pd.DataFrame(index=pivot.index)
    feature_names: list[str] = []
    for feature in selected_features:
        key = feature["feature_indicator_key"]
        if key not in pivot:
            continue
        lag = max(1, int(feature.get("selected_lag") or 1))
        for lag_value in sorted(set([1, lag, 3, 6])):
            if lag_value > MAX_LAG:
                continue
            name = f"{key}_lag{lag_value}"
            feature_frame[name] = pivot[key].shift(lag_value)
            feature_names.append(name)
        for window in ROLLING_WINDOWS:
            name = f"{key}_ma{window}"
            feature_frame[name] = pivot[key].shift(1).rolling(window, min_periods=2).mean()
            feature_names.append(name)

    feature_frame["target_current"] = pivot[target.target_indicator_key]
    feature_frame["target_lag1"] = pivot[target.target_indicator_key].shift(1)
    feature_frame["target_ma3"] = pivot[target.target_indicator_key].shift(1).rolling(3, min_periods=2).mean()
    feature_frame["target"] = pivot[target.target_indicator_key].shift(-horizon)

    feature_names.extend(["target_current", "target_lag1", "target_ma3"])
    if len(feature_names) >= 2:
        first = feature_names[0]
        second = feature_names[1]
        feature_frame[f"{first}_x_{second}"] = feature_frame[first] * feature_frame[second]

    feature_frame = feature_frame.reset_index().rename(columns={"date": "feature_date"})
    feature_frame["central_bank_code"] = target.central_bank_code
    feature_frame["target_family"] = target.target_family
    feature_frame["target_indicator_key"] = target.target_indicator_key
    feature_frame["target_indicator"] = target.target_indicator
    return feature_frame.dropna(subset=["target"]).reset_index(drop=True)


def train_models_for_target(
    matrix: pd.DataFrame,
    target: TargetSpec,
    *,
    horizon: int,
) -> dict[str, list[dict[str, Any]]]:
    feature_cols = [
        col
        for col in matrix.columns
        if col
        not in {
            "feature_date",
            "target",
            "central_bank_code",
            "target_family",
            "target_indicator_key",
            "target_indicator",
        }
        and matrix[col].notna().any()
    ]
    matrix = matrix.sort_values("feature_date").reset_index(drop=True)
    train_df, val_df, test_df = time_split(matrix)
    if min(len(train_df), len(val_df), len(test_df)) < 5:
        return {"scores": [], "importances": [], "forecasts": [], "scenarios": []}

    models = {
        "persistence_baseline": None,
        "ridge": Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
                ("model", Ridge(alpha=1.0)),
            ]
        ),
        "random_forest": Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                (
                    "model",
                    RandomForestRegressor(
                        n_estimators=200,
                        min_samples_leaf=3,
                        random_state=42,
                    ),
                ),
            ]
        ),
        "hist_gradient_boosting": Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                (
                    "model",
                    HistGradientBoostingRegressor(
                        max_iter=180,
                        learning_rate=0.05,
                        l2_regularization=0.1,
                        random_state=42,
                    ),
                ),
            ]
        ),
    }

    score_rows: list[dict[str, Any]] = []
    importance_rows: list[dict[str, Any]] = []
    forecast_rows: list[dict[str, Any]] = []
    scenario_rows: list[dict[str, Any]] = []
    fitted_models: dict[str, Any] = {}

    for model_name, model in models.items():
        if model_name == "persistence_baseline":
            for split_name, split_df in [("validation", val_df), ("test", test_df)]:
                predictions = split_df["target_current"].to_numpy()
                score_rows.append(score_model(target, model_name, split_name, split_df["target"], predictions))
                forecast_rows.extend(forecast_records(target, model_name, split_name, split_df, predictions, horizon))
            continue

        model.fit(train_df[feature_cols], train_df["target"])
        fitted_models[model_name] = model
        for split_name, split_df in [("validation", val_df), ("test", test_df)]:
            predictions = model.predict(split_df[feature_cols])
            score_rows.append(score_model(target, model_name, split_name, split_df["target"], predictions))
            forecast_rows.extend(forecast_records(target, model_name, split_name, split_df, predictions, horizon))
        importance_rows.extend(extract_importances(target, model_name, model, feature_cols))

    best_model_name = choose_best_model(score_rows)
    if best_model_name in fitted_models:
        scenario_rows.extend(
            scenario_sensitivity(target, fitted_models[best_model_name], matrix, feature_cols, best_model_name)
        )

    return {
        "scores": score_rows,
        "importances": importance_rows,
        "forecasts": forecast_rows,
        "scenarios": scenario_rows,
    }


def time_split(matrix: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    n = len(matrix)
    train_end = max(int(n * 0.70), 1)
    val_end = max(int(n * 0.85), train_end + 1)
    return matrix.iloc[:train_end], matrix.iloc[train_end:val_end], matrix.iloc[val_end:]


def score_model(
    target: TargetSpec,
    model_name: str,
    split_name: str,
    y_true: pd.Series,
    predictions: np.ndarray,
) -> dict[str, Any]:
    rmse = float(np.sqrt(mean_squared_error(y_true, predictions)))
    mae = float(mean_absolute_error(y_true, predictions))
    denom = np.where(np.abs(y_true) < 1e-6, np.nan, np.abs(y_true))
    mape = float(np.nanmean(np.abs((y_true - predictions) / denom)) * 100)
    return {
        "central_bank_code": target.central_bank_code,
        "target_family": target.target_family,
        "target_indicator_key": target.target_indicator_key,
        "target_indicator": target.target_indicator,
        "model": model_name,
        "split": split_name,
        "observations": int(len(y_true)),
        "rmse": rmse,
        "mae": mae,
        "mape_pct": mape,
    }


def forecast_records(
    target: TargetSpec,
    model_name: str,
    split_name: str,
    split_df: pd.DataFrame,
    predictions: np.ndarray,
    horizon: int,
) -> list[dict[str, Any]]:
    return [
        {
            "central_bank_code": target.central_bank_code,
            "target_family": target.target_family,
            "target_indicator_key": target.target_indicator_key,
            "target_indicator": target.target_indicator,
            "model": model_name,
            "split": split_name,
            "feature_date": row.feature_date,
            "forecast_horizon_periods": horizon,
            "actual": float(row.target),
            "prediction": float(prediction),
            "error": float(row.target - prediction),
        }
        for row, prediction in zip(split_df.itertuples(index=False), predictions, strict=False)
    ]


def extract_importances(
    target: TargetSpec, model_name: str, model: Pipeline, feature_cols: list[str]
) -> list[dict[str, Any]]:
    estimator = model.named_steps["model"]
    values = None
    if hasattr(estimator, "feature_importances_"):
        values = estimator.feature_importances_
    elif hasattr(estimator, "coef_"):
        values = np.abs(estimator.coef_)
    if values is None:
        return []
    total = float(np.sum(np.abs(values))) or 1.0
    return [
        {
            "central_bank_code": target.central_bank_code,
            "target_family": target.target_family,
            "target_indicator_key": target.target_indicator_key,
            "target_indicator": target.target_indicator,
            "model": model_name,
            "feature": feature,
            "importance": float(abs(value) / total),
        }
        for feature, value in sorted(zip(feature_cols, values, strict=False), key=lambda item: abs(item[1]), reverse=True)
    ]


def choose_best_model(score_rows: list[dict[str, Any]]) -> str:
    scores = pd.DataFrame(score_rows)
    test_scores = scores[scores["split"] == "test"]
    if test_scores.empty:
        return ""
    return str(test_scores.sort_values("rmse").iloc[0]["model"])


def scenario_sensitivity(
    target: TargetSpec,
    model: Pipeline,
    matrix: pd.DataFrame,
    feature_cols: list[str],
    model_name: str,
) -> list[dict[str, Any]]:
    latest = matrix.sort_values("feature_date").iloc[-1:].copy()
    baseline = float(model.predict(latest[feature_cols])[0])
    rows = []
    numeric_features = [col for col in feature_cols if col in latest.columns]
    for feature in numeric_features[:12]:
        for shock_name, shock_value in [("positive_1sd", 1.0), ("negative_1sd", -1.0), ("stress_2sd", 2.0)]:
            scenario = latest.copy()
            scenario[feature] = scenario[feature] + shock_value
            prediction = float(model.predict(scenario[feature_cols])[0])
            rows.append(
                {
                    "central_bank_code": target.central_bank_code,
                    "target_family": target.target_family,
                    "target_indicator_key": target.target_indicator_key,
                    "target_indicator": target.target_indicator,
                    "model": model_name,
                    "feature": feature,
                    "scenario": shock_name,
                    "baseline_prediction": baseline,
                    "scenario_prediction": prediction,
                    "prediction_delta": prediction - baseline,
                }
            )
    return rows


def write_outputs(
    output_path: Path,
    feature_selection: pd.DataFrame,
    matrices: pd.DataFrame,
    scores: pd.DataFrame,
    importances: pd.DataFrame,
    forecasts: pd.DataFrame,
    scenarios: pd.DataFrame,
    *,
    horizon: int,
) -> None:
    feature_selection.to_csv(output_path / "feature_selection.csv", index=False)
    matrices.to_csv(output_path / "modeling_matrix.csv", index=False)
    scores.to_csv(output_path / "model_scores.csv", index=False)
    importances.to_csv(output_path / "feature_importances.csv", index=False)
    forecasts.to_csv(output_path / "forecasts.csv", index=False)
    scenarios.to_csv(output_path / "scenario_sensitivity.csv", index=False)

    report = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "horizon_periods": horizon,
        "rows": {
            "feature_selection": len(feature_selection),
            "modeling_matrix": len(matrices),
            "model_scores": len(scores),
            "feature_importances": len(importances),
            "forecasts": len(forecasts),
            "scenario_sensitivity": len(scenarios),
        },
        "best_models": best_model_summary(scores),
        "modeling_notes": [
            "Targets and predictors are normalized EDA values.",
            "Splits are chronological: 70% train, 15% validation, 15% test.",
            "Feature selection combines lag relationships, direct correlation, PCA loading, same-theme bonus, and coverage.",
            "Highly redundant selected features are pruned when pairwise absolute correlation is at least 0.92.",
            "Scenario sensitivity shocks normalized feature values, not raw economic units.",
        ],
    }
    (output_path / "modeling_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_plots(output_path, scores, importances, forecasts, scenarios)
    write_readme(output_path)


def best_model_summary(scores: pd.DataFrame) -> list[dict[str, Any]]:
    if scores.empty:
        return []
    test_scores = scores[scores["split"] == "test"].copy()
    if test_scores.empty:
        return []
    idx = test_scores.groupby(["central_bank_code", "target_family"])["rmse"].idxmin()
    return test_scores.loc[idx].sort_values(["target_family", "central_bank_code"]).to_dict(orient="records")


def write_plots(
    output_path: Path,
    scores: pd.DataFrame,
    importances: pd.DataFrame,
    forecasts: pd.DataFrame,
    scenarios: pd.DataFrame,
) -> None:
    if not scores.empty:
        fig = px.bar(
            scores[scores["split"] == "test"],
            x="central_bank_code",
            y="rmse",
            color="model",
            facet_col="target_family",
            barmode="group",
            title="Test RMSE by Target and Model",
        )
        fig.write_html(output_path / "model_scores.html", include_plotlyjs="cdn")

    if not importances.empty:
        top = importances.sort_values("importance", ascending=False).groupby(
            ["central_bank_code", "target_family", "model"]
        ).head(8)
        fig = px.bar(
            top,
            x="importance",
            y="feature",
            color="model",
            facet_col="target_family",
            orientation="h",
            title="Top Feature Importances",
        )
        fig.update_layout(height=900)
        fig.write_html(output_path / "feature_importances.html", include_plotlyjs="cdn")

    if not forecasts.empty:
        sample = forecasts[forecasts["split"] == "test"].copy()
        if not sample.empty:
            first_keys = sample[["central_bank_code", "target_family"]].drop_duplicates().head(6)
            sample = sample.merge(first_keys, on=["central_bank_code", "target_family"])
            fig = go.Figure()
            for _, group in sample.groupby(["central_bank_code", "target_family", "model"]):
                name = f"{group['central_bank_code'].iloc[0]} {group['target_family'].iloc[0]} {group['model'].iloc[0]}"
                fig.add_trace(go.Scatter(x=group["feature_date"], y=group["prediction"], mode="lines", name=name))
            fig.update_layout(title="Sample Test Forecasts", height=700)
            fig.write_html(output_path / "forecast_samples.html", include_plotlyjs="cdn")

    if not scenarios.empty:
        top = scenarios.reindex(scenarios["prediction_delta"].abs().sort_values(ascending=False).index).head(80)
        fig = px.bar(
            top,
            x="prediction_delta",
            y="feature",
            color="scenario",
            facet_col="target_family",
            orientation="h",
            title="Scenario Sensitivity: Largest Prediction Deltas",
        )
        fig.update_layout(height=1000)
        fig.write_html(output_path / "scenario_sensitivity.html", include_plotlyjs="cdn")


def write_readme(output_path: Path) -> None:
    (output_path / "README.md").write_text(
        "\n".join(
            [
                "# Macro Modeling Workbench",
                "",
                "Generated by `python -m scripts.build_modeling_workbench`.",
                "",
                "Core files:",
                "- `feature_selection.csv`: selected non-redundant indicators and why they were chosen.",
                "- `modeling_matrix.csv`: engineered lag, rolling, and interaction features.",
                "- `model_scores.csv`: validation/test RMSE, MAE, and MAPE by model.",
                "- `feature_importances.csv`: model coefficients/importances for interpretation.",
                "- `forecasts.csv`: validation/test actual vs prediction rows.",
                "- `scenario_sensitivity.csv`: one-feature shock scenarios around latest observations.",
                "- `modeling_report.json`: compact summary and modeling notes.",
                "",
                "HTML charts:",
                "- `model_scores.html`",
                "- `feature_importances.html`",
                "- `forecast_samples.html`",
                "- `scenario_sensitivity.html`",
                "",
                "This is a research workbench. Treat findings as candidates for economic review and out-of-sample validation.",
                "",
            ]
        ),
        encoding="utf-8",
    )
