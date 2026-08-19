"""Correlate one headline indicator against every other indicator in the same country.

Reads the already-deduplicated processed.macro_observations table. Weekly and
quarterly series are resampled onto the anchor indicator's monthly calendar so
every pair is compared on a common frequency.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from scipy import stats
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from statsmodels.stats.multitest import multipletests

from app.db.session import session_scope

MIN_PAIR_OBSERVATIONS = 8


async def load_country_observations(session: AsyncSession, country_code: str) -> pd.DataFrame:
    result = await session.execute(
        text(
            """
            SELECT indicator_key, indicator_name, primary_category, frequency,
                   coalesce(reference_period_start, release_date_utc) AS period_anchor,
                   actual_value
            FROM processed.macro_observations
            WHERE country_code = :country_code
              AND is_latest = true
              AND actual_value IS NOT NULL
            ORDER BY indicator_key, period_anchor
            """
        ),
        {"country_code": country_code},
    )
    df = pd.DataFrame(result.mappings().all())
    df["actual_value"] = df["actual_value"].astype(float)
    return df


def to_monthly_panel(obs: pd.DataFrame) -> pd.DataFrame:
    """Pivot to one column per indicator, one row per month."""
    obs = obs.copy()
    obs["month"] = pd.PeriodIndex(pd.to_datetime(obs["period_anchor"]), freq="M")

    columns = []
    for key, group in obs.groupby("indicator_key"):
        freq = group["frequency"].iloc[0]
        monthly = group.groupby("month")["actual_value"].mean()
        if freq in ("quarterly", "irregular"):
            full_index = pd.period_range(monthly.index.min(), monthly.index.max(), freq="M")
            limit = 2 if freq == "quarterly" else None
            monthly = monthly.reindex(full_index).ffill(limit=limit)
        columns.append(monthly.rename(key))

    return pd.concat(columns, axis=1).sort_index()


def correlate_pair(anchor: pd.Series, other: pd.Series, lag: int) -> dict[str, Any]:
    """anchor(t) vs other(t + lag). Positive lag means the anchor leads."""
    paired = pd.concat([anchor, other.shift(-lag)], axis=1).dropna()
    n = len(paired)
    if n < MIN_PAIR_OBSERVATIONS:
        return {"n": n, "pearson_r": None, "spearman_r": None, "spearman_p": None}
    pearson_r, _ = stats.pearsonr(paired.iloc[:, 0], paired.iloc[:, 1])
    spearman_r, spearman_p = stats.spearmanr(paired.iloc[:, 0], paired.iloc[:, 1])
    return {
        "n": n,
        "pearson_r": round(float(pearson_r), 4),
        "spearman_r": round(float(spearman_r), 4),
        "spearman_p": round(float(spearman_p), 4),
    }


async def build_indicator_correlation_study(
    country_code: str,
    anchor_indicator_key: str,
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    async with session_scope() as session:
        obs = await load_country_observations(session, country_code)

    if anchor_indicator_key not in obs["indicator_key"].unique():
        raise ValueError(f"{anchor_indicator_key!r} has no observations for {country_code}")

    panel = to_monthly_panel(obs)
    anchor = panel[anchor_indicator_key].dropna()
    meta = obs.drop_duplicates("indicator_key").set_index("indicator_key")

    rows = []
    for key in panel.columns:
        if key == anchor_indicator_key:
            continue
        other = panel[key]
        same = correlate_pair(anchor, other, lag=0)
        anchor_leads = correlate_pair(anchor, other, lag=1)
        anchor_lags = correlate_pair(anchor, other, lag=-1)
        rows.append(
            {
                "indicator_key": key,
                "indicator_name": meta.loc[key, "indicator_name"],
                "category": meta.loc[key, "primary_category"],
                "frequency": meta.loc[key, "frequency"],
                "n_same_month": same["n"],
                "pearson_same_month": same["pearson_r"],
                "spearman_same_month": same["spearman_r"],
                "p_same_month": same["spearman_p"],
                "n_anchor_leads_1mo": anchor_leads["n"],
                "spearman_anchor_leads_1mo": anchor_leads["spearman_r"],
                "n_anchor_lags_1mo": anchor_lags["n"],
                "spearman_anchor_lags_1mo": anchor_lags["spearman_r"],
            }
        )

    results = pd.DataFrame(rows)

    valid = results["p_same_month"].notna()
    results["fdr_significant_q10"] = False
    if valid.sum() > 0:
        reject, _, _, _ = multipletests(
            results.loc[valid, "p_same_month"].to_numpy(), alpha=0.10, method="fdr_bh"
        )
        results.loc[valid, "fdr_significant_q10"] = reject

    results["_abs_rank"] = results["spearman_same_month"].abs()
    results = results.sort_values("_abs_rank", ascending=False, na_position="last").drop(
        columns="_abs_rank"
    )

    csv_path = output_dir / f"{country_code.lower()}_{anchor_indicator_key}_correlations.csv"
    results.to_csv(csv_path, index=False)

    summary = {
        "country_code": country_code,
        "anchor_indicator_key": anchor_indicator_key,
        "anchor_observation_count": int(len(anchor)),
        "anchor_period_start": str(anchor.index.min()),
        "anchor_period_end": str(anchor.index.max()),
        "n_indicators_compared": int(len(results)),
        "csv_path": str(csv_path),
    }
    json_path = output_dir / f"{country_code.lower()}_{anchor_indicator_key}_summary.json"
    json_path.write_text(json.dumps(summary, indent=2))

    return summary
