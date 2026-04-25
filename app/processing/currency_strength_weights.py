"""Derive initial indicator weights for a currency strength model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px

TARGET_DEFINITIONS = {
    "inflation": {
        "category": "Inflation",
        "preferred_keys": ("cpi_headline_yoy", "core_cpi_yoy", "inflation_rate_yoy"),
        "economic_note": "Higher inflation can support a currency when it increases expected policy tightening, but can become negative if it damages real growth.",
    },
    "gdp_growth": {
        "category": "Growth",
        "preferred_keys": ("gdp_qoq", "gdp_mom", "gdp_yoy", "retail_sales_mom"),
        "economic_note": "Stronger growth normally supports currency strength through better real activity and tighter policy expectations.",
    },
    "unemployment": {
        "category": "Labor",
        "preferred_keys": ("unemployment_rate", "claimant_count_change"),
        "economic_note": "Higher unemployment is generally currency-negative because it signals labor-market weakness and easier policy pressure.",
    },
}

MIN_OVERLAP = 18
HIGH_CORR_THRESHOLD = 0.70
REDUNDANCY_THRESHOLD = 0.92


def build_currency_strength_weights(
    data_path: Path | str = Path("data/eda/eda_observations.csv"),
    pca_path: Path | str = Path("data/eda/analysis/pca_loadings.csv"),
    model_importance_path: Path | str = Path("data/modeling/feature_importances.csv"),
    output_dir: Path | str = Path("data/currency_strength_weights"),
) -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    df = load_eda(Path(data_path))
    pca = read_optional_csv(Path(pca_path))
    model_importances = read_optional_csv(Path(model_importance_path))

    targets = identify_main_targets(df)
    correlations = calculate_target_correlations(df, targets)
    weights = assign_weights(df, targets, correlations, pca, model_importances)
    key_drivers = select_key_drivers(weights)
    refinements = build_refinement_notes(weights, correlations)

    targets.to_csv(output_path / "main_economic_indicators.csv", index=False)
    correlations.to_csv(output_path / "target_correlations.csv", index=False)
    weights.to_csv(output_path / "indicator_weights.csv", index=False)
    key_drivers.to_csv(output_path / "key_currency_strength_drivers.csv", index=False)
    refinements.to_csv(output_path / "weight_refinement_notes.csv", index=False)

    write_plots(output_path, weights, correlations)
    write_report(output_path, targets, correlations, weights, key_drivers, refinements)
    write_readme(output_path)

    return {
        "output_dir": str(output_path),
        "targets": int(len(targets)),
        "correlation_rows": int(len(correlations)),
        "weight_rows": int(len(weights)),
        "key_driver_rows": int(len(key_drivers)),
        "files": sorted(path.name for path in output_path.iterdir() if path.is_file()),
    }


def load_eda(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for column in ["value", "value_normalized"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df.dropna(subset=["date", "central_bank_code", "indicator_key", "value_normalized"])


def read_optional_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def identify_main_targets(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    counts = (
        df.groupby(["central_bank_code", "indicator_key", "indicator", "primary_category", "importance"])
        .size()
        .rename("observations")
        .reset_index()
    )
    for bank, bank_counts in counts.groupby("central_bank_code"):
        for target_family, definition in TARGET_DEFINITIONS.items():
            preferred = bank_counts[bank_counts["indicator_key"].isin(definition["preferred_keys"])]
            if preferred.empty:
                preferred = bank_counts[
                    bank_counts["primary_category"].str.casefold()
                    == definition["category"].casefold()
                ]
            if preferred.empty:
                continue
            selected = preferred.sort_values(["importance", "observations"], ascending=[True, False]).iloc[0]
            rows.append(
                {
                    "central_bank_code": bank,
                    "target_family": target_family,
                    "target_indicator_key": selected["indicator_key"],
                    "target_indicator": selected["indicator"],
                    "target_category": selected["primary_category"],
                    "observations": int(selected["observations"]),
                    "selection_basis": (
                        "preferred_indicator"
                        if selected["indicator_key"] in definition["preferred_keys"]
                        else "best_available_category_proxy"
                    ),
                    "economic_note": definition["economic_note"],
                }
            )
    return pd.DataFrame(rows).sort_values(["central_bank_code", "target_family"])


def calculate_target_correlations(df: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    metadata = (
        df.drop_duplicates(["central_bank_code", "indicator_key"])
        .set_index(["central_bank_code", "indicator_key"])
    )
    for _, target in targets.iterrows():
        bank_df = df[df["central_bank_code"] == target["central_bank_code"]]
        pivot = (
            bank_df.pivot_table(
                index="date",
                columns="indicator_key",
                values="value_normalized",
                aggfunc="mean",
            )
            .sort_index()
            .dropna(axis=1, thresh=MIN_OVERLAP)
        )
        target_key = target["target_indicator_key"]
        if target_key not in pivot.columns:
            continue
        for indicator_key in pivot.columns:
            if indicator_key == target_key:
                continue
            pair = pivot[[target_key, indicator_key]].dropna()
            if len(pair) < MIN_OVERLAP:
                continue
            meta = metadata.loc[(target["central_bank_code"], indicator_key)]
            pearson = pair[target_key].corr(pair[indicator_key], method="pearson")
            spearman = pair[target_key].corr(pair[indicator_key], method="spearman")
            rows.append(
                {
                    "central_bank_code": target["central_bank_code"],
                    "target_family": target["target_family"],
                    "target_indicator_key": target_key,
                    "target_indicator": target["target_indicator"],
                    "indicator_key": indicator_key,
                    "indicator": meta["indicator"],
                    "indicator_category": meta["primary_category"],
                    "importance": int(meta["importance"]),
                    "observations": int(len(pair)),
                    "pearson_correlation": float(pearson),
                    "spearman_correlation": float(spearman),
                    "abs_pearson": float(abs(pearson)),
                    "abs_spearman": float(abs(spearman)),
                    "direction": "positive" if pearson >= 0 else "negative",
                }
            )
    corr = pd.DataFrame(rows)
    if corr.empty:
        return corr
    return corr.sort_values(["central_bank_code", "target_family", "abs_pearson"], ascending=[True, True, False])


def assign_weights(
    df: pd.DataFrame,
    targets: pd.DataFrame,
    correlations: pd.DataFrame,
    pca: pd.DataFrame,
    model_importances: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    pca_scores = build_pca_scores(pca)
    model_scores = build_model_scores(model_importances)
    coverage = (
        df.groupby(["central_bank_code", "indicator_key"])
        .size()
        .rename("indicator_observations")
        .reset_index()
    )

    for _, corr in correlations.iterrows():
        same_category = corr["indicator_category"] == target_category(targets, corr)
        corr_score = 0.6 * corr["abs_pearson"] + 0.4 * corr["abs_spearman"]
        pca_score = pca_scores.get((corr["central_bank_code"], corr["indicator_key"]), 0.0)
        model_score = model_scores.get(
            (corr["central_bank_code"], corr["target_family"], corr["indicator_key"]),
            0.0,
        )
        observations = int(corr["observations"])
        coverage_score = min(observations / 72, 1.0)
        economic_bonus = 0.12 if same_category else 0.0
        raw_score = (
            0.50 * corr_score
            + 0.18 * pca_score
            + 0.17 * model_score
            + 0.10 * coverage_score
            + economic_bonus
        )
        rows.append(
            {
                **corr.to_dict(),
                "same_macro_theme": bool(same_category),
                "pca_score": pca_score,
                "model_importance_score": model_score,
                "coverage_score": coverage_score,
                "raw_weight_score": raw_score,
                "economic_direction_for_currency": economic_direction(corr["target_family"], corr["direction"]),
                "justification": build_justification(corr, same_category, pca_score, model_score),
            }
        )

    weights = pd.DataFrame(rows)
    if weights.empty:
        return weights
    weights = prune_redundant(weights, correlations)
    weights["normalized_weight"] = weights.groupby(
        ["central_bank_code", "target_family"]
    )["raw_weight_score"].transform(lambda values: values / values.sum())
    weights["weight_pct"] = weights["normalized_weight"] * 100
    return weights.sort_values(
        ["central_bank_code", "target_family", "normalized_weight"],
        ascending=[True, True, False],
    )


def target_category(targets: pd.DataFrame, corr: pd.Series) -> str:
    row = targets[
        (targets["central_bank_code"] == corr["central_bank_code"])
        & (targets["target_family"] == corr["target_family"])
    ]
    return "" if row.empty else str(row.iloc[0]["target_category"])


def build_pca_scores(pca: pd.DataFrame) -> dict[tuple[str, str], float]:
    if pca.empty:
        return {}
    top = pca[pca["component"].isin(["PC1", "PC2", "PC3"])].copy()
    return (
        top.groupby(["central_bank_code", "indicator_key"])["abs_loading"]
        .max()
        .clip(upper=1)
        .to_dict()
    )


def build_model_scores(model_importances: pd.DataFrame) -> dict[tuple[str, str, str], float]:
    if model_importances.empty:
        return {}
    rows = []
    for _, row in model_importances.iterrows():
        feature = str(row["feature"])
        indicator_key = feature.split("_lag")[0].split("_ma")[0]
        rows.append(
            {
                "central_bank_code": row["central_bank_code"],
                "target_family": row["target_family"],
                "indicator_key": indicator_key,
                "importance": row["importance"],
            }
        )
    collapsed = pd.DataFrame(rows)
    if collapsed.empty:
        return {}
    return (
        collapsed.groupby(["central_bank_code", "target_family", "indicator_key"])["importance"]
        .max()
        .clip(upper=1)
        .to_dict()
    )


def economic_direction(target_family: str, corr_direction: str) -> str:
    if target_family == "unemployment":
        return "currency_negative" if corr_direction == "positive" else "currency_positive"
    if target_family == "gdp_growth":
        return "currency_positive" if corr_direction == "positive" else "currency_negative"
    if target_family == "inflation":
        return "policy_tightening_positive_but_regime_dependent" if corr_direction == "positive" else "disinflationary_or_growth_sensitive"
    return "unknown"


def build_justification(corr: pd.Series, same_category: bool, pca_score: float, model_score: float) -> str:
    strength = "strong" if corr["abs_pearson"] >= HIGH_CORR_THRESHOLD else "moderate" if corr["abs_pearson"] >= 0.4 else "weak"
    pieces = [
        f"{strength} {corr['direction']} Pearson correlation ({corr['pearson_correlation']:.2f})",
        f"Spearman {corr['spearman_correlation']:.2f}",
    ]
    if same_category:
        pieces.append("same macro theme")
    if pca_score > 0:
        pieces.append(f"PCA loading signal {pca_score:.2f}")
    if model_score > 0:
        pieces.append(f"model importance signal {model_score:.2f}")
    return "; ".join(pieces)


def prune_redundant(weights: pd.DataFrame, correlations: pd.DataFrame) -> pd.DataFrame:
    kept_rows = []
    for (bank, family), group in weights.groupby(["central_bank_code", "target_family"]):
        ranked = group.sort_values("raw_weight_score", ascending=False)
        kept: list[pd.Series] = []
        for _, candidate in ranked.iterrows():
            redundant = False
            for existing in kept:
                redundant_corr = feature_pair_abs_corr(
                    correlations,
                    bank,
                    candidate["indicator_key"],
                    existing["indicator_key"],
                )
                if redundant_corr >= REDUNDANCY_THRESHOLD:
                    redundant = True
                    break
            if not redundant:
                kept.append(candidate)
        kept_rows.extend(kept)
    return pd.DataFrame(kept_rows)


def feature_pair_abs_corr(correlations: pd.DataFrame, bank: str, left: str, right: str) -> float:
    rows = correlations[
        (correlations["central_bank_code"] == bank)
        & (
            (
                (correlations["indicator_key"] == left)
                & (correlations["target_indicator_key"] == right)
            )
            | (
                (correlations["indicator_key"] == right)
                & (correlations["target_indicator_key"] == left)
            )
        )
    ]
    if rows.empty:
        return 0.0
    return float(rows["abs_pearson"].max())


def select_key_drivers(weights: pd.DataFrame) -> pd.DataFrame:
    if weights.empty:
        return weights
    return (
        weights.sort_values("normalized_weight", ascending=False)
        .groupby(["central_bank_code", "target_family"])
        .head(8)
        .reset_index(drop=True)
    )


def build_refinement_notes(weights: pd.DataFrame, correlations: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if weights.empty:
        return pd.DataFrame(rows)
    for (bank, family), group in weights.groupby(["central_bank_code", "target_family"]):
        top = group.sort_values("normalized_weight", ascending=False).head(5)
        concentration = float(top["normalized_weight"].sum())
        rows.append(
            {
                "central_bank_code": bank,
                "target_family": family,
                "top_5_weight_share": concentration,
                "refinement_note": (
                    "Weights are concentrated; validate out-of-sample and cap single-indicator exposure."
                    if concentration > 0.75
                    else "Weights are diversified; next refinement should test regime-specific performance."
                ),
                "modeling_note": "Use rolling-window validation and compare against the existing currency stance score before production use.",
            }
        )
    return pd.DataFrame(rows)


def write_plots(output_path: Path, weights: pd.DataFrame, correlations: pd.DataFrame) -> None:
    if not weights.empty:
        top = weights.sort_values("normalized_weight", ascending=False).groupby(
            ["central_bank_code", "target_family"]
        ).head(8)
        fig = px.bar(
            top,
            x="weight_pct",
            y="indicator_key",
            color="target_family",
            facet_col="central_bank_code",
            facet_col_wrap=2,
            orientation="h",
            title="Top Normalized Currency Strength Indicator Weights",
        )
        fig.update_layout(height=1200)
        fig.write_html(output_path / "indicator_weights.html", include_plotlyjs="cdn")
    if not correlations.empty:
        fig = px.scatter(
            correlations,
            x="pearson_correlation",
            y="spearman_correlation",
            color="target_family",
            facet_col="central_bank_code",
            facet_col_wrap=2,
            hover_name="indicator",
            title="Indicator Correlations to Main Macro Targets",
        )
        fig.update_layout(height=1200)
        fig.write_html(output_path / "target_correlations.html", include_plotlyjs="cdn")


def write_report(
    output_path: Path,
    targets: pd.DataFrame,
    correlations: pd.DataFrame,
    weights: pd.DataFrame,
    key_drivers: pd.DataFrame,
    refinements: pd.DataFrame,
) -> None:
    report = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "summary": {
            "main_targets": len(targets),
            "correlation_rows": len(correlations),
            "weighted_indicator_rows": len(weights),
            "key_driver_rows": len(key_drivers),
        },
        "method": [
            "Identify target proxies for inflation, GDP/growth, and unemployment per central bank.",
            "Compute Pearson and Spearman correlations on normalized indicator values.",
            "Score indicators using correlation strength, PCA loading, model importance, coverage, and same-theme economic bonus.",
            "Prune highly redundant indicators and normalize weights to sum to 1 per central bank and target family.",
        ],
        "top_key_drivers": key_drivers.head(30).to_dict(orient="records"),
        "refinement_notes": refinements.to_dict(orient="records"),
    }
    (output_path / "currency_strength_weight_report.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )


def write_readme(output_path: Path) -> None:
    (output_path / "README.md").write_text(
        "\n".join(
            [
                "# Currency Strength Indicator Weights",
                "",
                "Generated by `python -m scripts.build_currency_strength_weights`.",
                "",
                "Files:",
                "- `main_economic_indicators.csv`: target proxies for inflation, GDP/growth, and unemployment.",
                "- `target_correlations.csv`: Pearson/Spearman correlations to each target.",
                "- `indicator_weights.csv`: normalized weights per central bank and target family.",
                "- `key_currency_strength_drivers.csv`: highest-weight drivers to prioritize.",
                "- `weight_refinement_notes.csv`: caveats and next refinement steps.",
                "- `indicator_weights.html` and `target_correlations.html`: interactive charts.",
                "",
                "Weights are initial research weights, not final production parameters.",
                "",
            ]
        ),
        encoding="utf-8",
    )
