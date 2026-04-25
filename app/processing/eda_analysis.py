"""Generate exploratory analysis artifacts from the cleaned EDA dataset."""

from __future__ import annotations

import json
import warnings
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from sqlalchemy import text
from statsmodels.tsa.stattools import adfuller, grangercausalitytests, kpss

from app.db.session import session_scope


MAX_LAG = 6
MIN_SERIES_POINTS = 12
MIN_PAIR_POINTS = 18
MAX_GRANGER_PAIRS = 250


def json_default(value: Any) -> str | float | int | bool | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, np.generic):
        return value.item()
    return str(value)


async def build_eda_analysis(
    output_dir: Path | str = Path("data/eda/analysis"),
) -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    async with session_scope() as session:
        result = await session.execute(
            text(
                """
                SELECT *
                FROM processed.eda_observations
                ORDER BY date, central_bank_code, indicator_key
                """
            )
        )
        rows = [dict(row) for row in result.mappings().all()]

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("processed.eda_observations is empty. Run build_eda_dataset first.")

    df["date"] = pd.to_datetime(df["date"])
    for column in [
        "value",
        "estimate_value",
        "previous_value",
        "surprise_value",
        "value_zscore",
        "value_minmax",
        "value_normalized",
    ]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    descriptive = descriptive_statistics(df)
    stationarity = stationarity_tests(df)
    correlations, redundant = correlation_analysis(df)
    lag_results = lag_analysis(df)
    granger = granger_analysis(df, lag_results)
    pca_variance, pca_loadings = pca_analysis(df)
    subsamples = subsample_analysis(df)

    files = {
        "descriptive_statistics.csv": descriptive,
        "stationarity_tests.csv": stationarity,
        "correlations.csv": correlations,
        "high_correlation_pairs.csv": redundant,
        "lag_analysis.csv": lag_results,
        "granger_causality.csv": granger,
        "pca_explained_variance.csv": pca_variance,
        "pca_loadings.csv": pca_loadings,
        "subsample_summary.csv": subsamples,
    }
    for filename, frame in files.items():
        frame.to_csv(output_path / filename, index=False)

    plot_files = write_plots(output_path, df, correlations, lag_results, pca_variance)
    report = build_report(
        df,
        descriptive,
        stationarity,
        correlations,
        redundant,
        lag_results,
        granger,
        pca_variance,
        pca_loadings,
        subsamples,
        plot_files,
    )
    (output_path / "eda_report.json").write_text(
        json.dumps(report, default=json_default, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_readme(output_path)

    return {
        "output_dir": str(output_path),
        "files": sorted([*files.keys(), *plot_files, "eda_report.json", "README.md"]),
        "rows": int(len(df)),
        "series": int(df[["central_bank_code", "indicator_key"]].drop_duplicates().shape[0]),
        "correlation_pairs": int(len(correlations)),
        "lag_pairs": int(len(lag_results)),
        "granger_tests": int(len(granger)),
    }


def descriptive_statistics(df: pd.DataFrame) -> pd.DataFrame:
    grouped = df.groupby(
        [
            "central_bank_code",
            "country",
            "indicator_key",
            "indicator",
            "primary_category",
            "frequency",
            "importance",
        ],
        dropna=False,
    )
    stats = grouped.agg(
        observations=("value", "count"),
        mean=("value", "mean"),
        median=("value", "median"),
        std=("value", "std"),
        min=("value", "min"),
        max=("value", "max"),
        skew=("value", "skew"),
        first_date=("date", "min"),
        last_date=("date", "max"),
        outlier_iqr_count=("is_outlier_iqr", "sum"),
    ).reset_index()
    stats["range"] = stats["max"] - stats["min"]
    return stats.sort_values(["central_bank_code", "primary_category", "indicator_key"])


def stationarity_tests(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for key, series_df in iter_series(df):
        values = series_df["value"].dropna().astype(float)
        row = base_series_row(key, series_df)
        row["observations"] = int(len(values))
        row["adf_statistic"] = None
        row["adf_p_value"] = None
        row["kpss_statistic"] = None
        row["kpss_p_value"] = None
        row["likely_stationary"] = None
        row["first_difference_adf_p_value"] = None

        if len(values) >= MIN_SERIES_POINTS and values.nunique() > 3:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    adf_result = adfuller(values, autolag="AIC")
                    row["adf_statistic"] = float(adf_result[0])
                    row["adf_p_value"] = float(adf_result[1])
                except Exception as exc:
                    row["adf_error"] = str(exc)

                try:
                    kpss_result = kpss(values, regression="c", nlags="auto")
                    row["kpss_statistic"] = float(kpss_result[0])
                    row["kpss_p_value"] = float(kpss_result[1])
                except Exception as exc:
                    row["kpss_error"] = str(exc)

                diff_values = values.diff().dropna()
                if len(diff_values) >= MIN_SERIES_POINTS and diff_values.nunique() > 3:
                    try:
                        row["first_difference_adf_p_value"] = float(
                            adfuller(diff_values, autolag="AIC")[1]
                        )
                    except Exception as exc:
                        row["first_difference_error"] = str(exc)

        if row["adf_p_value"] is not None and row["kpss_p_value"] is not None:
            row["likely_stationary"] = bool(row["adf_p_value"] < 0.05 and row["kpss_p_value"] > 0.05)
        rows.append(row)

    return pd.DataFrame(rows).sort_values(["central_bank_code", "indicator_key"])


def correlation_analysis(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    for central_bank, cb_df in df.groupby("central_bank_code"):
        pivot = make_pivot(cb_df)
        if pivot.shape[1] < 2:
            continue
        corr = pivot.corr(min_periods=MIN_PAIR_POINTS)
        metadata = cb_df.drop_duplicates("indicator_key").set_index("indicator_key")
        for i, left in enumerate(corr.columns):
            for right in corr.columns[i + 1:]:
                value = corr.loc[left, right]
                if pd.isna(value):
                    continue
                overlap = int(pivot[[left, right]].dropna().shape[0])
                rows.append(
                    {
                        "central_bank_code": central_bank,
                        "left_indicator_key": left,
                        "left_indicator": metadata.loc[left, "indicator"],
                        "left_category": metadata.loc[left, "primary_category"],
                        "left_importance": int(metadata.loc[left, "importance"]),
                        "right_indicator_key": right,
                        "right_indicator": metadata.loc[right, "indicator"],
                        "right_category": metadata.loc[right, "primary_category"],
                        "right_importance": int(metadata.loc[right, "importance"]),
                        "observations": overlap,
                        "correlation": float(value),
                        "abs_correlation": float(abs(value)),
                    }
                )

    correlations = pd.DataFrame(rows)
    if correlations.empty:
        return correlations, correlations
    correlations = correlations.sort_values(
        ["abs_correlation", "observations"], ascending=[False, False]
    )
    redundant = correlations[
        (correlations["abs_correlation"] >= 0.85)
        & (correlations["left_category"] == correlations["right_category"])
    ].copy()
    return correlations, redundant


def lag_analysis(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for central_bank, cb_df in df.groupby("central_bank_code"):
        metadata = cb_df.drop_duplicates("indicator_key").set_index("indicator_key")
        pivot = make_pivot(cb_df)
        target_keys = metadata[metadata["importance"] == 1].index.tolist()
        candidate_keys = metadata[metadata["importance"] > 1].index.tolist()

        for target in target_keys:
            for candidate in candidate_keys:
                if target == candidate:
                    continue
                if target not in pivot.columns or candidate not in pivot.columns:
                    continue
                if metadata.loc[target, "primary_category"] != metadata.loc[candidate, "primary_category"]:
                    continue
                pair = pivot[[target, candidate]].dropna()
                if len(pair) < MIN_PAIR_POINTS:
                    continue
                best = best_lag_correlation(pair[target], pair[candidate])
                if best is None:
                    continue
                lag, corr, overlap = best
                rows.append(
                    {
                        "central_bank_code": central_bank,
                        "target_indicator_key": target,
                        "target_indicator": metadata.loc[target, "indicator"],
                        "target_category": metadata.loc[target, "primary_category"],
                        "candidate_indicator_key": candidate,
                        "candidate_indicator": metadata.loc[candidate, "indicator"],
                        "candidate_category": metadata.loc[candidate, "primary_category"],
                        "best_lag_periods": lag,
                        "best_lag_correlation": corr,
                        "abs_best_lag_correlation": abs(corr),
                        "observations": overlap,
                    }
                )
    lag_df = pd.DataFrame(rows)
    if lag_df.empty:
        return lag_df
    return lag_df.sort_values(["abs_best_lag_correlation", "observations"], ascending=[False, False])


def best_lag_correlation(target: pd.Series, candidate: pd.Series) -> tuple[int, float, int] | None:
    best: tuple[int, float, int] | None = None
    for lag in range(MAX_LAG + 1):
        aligned = pd.concat([target, candidate.shift(lag)], axis=1).dropna()
        if len(aligned) < MIN_PAIR_POINTS:
            continue
        corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
        if pd.isna(corr):
            continue
        current = (lag, float(corr), int(len(aligned)))
        if best is None or abs(current[1]) > abs(best[1]):
            best = current
    return best


def granger_analysis(df: pd.DataFrame, lag_results: pd.DataFrame) -> pd.DataFrame:
    if lag_results.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    candidates = lag_results.head(MAX_GRANGER_PAIRS)
    for _, lag_row in candidates.iterrows():
        central_bank = lag_row["central_bank_code"]
        target = lag_row["target_indicator_key"]
        candidate = lag_row["candidate_indicator_key"]
        cb_df = df[df["central_bank_code"] == central_bank]
        pivot = make_pivot(cb_df)
        if target not in pivot.columns or candidate not in pivot.columns:
            continue
        pair = pivot[[target, candidate]].dropna()
        if len(pair) < max(MIN_PAIR_POINTS, MAX_LAG * 4):
            continue
        maxlag = min(MAX_LAG, max(1, len(pair) // 6))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                result = grangercausalitytests(pair[[target, candidate]], maxlag=maxlag, verbose=False)
            except Exception as exc:
                rows.append(
                    {
                        **lag_row.to_dict(),
                        "tested_max_lag": maxlag,
                        "best_granger_lag": None,
                        "min_granger_p_value": None,
                        "granger_error": str(exc),
                    }
                )
                continue

        p_values = {
            lag: float(test_result[0]["ssr_ftest"][1])
            for lag, test_result in result.items()
        }
        best_lag = min(p_values, key=p_values.get)
        rows.append(
            {
                **lag_row.to_dict(),
                "tested_max_lag": maxlag,
                "best_granger_lag": int(best_lag),
                "min_granger_p_value": p_values[best_lag],
                "granger_significant_05": bool(p_values[best_lag] < 0.05),
            }
        )
    granger_df = pd.DataFrame(rows)
    if granger_df.empty:
        return granger_df
    return granger_df.sort_values(["min_granger_p_value", "abs_best_lag_correlation"], ascending=[True, False])


def pca_analysis(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    variance_rows: list[dict[str, Any]] = []
    loading_rows: list[dict[str, Any]] = []

    for central_bank, cb_df in df.groupby("central_bank_code"):
        pivot = make_pivot(cb_df)
        usable = pivot.dropna(axis=1, thresh=MIN_SERIES_POINTS)
        if usable.shape[1] < 3 or usable.shape[0] < MIN_PAIR_POINTS:
            continue
        filled = usable.interpolate(limit_direction="both").ffill().bfill()
        filled = filled.fillna(filled.mean())
        standardized = (filled - filled.mean()) / filled.std(ddof=0).replace(0, np.nan)
        standardized = standardized.dropna(axis=1)
        if standardized.shape[1] < 3:
            continue

        matrix = standardized.to_numpy(dtype=float)
        _, singular_values, vh = np.linalg.svd(matrix, full_matrices=False)
        explained = singular_values**2 / np.sum(singular_values**2)
        columns = standardized.columns.tolist()
        for component_index, ratio in enumerate(explained[:5], start=1):
            variance_rows.append(
                {
                    "central_bank_code": central_bank,
                    "component": f"PC{component_index}",
                    "explained_variance_ratio": float(ratio),
                    "cumulative_explained_variance": float(explained[:component_index].sum()),
                    "indicator_count": len(columns),
                    "observation_count": standardized.shape[0],
                }
            )

        metadata = cb_df.drop_duplicates("indicator_key").set_index("indicator_key")
        for component_index in range(min(3, vh.shape[0])):
            component_loadings = pd.Series(vh[component_index], index=columns)
            top_loadings = component_loadings.abs().sort_values(ascending=False).head(12).index
            for indicator_key in top_loadings:
                loading_rows.append(
                    {
                        "central_bank_code": central_bank,
                        "component": f"PC{component_index + 1}",
                        "indicator_key": indicator_key,
                        "indicator": metadata.loc[indicator_key, "indicator"],
                        "primary_category": metadata.loc[indicator_key, "primary_category"],
                        "loading": float(component_loadings.loc[indicator_key]),
                        "abs_loading": float(abs(component_loadings.loc[indicator_key])),
                    }
                )

    return pd.DataFrame(variance_rows), pd.DataFrame(loading_rows)


def subsample_analysis(df: pd.DataFrame) -> pd.DataFrame:
    sample_df = df.copy()
    sample_df["regime"] = pd.cut(
        sample_df["date"].dt.year,
        bins=[0, 2021, 2023, 9999],
        labels=["pre_2022", "2022_2023", "2024_plus"],
    )
    return (
        sample_df.groupby(["regime", "central_bank_code", "primary_category"], observed=True)
        .agg(
            observations=("value", "count"),
            indicators=("indicator_key", "nunique"),
            mean_normalized=("value_normalized", "mean"),
            std_normalized=("value_normalized", "std"),
            outlier_iqr_count=("is_outlier_iqr", "sum"),
        )
        .reset_index()
        .sort_values(["regime", "central_bank_code", "primary_category"])
    )


def write_plots(
    output_path: Path,
    df: pd.DataFrame,
    correlations: pd.DataFrame,
    lag_results: pd.DataFrame,
    pca_variance: pd.DataFrame,
) -> list[str]:
    plot_files: list[str] = []

    fig = make_subplots(rows=2, cols=1, subplot_titles=("Observations by Central Bank", "Observations by Category"))
    cb_counts = df["central_bank_code"].value_counts().sort_index()
    category_counts = df["primary_category"].value_counts().sort_index()
    fig.add_trace(go.Bar(x=cb_counts.index, y=cb_counts.values, name="Central banks"), row=1, col=1)
    fig.add_trace(go.Bar(x=category_counts.index, y=category_counts.values, name="Categories"), row=2, col=1)
    fig.update_layout(height=760, title="EDA Coverage Overview", showlegend=False)
    fig.write_html(output_path / "coverage_overview.html", include_plotlyjs="cdn")
    plot_files.append("coverage_overview.html")

    top_series = (
        df[df["importance"] == 1]
        .sort_values(["central_bank_code", "primary_category", "indicator_key", "date"])
        .groupby(["central_bank_code", "indicator_key"])
        .filter(lambda group: len(group) >= MIN_SERIES_POINTS)
    )
    sample_keys = (
        top_series[["central_bank_code", "indicator_key"]]
        .drop_duplicates()
        .groupby("central_bank_code")
        .head(3)
    )
    sample = top_series.merge(sample_keys, on=["central_bank_code", "indicator_key"])
    fig = px.line(
        sample,
        x="date",
        y="value_normalized",
        color="indicator",
        facet_col="central_bank_code",
        facet_col_wrap=2,
        title="Sample Headline Time Series, Normalized",
    )
    fig.update_layout(height=1100)
    fig.write_html(output_path / "time_series_samples.html", include_plotlyjs="cdn")
    plot_files.append("time_series_samples.html")

    fig = px.histogram(
        df,
        x="value_normalized",
        color="primary_category",
        facet_col="central_bank_code",
        facet_col_wrap=2,
        nbins=60,
        title="Normalized Value Distributions",
    )
    fig.update_layout(height=1100)
    fig.write_html(output_path / "distribution_histograms.html", include_plotlyjs="cdn")
    plot_files.append("distribution_histograms.html")

    if not correlations.empty:
        top_corr = correlations.head(80).copy()
        top_corr["pair"] = (
            top_corr["left_indicator_key"] + " vs " + top_corr["right_indicator_key"]
        )
        fig = px.bar(
            top_corr.sort_values("abs_correlation"),
            x="abs_correlation",
            y="pair",
            color="central_bank_code",
            orientation="h",
            title="Strongest Absolute Correlations",
        )
        fig.update_layout(height=1300, yaxis={"dtick": 1})
        fig.write_html(output_path / "top_correlations.html", include_plotlyjs="cdn")
        plot_files.append("top_correlations.html")

    if not lag_results.empty:
        top_lags = lag_results.head(80).copy()
        top_lags["pair"] = (
            top_lags["candidate_indicator_key"] + " -> " + top_lags["target_indicator_key"]
        )
        fig = px.scatter(
            top_lags,
            x="best_lag_periods",
            y="best_lag_correlation",
            color="central_bank_code",
            size="observations",
            hover_name="pair",
            title="Top Candidate Lead/Lag Relationships",
        )
        fig.write_html(output_path / "lag_relationships.html", include_plotlyjs="cdn")
        plot_files.append("lag_relationships.html")

    if not pca_variance.empty:
        fig = px.bar(
            pca_variance,
            x="component",
            y="explained_variance_ratio",
            color="central_bank_code",
            barmode="group",
            title="PCA Explained Variance by Central Bank",
        )
        fig.write_html(output_path / "pca_explained_variance.html", include_plotlyjs="cdn")
        plot_files.append("pca_explained_variance.html")

    return plot_files


def build_report(
    df: pd.DataFrame,
    descriptive: pd.DataFrame,
    stationarity: pd.DataFrame,
    correlations: pd.DataFrame,
    redundant: pd.DataFrame,
    lag_results: pd.DataFrame,
    granger: pd.DataFrame,
    pca_variance: pd.DataFrame,
    pca_loadings: pd.DataFrame,
    subsamples: pd.DataFrame,
    plot_files: list[str],
) -> dict[str, Any]:
    stationary_count = (
        int(stationarity["likely_stationary"].fillna(False).sum())
        if "likely_stationary" in stationarity
        else 0
    )
    significant_granger = (
        int(granger["granger_significant_05"].fillna(False).sum())
        if "granger_significant_05" in granger
        else 0
    )
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "input": {
            "rows": int(len(df)),
            "series": int(df[["central_bank_code", "indicator_key"]].drop_duplicates().shape[0]),
            "date_start": df["date"].min().date().isoformat(),
            "date_end": df["date"].max().date().isoformat(),
        },
        "descriptive_statistics": {
            "series_profiled": int(len(descriptive)),
            "highest_outlier_series": records(descriptive.sort_values("outlier_iqr_count", ascending=False).head(10)),
        },
        "stationarity": {
            "series_tested": int(len(stationarity)),
            "likely_stationary_count": stationary_count,
            "likely_non_stationary_count": int(len(stationarity) - stationary_count),
        },
        "correlation": {
            "pairs": int(len(correlations)),
            "high_same_category_pairs": int(len(redundant)),
            "top_pairs": records(correlations.head(15)),
        },
        "lag_analysis": {
            "pairs": int(len(lag_results)),
            "top_pairs": records(lag_results.head(15)),
        },
        "granger": {
            "tests": int(len(granger)),
            "significant_05": significant_granger,
            "top_tests": records(granger.head(15)),
        },
        "pca": {
            "central_banks": sorted(pca_variance["central_bank_code"].unique().tolist())
            if not pca_variance.empty
            else [],
            "variance_rows": int(len(pca_variance)),
            "loading_rows": int(len(pca_loadings)),
        },
        "subsamples": {"rows": int(len(subsamples))},
        "plots": plot_files,
    }


def records(df: pd.DataFrame) -> list[dict[str, Any]]:
    return df.replace({np.nan: None}).to_dict(orient="records")


def iter_series(df: pd.DataFrame):
    grouped = df.sort_values("date").groupby(["central_bank_code", "indicator_key"], sort=True)
    for (central_bank, indicator_key), series_df in grouped:
        yield (central_bank, indicator_key), series_df


def base_series_row(key: tuple[str, str], series_df: pd.DataFrame) -> dict[str, Any]:
    central_bank, indicator_key = key
    first = series_df.iloc[0]
    return {
        "central_bank_code": central_bank,
        "indicator_key": indicator_key,
        "indicator": first["indicator"],
        "primary_category": first["primary_category"],
        "frequency": first["frequency"],
        "importance": int(first["importance"]),
        "first_date": series_df["date"].min().date().isoformat(),
        "last_date": series_df["date"].max().date().isoformat(),
    }


def make_pivot(df: pd.DataFrame) -> pd.DataFrame:
    pivot = (
        df.pivot_table(
            index="date",
            columns="indicator_key",
            values="value_normalized",
            aggfunc="mean",
        )
        .sort_index()
    )
    return pivot.dropna(axis=1, thresh=MIN_SERIES_POINTS)


def write_readme(output_path: Path) -> None:
    (output_path / "README.md").write_text(
        "\n".join(
            [
                "# EDA Analysis Outputs",
                "",
                "Generated by `python -m scripts.build_eda_analysis` from `processed.eda_observations`.",
                "",
                "CSV outputs:",
                "- `descriptive_statistics.csv`: mean, median, std, min, max, skew, range, outlier counts.",
                "- `stationarity_tests.csv`: ADF/KPSS results and first-difference ADF checks.",
                "- `correlations.csv`: pairwise normalized correlations by central bank.",
                "- `high_correlation_pairs.csv`: same-category pairs with absolute correlation >= 0.85.",
                "- `lag_analysis.csv`: strongest candidate sub-indicator lag vs headline target.",
                "- `granger_causality.csv`: Granger tests for the strongest lag candidates.",
                "- `pca_explained_variance.csv` and `pca_loadings.csv`: PCA variance and component drivers.",
                "- `subsample_summary.csv`: regime summaries for pre-2022, 2022-2023, and 2024+.",
                "",
                "HTML outputs are interactive Plotly charts for coverage, distributions, sample time series,",
                "top correlations, lag relationships, and PCA explained variance.",
                "",
            ]
        ),
        encoding="utf-8",
    )
