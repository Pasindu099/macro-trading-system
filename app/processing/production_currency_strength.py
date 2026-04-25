"""Production-candidate currency strength model and validation.

This layer uses the recommended indicator subset and refined weights from the
research/refinement process. It builds no-lookahead monthly strength signals by
only using observations whose release timestamp is known by the signal date.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


CURRENCY_MAP = {
    "BOC": {"country_code": "CA", "currency": "CAD", "pair_code": "USDCAD", "return_multiplier": -1},
    "BOE": {"country_code": "UK", "currency": "GBP", "pair_code": "GBPUSD", "return_multiplier": 1},
    "BOJ": {"country_code": "JP", "currency": "JPY", "pair_code": "USDJPY", "return_multiplier": -1},
    "ECB": {"country_code": "EU", "currency": "EUR", "pair_code": "EURUSD", "return_multiplier": 1},
    "FED": {"country_code": "US", "currency": "USD", "pair_code": "USD_BASKET", "return_multiplier": 1},
    "RBA": {"country_code": "AU", "currency": "AUD", "pair_code": "AUDUSD", "return_multiplier": 1},
    "RBNZ": {"country_code": "NZ", "currency": "NZD", "pair_code": "NZDUSD", "return_multiplier": 1},
    "SNB": {"country_code": "CH", "currency": "CHF", "pair_code": "USDCHF", "return_multiplier": -1},
}

DEFAULT_TOP_N_PER_CURRENCY = 12


def json_default(value: Any) -> str | float | int | bool | None:
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    return str(value)


def build_production_currency_strength(
    eda_path: Path | str = Path("data/eda/eda_observations.csv"),
    subset_path: Path | str = Path("data/currency_strength_refinement/recommended_indicator_subset.csv"),
    refined_weights_path: Path | str = Path("data/currency_strength_refinement/refined_indicator_weights.csv"),
    fx_returns_path: Path | str = Path("data/fx_validation/fx_returns.csv"),
    stance_path: Path | str = Path("data/currency_stance/currency_stance.csv"),
    output_dir: Path | str = Path("data/production_currency_strength"),
    *,
    top_n_per_currency: int = DEFAULT_TOP_N_PER_CURRENCY,
) -> dict[str, Any]:
    """Build production-candidate signals, backtest, docs, and monitoring plan."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    observations = load_observations(Path(eda_path))
    subset = load_subset_weights(Path(subset_path), top_n_per_currency=top_n_per_currency)
    refined_weights = load_refined_weights(Path(refined_weights_path))
    fx_returns = load_fx_returns(Path(fx_returns_path))
    stance = load_existing_stance(Path(stance_path))

    selected_weights = prepare_production_weights(subset, refined_weights)
    signals, contributions = calculate_no_lookahead_signals(observations, selected_weights)
    validation_rows = validate_model(signals, fx_returns, score_column="strength_score", model_name="refined_subset")
    stance_validation = validate_existing_stance(stance, fx_returns)
    comparison = compare_models(validation_rows, stance_validation)
    latest_signals = latest_currency_signals(signals)
    monitoring = build_monitoring_snapshot(signals, contributions, observations)

    selected_weights.to_csv(output_path / "production_indicator_weights.csv", index=False)
    signals.to_csv(output_path / "production_currency_strength_signals.csv", index=False)
    contributions.to_csv(output_path / "production_indicator_contributions.csv", index=False)
    validation_rows.to_csv(output_path / "production_backtest_metrics.csv", index=False)
    stance_validation.to_csv(output_path / "existing_stance_backtest_metrics.csv", index=False)
    comparison.to_csv(output_path / "stance_comparison_metrics.csv", index=False)
    latest_signals.to_csv(output_path / "latest_currency_strength_signals.csv", index=False)
    monitoring.to_csv(output_path / "monitoring_snapshot.csv", index=False)

    write_report(output_path, validation_rows, stance_validation, comparison, latest_signals)
    write_model_documentation(output_path, selected_weights, validation_rows, stance_validation, comparison)
    write_integration_plan(output_path)
    write_monitoring_plan(output_path)
    write_plots(output_path, signals, validation_rows, stance_validation, comparison, latest_signals)

    return {
        "output_dir": str(output_path),
        "weights": int(len(selected_weights)),
        "signals": int(len(signals)),
        "validation_rows": int(len(validation_rows)),
        "comparison_rows": int(len(comparison)),
        "files": sorted(path.name for path in output_path.iterdir() if path.is_file()),
    }


