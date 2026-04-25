"""Refine currency-strength indicator weights with FX return validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px


CURRENCY_MAP = {
    "BOC": {"currency": "CAD", "pair_code": "USDCAD", "return_multiplier": -1},
    "BOE": {"currency": "GBP", "pair_code": "GBPUSD", "return_multiplier": 1},
    "BOJ": {"currency": "JPY", "pair_code": "USDJPY", "return_multiplier": -1},
    "ECB": {"currency": "EUR", "pair_code": "EURUSD", "return_multiplier": 1},
    "FED": {"currency": "USD", "pair_code": "USD_BASKET", "return_multiplier": 1},
    "RBA": {"currency": "AUD", "pair_code": "AUDUSD", "return_multiplier": 1},
    "RBNZ": {"currency": "NZD", "pair_code": "NZDUSD", "return_multiplier": 1},
    "SNB": {"currency": "CHF", "pair_code": "USDCHF", "return_multiplier": -1},
}

FAMILY_WEIGHTS = {"inflation": 1 / 3, "gdp_growth": 1 / 3, "unemployment": 1 / 3}
TOP_SUBSET_COUNT = 12
PERTURBATION = 0.20


def build_currency_strength_refinement(
    eda_path: Path | str = Path("data/eda/eda_observations.csv"),
    weights_path: Path | str = Path("data/currency_strength_weights/indicator_weights.csv"),
    returns_path: Path | str = Path("data/fx_validation/fx_returns.csv"),
    output_dir: Path | str = Path("data/currency_strength_refinement"),
) -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    eda = load_eda(Path(eda_path))
    weights = load_weights(Path(weights_path))
    returns = load_fx_returns(Path(returns_path))

    aggregate_weights = aggregate_indicator_weights(weights)
    scores, contributions = build_strength_scores(eda, aggregate_weights)
    validation = validate_strength_scores(scores, returns)
    contribution_impact = validate_contributions(contributions, returns)
    refined_weights = refine_weights(aggregate_weights, contribution_impact)
    refined_scores, refined_contributions = build_strength_scores(
        eda,
        refined_weights,
        weight_col="refined_signed_weight",
        score_name="refined_strength_score",
    )
    refined_validation = validate_strength_scores(refined_scores, returns, score_column="refined_strength_score")
    subset = select_important_subset(refined_weights, contribution_impact)
    sensitivity = run_sensitivity_analysis(eda, refined_weights, returns)

    aggregate_weights.to_csv(output_path / "initial_aggregate_weights.csv", index=False)
    scores.to_csv(output_path / "initial_strength_scores.csv", index=False)
    contributions.to_csv(output_path / "initial_indicator_contributions.csv", index=False)
    validation.to_csv(output_path / "initial_model_validation.csv", index=False)
    contribution_impact.to_csv(output_path / "indicator_contribution_impact.csv", index=False)
    refined_weights.to_csv(output_path / "refined_indicator_weights.csv", index=False)
    refined_scores.to_csv(output_path / "refined_strength_scores.csv", index=False)
    refined_contributions.to_csv(output_path / "refined_indicator_contributions.csv", index=False)
    refined_validation.to_csv(output_path / "refined_model_validation.csv", index=False)
    subset.to_csv(output_path / "recommended_indicator_subset.csv", index=False)
    sensitivity.to_csv(output_path / "weight_sensitivity_analysis.csv", index=False)

    report = build_report(validation, refined_validation, subset, sensitivity)
    (output_path / "currency_strength_refinement_report.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )
    write_documentation(output_path, validation, refined_validation, subset, sensitivity)
    write_plots(output_path, validation, refined_validation, subset, sensitivity, refined_scores)

    return {
        "output_dir": str(output_path),
        "initial_weight_rows": int(len(aggregate_weights)),
        "refined_weight_rows": int(len(refined_weights)),
        "subset_rows": int(len(subset)),
        "validation_rows": int(len(refined_validation)),
        "sensitivity_rows": int(len(sensitivity)),
        "files": sorted(path.name for path in output_path.iterdir() if path.is_file()),
    }


def load_eda(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()
    df["value_normalized"] = pd.to_numeric(df["value_normalized"], errors="coerce")
    return df.dropna(subset=["month", "central_bank_code", "indicator_key", "value_normalized"])


def load_weights(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    numeric = [
        "normalized_weight",
        "raw_weight_score",
        "pearson_correlation",
        "spearman_correlation",
        "model_importance_score",
        "pca_score",
    ]
    for column in numeric:
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0)
    return df


def load_fx_returns(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["price_date"] = pd.to_datetime(df["price_date"], errors="coerce")
    df["month"] = df["price_date"].dt.to_period("M").dt.to_timestamp()
    df["monthly_return"] = pd.to_numeric(df["monthly_return"], errors="coerce")
    monthly = (
        df.dropna(subset=["month", "pair_code", "monthly_return"])
        .sort_values("price_date")
        .groupby(["pair_code", "month"])
        .tail(1)
        .copy()
    )
    rows = []
    for central_bank, mapping in CURRENCY_MAP.items():
        pair = mapping["pair_code"]
        pair_df = monthly[monthly["pair_code"] == pair].copy()
        pair_df["central_bank_code"] = central_bank
        pair_df["currency"] = mapping["currency"]
        pair_df["currency_monthly_return"] = pair_df["monthly_return"] * mapping["return_multiplier"]
        rows.append(pair_df[["central_bank_code", "currency", "month", "currency_monthly_return"]])
    return pd.concat(rows, ignore_index=True)


def aggregate_indicator_weights(weights: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (bank, indicator_key), group in weights.groupby(["central_bank_code", "indicator_key"]):
        signed_components = []
        for _, row in group.iterrows():
            family_weight = FAMILY_WEIGHTS.get(row["target_family"], 0.0)
            signed_components.append(
                row["normalized_weight"] * family_weight * direction_sign(row["economic_direction_for_currency"])
            )
        raw_signed_weight = float(np.sum(signed_components))
        rows.append(
            {
                "central_bank_code": bank,
                "currency": CURRENCY_MAP.get(bank, {}).get("currency"),
                "indicator_key": indicator_key,
                "indicator": group["indicator"].iloc[0],
                "indicator_category": group["indicator_category"].iloc[0],
                "target_families": ",".join(sorted(group["target_family"].unique())),
                "initial_signed_weight": raw_signed_weight,
                "initial_abs_weight": abs(raw_signed_weight),
                "avg_abs_correlation": float(group["abs_pearson"].mean()),
                "avg_model_importance": float(group["model_importance_score"].mean()),
                "avg_pca_score": float(group["pca_score"].mean()),
                "economic_rationale": summarize_rationale(group),
            }
        )
    result = pd.DataFrame(rows)
    result = normalize_abs_weights(result, "initial_abs_weight", "initial_normalized_abs_weight")
    return result.sort_values(["central_bank_code", "initial_normalized_abs_weight"], ascending=[True, False])


def direction_sign(direction: str) -> int:
    if direction == "currency_negative" or direction == "disinflationary_or_growth_sensitive":
        return -1
    return 1


def summarize_rationale(group: pd.DataFrame) -> str:
    strongest = group.sort_values("normalized_weight", ascending=False).iloc[0]
    return (
        f"Linked to {', '.join(sorted(group['target_family'].unique()))}; "
        f"strongest target link is {strongest['target_family']} with "
        f"Pearson {strongest['pearson_correlation']:.2f}. "
        f"{strongest['justification']}"
    )


def normalize_abs_weights(df: pd.DataFrame, source_col: str, target_col: str) -> pd.DataFrame:
    df = df.copy()
    total = df.groupby("central_bank_code")[source_col].transform("sum")
    df[target_col] = np.where(total > 0, df[source_col] / total, 0.0)
    return df


def build_strength_scores(
    eda: pd.DataFrame,
    weights: pd.DataFrame,
    *,
    weight_col: str = "initial_signed_weight",
    score_name: str = "strength_score",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    monthly = (
        eda.groupby(["central_bank_code", "month", "indicator_key"])["value_normalized"]
        .mean()
        .reset_index()
    )
    merged = monthly.merge(
        weights[["central_bank_code", "indicator_key", "indicator", "indicator_category", weight_col]],
        on=["central_bank_code", "indicator_key"],
        how="inner",
    )
    merged["contribution"] = merged["value_normalized"] * merged[weight_col]
    contributions = merged.rename(columns={weight_col: "signed_weight"})
    scores = (
        contributions.groupby(["central_bank_code", "month"])["contribution"]
        .sum()
        .rename(score_name)
        .reset_index()
    )
    return scores, contributions


def validate_strength_scores(
    scores: pd.DataFrame,
    returns: pd.DataFrame,
    *,
    score_column: str = "strength_score",
) -> pd.DataFrame:
    merged = scores.merge(returns, on=["central_bank_code", "month"], how="inner")
    merged["forward_return"] = merged.groupby("central_bank_code")["currency_monthly_return"].shift(-1)
    rows = []
    for bank, group in merged.dropna(subset=["forward_return", score_column]).groupby("central_bank_code"):
        if len(group) < 12:
            continue
        score = group[score_column]
        fwd = group["forward_return"]
        corr = score.corr(fwd)
        spearman = score.corr(fwd, method="spearman")
        hit_rate = (np.sign(score) == np.sign(fwd)).mean()
        rmse_direction = np.sqrt(np.mean((np.sign(score) - np.sign(fwd)) ** 2))
        rows.append(
            {
                "central_bank_code": bank,
                "currency": CURRENCY_MAP[bank]["currency"],
                "observations": int(len(group)),
                "pearson_ic": float(corr),
                "spearman_ic": float(spearman),
                "directional_hit_rate": float(hit_rate),
                "direction_rmse": float(rmse_direction),
                "avg_forward_return": float(fwd.mean()),
                "score_volatility": float(score.std()),
            }
        )
    return pd.DataFrame(rows).sort_values("central_bank_code")


def validate_contributions(contributions: pd.DataFrame, returns: pd.DataFrame) -> pd.DataFrame:
    merged = contributions.merge(returns, on=["central_bank_code", "month"], how="inner")
    merged["forward_return"] = merged.groupby("central_bank_code")["currency_monthly_return"].shift(-1)
    rows = []
    for (bank, indicator_key), group in merged.dropna(subset=["forward_return", "contribution"]).groupby(
        ["central_bank_code", "indicator_key"]
    ):
        if len(group) < 12:
            continue
        rows.append(
            {
                "central_bank_code": bank,
                "indicator_key": indicator_key,
                "indicator": group["indicator"].iloc[0],
                "indicator_category": group["indicator_category"].iloc[0],
                "observations": int(len(group)),
                "contribution_pearson_ic": float(group["contribution"].corr(group["forward_return"])),
                "contribution_spearman_ic": float(
                    group["contribution"].corr(group["forward_return"], method="spearman")
                ),
                "contribution_hit_rate": float(
                    (np.sign(group["contribution"]) == np.sign(group["forward_return"])).mean()
                ),
            }
        )
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result["impact_score"] = (
        0.5 * result["contribution_pearson_ic"].fillna(0.0)
        + 0.3 * result["contribution_spearman_ic"].fillna(0.0)
        + 0.2 * (result["contribution_hit_rate"].fillna(0.5) - 0.5) * 2
    )
    return result.sort_values(["central_bank_code", "impact_score"], ascending=[True, False])


def refine_weights(initial: pd.DataFrame, impact: pd.DataFrame) -> pd.DataFrame:
    merged = initial.merge(
        impact[["central_bank_code", "indicator_key", "impact_score", "contribution_hit_rate"]],
        on=["central_bank_code", "indicator_key"],
        how="left",
    )
    merged["impact_score"] = merged["impact_score"].fillna(0.0)
    merged["contribution_hit_rate"] = merged["contribution_hit_rate"].fillna(0.5)
    merged["refinement_multiplier"] = (1.0 + merged["impact_score"]).clip(0.35, 1.65)
    merged["refined_signed_weight"] = merged["initial_signed_weight"] * merged["refinement_multiplier"]
    merged["refined_abs_weight"] = merged["refined_signed_weight"].abs()
    merged = normalize_abs_weights(merged, "refined_abs_weight", "refined_normalized_abs_weight")
    merged["weight_change"] = merged["refined_normalized_abs_weight"] - merged["initial_normalized_abs_weight"]
    merged["refinement_rationale"] = np.where(
        merged["impact_score"] > 0.10,
        "Increased: contribution aligned with forward FX returns.",
        np.where(
            merged["impact_score"] < -0.10,
            "Reduced: contribution moved against forward FX returns.",
            "Mostly unchanged: validation signal was neutral.",
        ),
    )
    return merged.sort_values(["central_bank_code", "refined_normalized_abs_weight"], ascending=[True, False])


def select_important_subset(refined: pd.DataFrame, impact: pd.DataFrame) -> pd.DataFrame:
    subset = (
        refined.sort_values("refined_normalized_abs_weight", ascending=False)
        .groupby("central_bank_code")
        .head(TOP_SUBSET_COUNT)
        .copy()
    )
    subset["selection_criteria"] = (
        "Top refined normalized weight after combining initial macro relevance, "
        "FX validation impact, PCA/model signals, and redundancy pruning."
    )
    subset["interpretability_note"] = (
        "Using this subset keeps the model focused on the largest macro drivers while "
        "reducing redundant low-weight inputs."
    )
    return subset


def run_sensitivity_analysis(
    eda: pd.DataFrame,
    refined: pd.DataFrame,
    returns: pd.DataFrame,
) -> pd.DataFrame:
    baseline_scores, _ = build_strength_scores(
        eda,
        refined,
        weight_col="refined_signed_weight",
        score_name="refined_strength_score",
    )
    baseline = validate_strength_scores(
        baseline_scores,
        returns,
        score_column="refined_strength_score",
    ).set_index("central_bank_code")
    rows = []
    top_features = (
        refined.sort_values("refined_normalized_abs_weight", ascending=False)
        .groupby("central_bank_code")
        .head(10)
    )
    for _, feature in top_features.iterrows():
        for shock_label, multiplier in [("minus_20pct", 1 - PERTURBATION), ("plus_20pct", 1 + PERTURBATION)]:
            scenario_weights = refined.copy()
            mask = (
                (scenario_weights["central_bank_code"] == feature["central_bank_code"])
                & (scenario_weights["indicator_key"] == feature["indicator_key"])
            )
            scenario_weights.loc[mask, "scenario_signed_weight"] = (
                scenario_weights.loc[mask, "refined_signed_weight"] * multiplier
            )
            scenario_weights["scenario_signed_weight"] = scenario_weights[
                "scenario_signed_weight"
            ].fillna(scenario_weights["refined_signed_weight"])
            scenario_scores, _ = build_strength_scores(
                eda,
                scenario_weights,
                weight_col="scenario_signed_weight",
                score_name="scenario_strength_score",
            )
            scenario_validation = validate_strength_scores(
                scenario_scores,
                returns,
                score_column="scenario_strength_score",
            )
            scenario_row = scenario_validation[
                scenario_validation["central_bank_code"] == feature["central_bank_code"]
            ]
            if scenario_row.empty or feature["central_bank_code"] not in baseline.index:
                continue
            base = baseline.loc[feature["central_bank_code"]]
            current = scenario_row.iloc[0]
            rows.append(
                {
                    "central_bank_code": feature["central_bank_code"],
                    "currency": feature["currency"],
                    "indicator_key": feature["indicator_key"],
                    "indicator": feature["indicator"],
                    "shock": shock_label,
                    "baseline_pearson_ic": base["pearson_ic"],
                    "scenario_pearson_ic": current["pearson_ic"],
                    "pearson_ic_delta": current["pearson_ic"] - base["pearson_ic"],
                    "baseline_hit_rate": base["directional_hit_rate"],
                    "scenario_hit_rate": current["directional_hit_rate"],
                    "hit_rate_delta": current["directional_hit_rate"] - base["directional_hit_rate"],
                }
            )
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result["sensitivity_magnitude"] = result["pearson_ic_delta"].abs() + result["hit_rate_delta"].abs()
    return result.sort_values("sensitivity_magnitude", ascending=False)


def build_report(
    validation: pd.DataFrame,
    refined_validation: pd.DataFrame,
    subset: pd.DataFrame,
    sensitivity: pd.DataFrame,
) -> dict[str, Any]:
    return {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "initial_validation": validation.to_dict(orient="records"),
        "refined_validation": refined_validation.to_dict(orient="records"),
        "performance_change": compare_validation(validation, refined_validation).to_dict(orient="records"),
        "top_recommended_indicators": subset.head(40).to_dict(orient="records"),
        "highest_sensitivity_indicators": sensitivity.head(30).to_dict(orient="records"),
        "caveats": [
            "FX return validation is preliminary and uses monthly USD-pair returns.",
            "Correlation and hit rate are directional diagnostics, not a complete trading backtest.",
            "Macro indicators are released with lags; production deployment should enforce no-lookahead release timing.",
            "Inflation can be currency-positive or negative depending on the policy/growth regime.",
        ],
    }


def compare_validation(initial: pd.DataFrame, refined: pd.DataFrame) -> pd.DataFrame:
    merged = initial.merge(refined, on=["central_bank_code", "currency"], suffixes=("_initial", "_refined"))
    if merged.empty:
        return merged
    merged["pearson_ic_change"] = merged["pearson_ic_refined"] - merged["pearson_ic_initial"]
    merged["hit_rate_change"] = merged["directional_hit_rate_refined"] - merged["directional_hit_rate_initial"]
    return merged[
        [
            "central_bank_code",
            "currency",
            "pearson_ic_initial",
            "pearson_ic_refined",
            "pearson_ic_change",
            "directional_hit_rate_initial",
            "directional_hit_rate_refined",
            "hit_rate_change",
        ]
    ]


def write_documentation(
    output_path: Path,
    initial_validation: pd.DataFrame,
    refined_validation: pd.DataFrame,
    subset: pd.DataFrame,
    sensitivity: pd.DataFrame,
) -> None:
    comparison = compare_validation(initial_validation, refined_validation)
    top_subset = subset.sort_values(["central_bank_code", "refined_normalized_abs_weight"], ascending=[True, False])
    lines = [
        "# Currency Strength Weight Refinement",
        "",
        "## Purpose",
        "",
        "This document explains the refinement of initial macro indicator weights for the currency strength model. The process starts from the correlation/PCA/model-importance weights generated from `target_correlations.html` and `indicator_weights.html`, then validates those weights against next-month FX returns.",
        "",
        "## Initial Correlation Analysis",
        "",
        "The initial weighting process identified inflation, GDP/growth, and unemployment target proxies for each central bank. Each candidate indicator was scored using Pearson/Spearman correlation to those targets, PCA loading strength, model feature-importance evidence, data coverage, and same-theme economic relevance.",
        "",
        "## Economic Significance",
        "",
        "- Growth indicators are generally currency-positive when stronger because they imply better activity and tighter policy expectations.",
        "- Labor strength is generally currency-positive; unemployment-linked indicators are usually currency-negative when they rise.",
        "- Inflation is regime-dependent: moderate upside inflation can support the currency through policy tightening expectations, while excessive inflation can hurt real growth and credibility.",
        "",
        "## Preliminary Currency Strength Model",
        "",
        "A monthly macro strength score was built by multiplying normalized indicator values by their signed weights and summing by currency. The score was evaluated against next-month FX returns versus USD. For USD-quoted inverse pairs such as USDCAD, USDCHF, and USDJPY, returns were inverted so positive return always means local-currency strength.",
        "",
        "### Validation Summary",
        "",
        dataframe_to_markdown(comparison) if not comparison.empty else "No validation rows were available.",
        "",
        "## Iterative Refinement",
        "",
        "Each indicator contribution was validated against next-month FX returns. Indicators with positive contribution alignment received a moderate weight increase, while indicators that moved against returns were reduced. Changes were clipped to avoid overfitting a short validation sample.",
        "",
        "## Recommended Indicator Subset",
        "",
        "The recommended subset keeps the top indicators by refined normalized weight for each central bank. This simplifies the model, improves interpretability, and reduces exposure to low-weight redundant indicators.",
        "",
        dataframe_to_markdown(
            top_subset[
                [
                    "central_bank_code",
                    "currency",
                    "indicator_key",
                    "indicator",
                    "indicator_category",
                    "refined_normalized_abs_weight",
                    "refinement_rationale",
                ]
            ].head(80)
        ),
        "",
        "## Sensitivity Analysis",
        "",
        "For the largest refined-weight indicators, weights were shocked by +/-20%. The resulting changes in Pearson information coefficient and directional hit rate identify which indicators most influence validation performance.",
        "",
        dataframe_to_markdown(sensitivity.head(40)) if not sensitivity.empty else "No sensitivity rows were available.",
        "",
        "## Final Recommendation",
        "",
        "Use `recommended_indicator_subset.csv` as the first production-candidate indicator set and `refined_indicator_weights.csv` as the full research set. Before deployment, rerun validation with strict release-date alignment and a rolling-window backtest against the dashboard's existing currency stance layer.",
        "",
    ]
    (output_path / "currency_strength_refinement.md").write_text("\n".join(lines), encoding="utf-8")


def dataframe_to_markdown(df: pd.DataFrame) -> str:
    """Render a compact markdown table without requiring tabulate."""
    if df.empty:
        return ""
    rendered = df.copy()
    for column in rendered.columns:
        if pd.api.types.is_float_dtype(rendered[column]):
            rendered[column] = rendered[column].map(lambda value: f"{value:.4f}")
    headers = list(rendered.columns)
    rows = rendered.astype(str).values.tolist()
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def write_plots(
    output_path: Path,
    initial_validation: pd.DataFrame,
    refined_validation: pd.DataFrame,
    subset: pd.DataFrame,
    sensitivity: pd.DataFrame,
    refined_scores: pd.DataFrame,
) -> None:
    comparison = compare_validation(initial_validation, refined_validation)
    if not comparison.empty:
        plot_df = comparison.melt(
            id_vars=["central_bank_code", "currency"],
            value_vars=["pearson_ic_initial", "pearson_ic_refined"],
            var_name="model_stage",
            value_name="pearson_ic",
        )
        fig = px.bar(
            plot_df,
            x="central_bank_code",
            y="pearson_ic",
            color="model_stage",
            barmode="group",
            title="Initial vs Refined Currency Strength Validation",
        )
        fig.write_html(output_path / "validation_comparison.html", include_plotlyjs="cdn")
    if not subset.empty:
        fig = px.bar(
            subset,
            x="refined_normalized_abs_weight",
            y="indicator_key",
            color="indicator_category",
            facet_col="central_bank_code",
            facet_col_wrap=2,
            orientation="h",
            title="Recommended Indicator Subset and Refined Weights",
        )
        fig.update_layout(height=1200)
        fig.write_html(output_path / "recommended_indicator_subset.html", include_plotlyjs="cdn")
    if not sensitivity.empty:
        fig = px.bar(
            sensitivity.head(80),
            x="sensitivity_magnitude",
            y="indicator_key",
            color="shock",
            facet_col="central_bank_code",
            facet_col_wrap=2,
            orientation="h",
            title="Weight Sensitivity Analysis",
        )
        fig.update_layout(height=1200)
        fig.write_html(output_path / "weight_sensitivity.html", include_plotlyjs="cdn")
    if not refined_scores.empty:
        fig = px.line(
            refined_scores,
            x="month",
            y="refined_strength_score",
            color="central_bank_code",
            title="Refined Monthly Currency Strength Scores",
        )
        fig.write_html(output_path / "refined_strength_scores.html", include_plotlyjs="cdn")