def load_observations(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["release_timestamp_utc"] = pd.to_datetime(df["release_timestamp_utc"], errors="coerce", utc=True)
    df["value_normalized"] = pd.to_numeric(df["value_normalized"], errors="coerce")
    df = df.dropna(subset=["date", "release_timestamp_utc", "central_bank_code", "indicator_key", "value_normalized"])
    return df.sort_values(["central_bank_code", "indicator_key", "release_timestamp_utc"])


def load_subset_weights(path: Path, *, top_n_per_currency: int) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["refined_normalized_abs_weight"] = pd.to_numeric(
        df["refined_normalized_abs_weight"], errors="coerce"
    )
    df["refined_signed_weight"] = pd.to_numeric(df["refined_signed_weight"], errors="coerce")
    df = df.dropna(subset=["central_bank_code", "indicator_key", "refined_signed_weight"])
    return (
        df.sort_values("refined_normalized_abs_weight", ascending=False)
        .groupby("central_bank_code")
        .head(top_n_per_currency)
        .reset_index(drop=True)
    )


def load_refined_weights(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    for column in ["refined_signed_weight", "refined_normalized_abs_weight"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df.dropna(subset=["central_bank_code", "indicator_key", "refined_signed_weight"])


def prepare_production_weights(subset: pd.DataFrame, refined_weights: pd.DataFrame) -> pd.DataFrame:
    keys = subset[["central_bank_code", "indicator_key"]].drop_duplicates()
    weights = refined_weights.merge(keys, on=["central_bank_code", "indicator_key"], how="inner")
    weights = weights.sort_values(["central_bank_code", "refined_normalized_abs_weight"], ascending=[True, False])
    total = weights.groupby("central_bank_code")["refined_signed_weight"].transform(lambda values: values.abs().sum())
    weights["production_signed_weight"] = np.where(total > 0, weights["refined_signed_weight"] / total, 0.0)
    weights["production_abs_weight"] = weights["production_signed_weight"].abs()
    weights["production_weight_pct"] = weights["production_abs_weight"] * 100
    return weights


def calculate_no_lookahead_signals(
    observations: pd.DataFrame,
    weights: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    start = observations["release_timestamp_utc"].dt.tz_convert(None).min().to_period("M").to_timestamp()
    end = observations["release_timestamp_utc"].dt.tz_convert(None).max().to_period("M").to_timestamp()
    signal_dates = pd.date_range(start=start, end=end, freq="ME", tz="UTC")

    rows: list[dict[str, Any]] = []
    contribution_rows: list[dict[str, Any]] = []
    for signal_date in signal_dates:
        known = observations[observations["release_timestamp_utc"] <= signal_date]
        if known.empty:
            continue
        latest = (
            known.sort_values("release_timestamp_utc")
            .groupby(["central_bank_code", "indicator_key"])
            .tail(1)
        )
        merged = latest.merge(
            weights[
                [
                    "central_bank_code",
                    "currency",
                    "indicator_key",
                    "indicator",
                    "indicator_category",
                    "production_signed_weight",
                    "production_abs_weight",
                ]
            ],
            on=["central_bank_code", "indicator_key"],
            how="inner",
            suffixes=("_obs", ""),
        )
        if merged.empty:
            continue
        merged["signal_date"] = signal_date
        merged["weighted_contribution"] = merged["value_normalized"] * merged["production_signed_weight"]
        for (bank, currency), group in merged.groupby(["central_bank_code", "currency"]):
            available_weight = group["production_abs_weight"].sum()
            if available_weight <= 0:
                continue
            score = group["weighted_contribution"].sum() / available_weight
            rows.append(
                {
                    "signal_date": signal_date.date().isoformat(),
                    "central_bank_code": bank,
                    "country_code": CURRENCY_MAP[bank]["country_code"],
                    "currency": currency,
                    "strength_score": score,
                    "strength_label": strength_label(score),
                    "available_indicators": int(group["indicator_key"].nunique()),
                    "available_weight_share": float(min(available_weight, 1.0)),
                    "latest_release_timestamp": group["release_timestamp_utc"].max().isoformat(),
                    "no_lookahead": True,
                }
            )
        contribution_rows.extend(
            merged[
                [
                    "signal_date",
                    "central_bank_code",
                    "currency",
                    "indicator_key",
                    "indicator",
                    "indicator_category",
                    "value_normalized",
                    "production_signed_weight",
                    "weighted_contribution",
                    "release_timestamp_utc",
                    "date",
                ]
            ].to_dict(orient="records")
        )

    signals = pd.DataFrame(rows)
    contributions = pd.DataFrame(contribution_rows)
    if not signals.empty:
        signals["signal_date"] = pd.to_datetime(signals["signal_date"])
    return signals, contributions


def strength_label(score: float) -> str:
    if score >= 0.75:
        return "strong_bullish"
    if score >= 0.25:
        return "bullish"
    if score <= -0.75:
        return "strong_bearish"
    if score <= -0.25:
        return "bearish"
    return "neutral"


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
    )
    rows = []
    for bank, mapping in CURRENCY_MAP.items():
        pair_df = monthly[monthly["pair_code"] == mapping["pair_code"]].copy()
        pair_df["central_bank_code"] = bank
        pair_df["currency"] = mapping["currency"]
        pair_df["currency_monthly_return"] = pair_df["monthly_return"] * mapping["return_multiplier"]
        rows.append(pair_df[["central_bank_code", "currency", "month", "currency_monthly_return"]])
    return pd.concat(rows, ignore_index=True)


def validate_model(
    signals: pd.DataFrame,
    fx_returns: pd.DataFrame,
    *,
    score_column: str,
    model_name: str,
) -> pd.DataFrame:
    if signals.empty:
        return pd.DataFrame()
    model = signals.copy()
    model["month"] = model["signal_date"].dt.to_period("M").dt.to_timestamp()
    merged = model.merge(fx_returns, on=["central_bank_code", "currency", "month"], how="inner")
    merged["forward_return"] = merged.groupby("central_bank_code")["currency_monthly_return"].shift(-1)
    merged["strategy_return"] = np.sign(merged[score_column]) * merged["forward_return"]
    rows = []
    for bank, group in merged.dropna(subset=[score_column, "forward_return", "strategy_return"]).groupby("central_bank_code"):
        rows.append(metric_row(bank, CURRENCY_MAP[bank]["currency"], group, score_column, model_name))
    return pd.DataFrame(rows).sort_values("central_bank_code") if rows else pd.DataFrame()


def metric_row(bank: str, currency: str, group: pd.DataFrame, score_column: str, model_name: str) -> dict[str, Any]:
    score = group[score_column]
    fwd = group["forward_return"]
    strategy = group["strategy_return"]
    cumulative = (1 + strategy).cumprod()
    drawdown = cumulative / cumulative.cummax() - 1
    return {
        "model_name": model_name,
        "central_bank_code": bank,
        "currency": currency,
        "observations": int(len(group)),
        "pearson_ic": float(score.corr(fwd)),
        "spearman_ic": float(score.corr(fwd, method="spearman")),
        "hit_rate": float((np.sign(score) == np.sign(fwd)).mean()),
        "avg_monthly_strategy_return": float(strategy.mean()),
        "monthly_strategy_volatility": float(strategy.std()),
        "information_ratio_annualized": information_ratio(strategy),
        "max_drawdown": float(drawdown.min()),
        "positive_signal_share": float((score > 0).mean()),
        "avg_abs_score": float(score.abs().mean()),
    }


def information_ratio(returns: pd.Series) -> float:
    std = returns.std()
    if std == 0 or pd.isna(std):
        return 0.0
    return float(np.sqrt(12) * returns.mean() / std)


def load_existing_stance(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty:
        return df
    df = df[df["window_months"] == 3].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["overall_stance_score"] = pd.to_numeric(df["overall_stance_score"], errors="coerce")
    bank_by_currency = {value["currency"]: bank for bank, value in CURRENCY_MAP.items()}
    df["central_bank_code"] = df["currency"].map(bank_by_currency)
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()
    return (
        df.dropna(subset=["central_bank_code", "month", "overall_stance_score"])
        .sort_values("date")
        .groupby(["central_bank_code", "currency", "month"])
        .tail(1)
    )


def validate_existing_stance(stance: pd.DataFrame, fx_returns: pd.DataFrame) -> pd.DataFrame:
    if stance.empty:
        return pd.DataFrame()
    signals = stance.rename(columns={"date": "signal_date", "overall_stance_score": "strength_score"})
    signals["signal_date"] = pd.to_datetime(signals["signal_date"])
    return validate_model(
        signals[["signal_date", "central_bank_code", "currency", "strength_score"]],
        fx_returns,
        score_column="strength_score",
        model_name="existing_currency_stance",
    )


def compare_models(production: pd.DataFrame, stance: pd.DataFrame) -> pd.DataFrame:
    if production.empty or stance.empty:
        return pd.DataFrame()
    merged = production.merge(
        stance,
        on=["central_bank_code", "currency"],
        suffixes=("_production", "_stance"),
    )
    for metric in ["pearson_ic", "hit_rate", "information_ratio_annualized", "max_drawdown"]:
        merged[f"{metric}_delta"] = merged[f"{metric}_production"] - merged[f"{metric}_stance"]
    return merged


def latest_currency_signals(signals: pd.DataFrame) -> pd.DataFrame:
    if signals.empty:
        return signals
    return signals.sort_values("signal_date").groupby("central_bank_code").tail(1).sort_values("strength_score", ascending=False)


def build_monitoring_snapshot(
    signals: pd.DataFrame, contributions: pd.DataFrame, observations: pd.DataFrame
) -> pd.DataFrame:
    rows = []
    latest_date = signals["signal_date"].max() if not signals.empty else None
    for bank, mapping in CURRENCY_MAP.items():
        bank_signals = signals[signals["central_bank_code"] == bank]
        bank_contrib = contributions[contributions["central_bank_code"] == bank]
        bank_obs = observations[observations["central_bank_code"] == bank]
        latest_signal = bank_signals.sort_values("signal_date").tail(1)
        rows.append(
            {
                "central_bank_code": bank,
                "currency": mapping["currency"],
                "latest_signal_date": latest_signal["signal_date"].iloc[0].date().isoformat() if not latest_signal.empty else None,
                "latest_strength_score": float(latest_signal["strength_score"].iloc[0]) if not latest_signal.empty else None,
                "latest_available_indicators": int(latest_signal["available_indicators"].iloc[0]) if not latest_signal.empty else 0,
                "tracked_contribution_indicators": int(bank_contrib["indicator_key"].nunique()) if not bank_contrib.empty else 0,
                "latest_source_release": bank_obs["release_timestamp_utc"].max().isoformat() if not bank_obs.empty else None,
                "status": "ok" if not latest_signal.empty and int(latest_signal["available_indicators"].iloc[0]) >= 6 else "review",
            }
        )
    return pd.DataFrame(rows)


def write_report(
    output_path: Path,
    production: pd.DataFrame,
    stance: pd.DataFrame,
    comparison: pd.DataFrame,
    latest: pd.DataFrame,
) -> None:
    report = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "summary": {
            "production_validation_rows": len(production),
            "stance_validation_rows": len(stance),
            "comparison_rows": len(comparison),
        },
        "production_metrics": production.to_dict(orient="records"),
        "existing_stance_metrics": stance.to_dict(orient="records"),
        "comparison": comparison.to_dict(orient="records"),
        "latest_signals": latest.to_dict(orient="records"),
        "production_readiness": {
            "status": "research_candidate",
            "reason": "Strict release-aligned backtest is implemented, but predictive metrics remain mixed and should be monitored before replacing the existing stance layer.",
        },
    }
    (output_path / "final_validation_report.json").write_text(
        json.dumps(report, indent=2, default=json_default),
        encoding="utf-8",
    )


def write_model_documentation(
    output_path: Path,
    weights: pd.DataFrame,
    production: pd.DataFrame,
    stance: pd.DataFrame,
    comparison: pd.DataFrame,
) -> None:
    lines = [
        "# Production Currency Strength Model",
        "",
        "## Model Overview and Objectives",
        "",
        "The production-candidate currency strength model converts macroeconomic indicator releases into a normalized strength score for each currency. The objective is to provide an interpretable macro signal that can be compared across AUD, CAD, CHF, EUR, GBP, JPY, NZD, and USD.",
        "",
        "## Data Sources and Preprocessing",
        "",
        "- `data/eda/eda_observations.csv`: cleaned macro observations with normalized values and release timestamps.",
        "- `data/currency_strength_refinement/recommended_indicator_subset.csv`: production subset selected from refined weights.",
        "- `data/currency_strength_refinement/refined_indicator_weights.csv`: refined signed weights.",
        "- `data/fx_validation/fx_returns.csv`: monthly FX returns used for validation.",
        "- `data/currency_stance/currency_stance.csv`: existing dashboard stance layer used as comparison.",
        "",
        "The signal calculation is release-date aligned: an indicator can only affect a signal date if its `release_timestamp_utc` is less than or equal to that signal date.",
        "",
        "## Indicator Selection and Weighting",
        "",
        "Indicators are selected from the refined subset. Weights are signed: positive weights mean higher indicator values contribute to currency strength, while negative weights mean higher values detract from currency strength. The selected weights are renormalized by currency so the absolute weights sum to 1.",
        "",
        "### Selected Indicator Count",
        "",
        dataframe_to_markdown(
            weights.groupby(["central_bank_code", "currency"]).agg(
                indicators=("indicator_key", "nunique"),
                abs_weight_sum=("production_abs_weight", "sum"),
            ).reset_index()
        ),
        "",
        "## Assumptions and Limitations",
        "",
        "- The model is monthly in this validation pack; intraday/live scoring can reuse the same release-aligned function with a current `as_of` timestamp.",
        "- Normalized indicator values are inherited from the EDA layer. A production hardening step should calculate normalization parameters from training windows only.",
        "- FX validation uses USD pairs and inverts USD-quoted pairs so positive forward return means local-currency strength.",
        "- Inflation effects are regime-dependent and should not be interpreted mechanically.",
        "",
        "## Final Validation Results",
        "",
        "### Production Candidate Metrics",
        "",
        dataframe_to_markdown(production),
        "",
        "### Existing Currency Stance Metrics",
        "",
        dataframe_to_markdown(stance) if not stance.empty else "Existing stance comparison was unavailable.",
        "",
        "### Production vs Existing Stance",
        "",
        dataframe_to_markdown(comparison) if not comparison.empty else "No overlapping comparison rows were available.",
        "",
        "## Maintenance and Update Procedures",
        "",
        "1. Refresh macro releases from EODHD.",
        "2. Rebuild `processed.eda_observations` and EDA analysis outputs.",
        "3. Rebuild currency strength weights and refinement outputs.",
        "4. Run `python -m scripts.build_production_currency_strength`.",
        "5. Review monitoring snapshot, validation metrics, and latest signal changes before publishing.",
        "",
    ]
    (output_path / "MODEL_DOCUMENTATION.md").write_text("\n".join(lines), encoding="utf-8")


def write_integration_plan(output_path: Path) -> None:
    lines = [
        "# Production Integration Plan",
        "",
        "## Deployment Steps",
        "",
        "1. Add `scripts.build_production_currency_strength` to the scheduled data pipeline after macro ingestion and EDA preprocessing.",
        "2. Store the latest output table `production_currency_strength_signals.csv` or its database equivalent for the dashboard API.",
        "3. Expose latest rows from `latest_currency_strength_signals.csv` in the frontend currency strength panel.",
        "4. Keep the existing currency stance layer visible during an observation period and label the new model as `research_candidate` until metrics stabilize.",
        "",
        "## Infrastructure Requirements",
        "",
        "- Python dependencies already present in the project: pandas, numpy, plotly.",
        "- Existing Docker app container and Postgres database.",
        "- Scheduled execution after successful data ingestion.",
        "- Persistent storage for generated reports and validation metrics.",
        "",
        "## DevOps Checklist",
        "",
        "- Add pipeline step health checks.",
        "- Alert on missing latest signals, low indicator coverage, or stale source releases.",
        "- Archive model output versions with timestamped artifacts.",
        "- Add dashboard API endpoint only after acceptance of validation metrics.",
        "",
    ]
    (output_path / "PRODUCTION_INTEGRATION_PLAN.md").write_text("\n".join(lines), encoding="utf-8")


def write_monitoring_plan(output_path: Path) -> None:
    lines = [
        "# Monitoring and Maintenance Plan",
        "",
        "## KPIs",
        "",
        "- Indicator coverage by currency.",
        "- Latest source release age.",
        "- Monthly Pearson/Spearman information coefficient versus forward FX returns.",
        "- Directional hit rate.",
        "- Strategy information ratio and max drawdown.",
        "- Signal turnover and large one-period score changes.",
        "- Data quality flags from the EDA layer.",
        "",
        "## Reporting Schedule",
        "",
        "- Daily: data freshness and latest signal availability.",
        "- Weekly: indicator coverage and large signal changes.",
        "- Monthly: validation metrics after FX returns are available.",
        "- Quarterly: weight recalibration review and stakeholder sign-off.",
        "",
        "## Feedback Loop",
        "",
        "Collect trader/researcher annotations for false positives, regime shifts, and indicators that behaved counterintuitively. Feed those notes into the next weight refinement review.",
        "",
        "## Update Process",
        "",
        "1. Refresh macro data.",
        "2. Rebuild EDA and currency strength weights.",
        "3. Compare new weights against the previous production version.",
        "4. Run backtest and monitoring checks.",
        "5. Promote only after review if validation does not degrade materially.",
        "",
    ]
    (output_path / "MONITORING_AND_MAINTENANCE_PLAN.md").write_text("\n".join(lines), encoding="utf-8")


def write_plots(
    output_path: Path,
    signals: pd.DataFrame,
    production: pd.DataFrame,
    stance: pd.DataFrame,
    comparison: pd.DataFrame,
    latest: pd.DataFrame,
) -> None:
    if not signals.empty:
        fig = px.line(
            signals,
            x="signal_date",
            y="strength_score",
            color="currency",
            title="Production-Candidate Currency Strength Scores",
        )
        fig.write_html(output_path / "production_strength_scores.html", include_plotlyjs="cdn")
    if not latest.empty:
        fig = px.bar(
            latest.sort_values("strength_score"),
            x="strength_score",
            y="currency",
            color="strength_label",
            orientation="h",
            title="Latest Production-Candidate Currency Strength Ranking",
        )
        fig.write_html(output_path / "latest_strength_ranking.html", include_plotlyjs="cdn")
    if not production.empty:
        metric_plot = production[["currency", "pearson_ic", "hit_rate", "information_ratio_annualized"]].melt(
            id_vars="currency", var_name="metric", value_name="value"
        )
        fig = px.bar(metric_plot, x="currency", y="value", color="metric", barmode="group", title="Production Validation Metrics")
        fig.write_html(output_path / "production_validation_metrics.html", include_plotlyjs="cdn")
    if not comparison.empty:
        deltas = comparison[
            ["currency", "pearson_ic_delta", "hit_rate_delta", "information_ratio_annualized_delta"]
        ].melt(id_vars="currency", var_name="metric_delta", value_name="delta")
        fig = px.bar(deltas, x="currency", y="delta", color="metric_delta", barmode="group", title="Production vs Existing Stance Metric Deltas")
        fig.write_html(output_path / "stance_comparison.html", include_plotlyjs="cdn")


def dataframe_to_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    rendered = df.copy()
    for column in rendered.columns:
        if pd.api.types.is_datetime64_any_dtype(rendered[column]):
            rendered[column] = rendered[column].astype(str)
        elif pd.api.types.is_float_dtype(rendered[column]):
            rendered[column] = rendered[column].map(lambda value: f"{value:.4f}")
    headers = list(rendered.columns)
    rows = rendered.astype(str).values.tolist()
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)
