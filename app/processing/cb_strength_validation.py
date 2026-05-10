"""CB strength score validation against forward FX returns.

Methodology
-----------
1. Load raw theme component scores (inflation / labour / growth) per currency
   per date from processed.cb_strength_score — these were stored alongside
   the default-weight scores and are reused for the grid search.

2. Load forward FX returns (5 / 10 / 21 trading-day horizons) computed from
   processed.fx_price_history using LEAD() window functions.

3. Walk-forward split (dates are configurable):
     Train  → optimise mandate weights via grid search
     Val    → confirm the best weights generalise before touching test
     Test   → single held-out evaluation, never used during tuning

4. Grid search (training period only):
   For each combination of (inflation_w, labor_w, growth_w):
     a. recompute CB pressure = weighted sum / total weight
     b. z-score normalise across currencies per date + window
     c. join to forward FX returns using the currency sign convention from
        fx_validation.py (return_sign_for_country: +1 for base, -1 for quote)
     d. compute directional accuracy at each horizon

   Best weights are chosen per mandate type (dual / inflation / single) by
   maximising directional accuracy on the 21-day horizon in the training set.

5. Report metrics (directional accuracy, Pearson r, Sharpe-like) for each
   period × window × horizon, plus a comparison to the policy_signals baseline.

FX pair → currency sign mapping (matches fx_validation.py FX_INSTRUMENTS):
    EURUSD  EUR +1   USD  n/a
    GBPUSD  GBP +1   USD  n/a
    USDJPY  JPY -1   USD  n/a
    USDCAD  CAD -1   USD  n/a
    AUDUSD  AUD +1   USD  n/a
    NZDUSD  NZD +1   USD  n/a
    USDCHF  CHF -1   USD  n/a
"""

from __future__ import annotations

import csv
import itertools
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope
from app.processing.macro_dataset import json_default


# ── Currency → pair mapping ────────────────────────────────────────────────────

# (pair_code, currency, return_sign)
# return_sign: +1 when the currency is the base (pair goes UP when currency strengthens)
#              -1 when the currency is the quote
CURRENCY_PAIR_MAP: list[tuple[str, str, int]] = [
    ("EURUSD", "EUR", +1),
    ("GBPUSD", "GBP", +1),
    ("USDJPY", "JPY", -1),
    ("USDCAD", "CAD", -1),
    ("AUDUSD", "AUD", +1),
    ("NZDUSD", "NZD", +1),
    ("USDCHF", "CHF", -1),
]

# Pair-divergence definitions: (pair_code, base_currency, quote_currency)
# Signal = base_cb_score - quote_cb_score; positive → pair goes up
PAIR_DIVERGENCE: list[tuple[str, str, str]] = [
    ("EURUSD", "EUR", "USD"),
    ("GBPUSD", "GBP", "USD"),
    ("USDJPY", "USD", "JPY"),
    ("USDCAD", "USD", "CAD"),
    ("AUDUSD", "AUD", "USD"),
    ("NZDUSD", "NZD", "USD"),
    ("USDCHF", "USD", "CHF"),
]

HORIZONS = (5, 10, 21)

# Grid search space — combinations evaluated on training period only
INFLATION_GRID = [1.00, 1.25, 1.50, 1.75, 2.00]
LABOR_GRID     = [0.25, 0.50, 0.75, 1.00]
GROWTH_GRID    = [0.25, 0.50, 0.75]


@dataclass
class ValidationConfig:
    train_start: date = date(2010, 1, 1)
    train_end:   date = date(2022, 12, 31)
    val_start:   date = date(2023, 1, 1)
    val_end:     date = date(2023, 12, 31)
    test_start:  date = date(2024, 1, 1)
    test_end:    date = date(2099, 12, 31)
    windows_months: tuple[int, ...] = (1, 2, 3)
    optimise_horizon: int = 21


@dataclass
class ValidationReport:
    config: dict[str, Any]
    grid_search: dict[str, Any]
    default_metrics: dict[str, Any]
    optimal_metrics: dict[str, Any]
    policy_baseline: dict[str, Any]
    pair_divergence: dict[str, Any]
    optimal_weights: list[dict[str, Any]]
    generated_at: str = field(
        default_factory=lambda: datetime.utcnow().isoformat() + "Z"
    )


# ── Entry point ───────────────────────────────────────────────────────────────

async def build_cb_strength_validation(
    output_dir: Path | str = Path("data/cb_strength"),
    config: ValidationConfig = ValidationConfig(),
) -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    val_dir = output_path / "validation"
    val_dir.mkdir(exist_ok=True)

    async with session_scope() as session:
        components = await _load_components(session, config)
        fx_returns = await _load_fx_forward_returns(session)
        policy_df  = await _load_policy_signals(session)

    if components.empty:
        return {"error": "No CB strength score data found — run build_cb_strength first."}
    if fx_returns.empty:
        return {"error": "No FX price history found — run build_fx_validation_layer first."}

    report_parts: dict[str, Any] = {
        "config": {
            "train":  f"{config.train_start} → {config.train_end}",
            "val":    f"{config.val_start} → {config.val_end}",
            "test":   f"{config.test_start} → {config.test_end}",
            "optimise_horizon": config.optimise_horizon,
        }
    }

    # ── Grid search (training period) ─────────────────────────────────────────
    print("Running grid search on training period…")
    grid_results, best_by_mandate = _grid_search(components, fx_returns, config)
    report_parts["grid_search_top10"] = grid_results[:10]
    report_parts["optimal_weights_by_mandate"] = best_by_mandate

    # Optimal weight entries (flat list for easy CSV export)
    optimal_rows = _build_optimal_weight_rows(best_by_mandate, components)
    report_parts["optimal_weights"] = optimal_rows

    # ── Metrics: default weights ───────────────────────────────────────────────
    print("Computing metrics with default weights…")
    default_samples = _build_samples(components, fx_returns, use_precomputed_score=True)
    report_parts["default_metrics"] = _aggregate_metrics(default_samples, config)

    # ── Metrics: optimal weights ───────────────────────────────────────────────
    print("Computing metrics with optimal weights…")
    components_opt = _apply_optimal_weights(components, best_by_mandate)
    opt_samples    = _build_samples(components_opt, fx_returns, use_precomputed_score=False)
    report_parts["optimal_metrics"] = _aggregate_metrics(opt_samples, config)

    # ── Policy signals baseline ────────────────────────────────────────────────
    if not policy_df.empty:
        print("Computing policy_signals baseline…")
        report_parts["policy_baseline"] = _policy_baseline_metrics(
            policy_df, fx_returns, config
        )

    # ── Pair divergence ────────────────────────────────────────────────────────
    print("Computing pair-divergence signals…")
    report_parts["pair_divergence"] = _pair_divergence_metrics(
        components_opt, fx_returns, config
    )

    # ── Export ────────────────────────────────────────────────────────────────
    _write_json(val_dir / "cb_validation_report.json", report_parts)
    _write_metrics_csv(val_dir / "cb_validation_metrics.csv", report_parts)
    _write_optimal_weights_csv(val_dir / "cb_optimal_weights.csv", optimal_rows)
    _write_grid_csv(val_dir / "cb_grid_search.csv", grid_results)

    print(f"Validation complete → {val_dir}")
    return report_parts


# ── Data loading ──────────────────────────────────────────────────────────────

async def _load_components(
    session: AsyncSession, config: ValidationConfig
) -> pd.DataFrame:
    """Load raw theme scores + precomputed cb_strength_score per currency per date."""
    rows = await _fetch_all(
        session,
        """
        SELECT
            date,
            country_code,
            currency,
            window_months,
            inflation_score,
            labor_score,
            growth_score,
            cb_strength_score,
            inflation_weight,
            labor_weight,
            growth_weight,
            mandate_type,
            categories_available,
            confidence
        FROM processed.cb_strength_score
        ORDER BY date, window_months, currency
        """,
    )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    for col in ("inflation_score", "labor_score", "growth_score",
                "cb_strength_score", "inflation_weight", "labor_weight", "growth_weight"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


async def _load_fx_forward_returns(session: AsyncSession) -> pd.DataFrame:
    """Compute forward returns from fx_price_history using LEAD by trading days.

    LEAD(N) approximates N-trading-day forward returns:
      LEAD(5)  ≈ 1-week  horizon
      LEAD(10) ≈ 2-week  horizon
      LEAD(21) ≈ 1-month horizon
    """
    rows = await _fetch_all(
        session,
        """
        WITH prices AS (
            SELECT
                pair_code,
                price_date,
                close_price,
                lead(close_price, 5)  OVER (PARTITION BY pair_code ORDER BY price_date)
                    AS fwd_5d,
                lead(close_price, 10) OVER (PARTITION BY pair_code ORDER BY price_date)
                    AS fwd_10d,
                lead(close_price, 21) OVER (PARTITION BY pair_code ORDER BY price_date)
                    AS fwd_21d
            FROM processed.fx_price_history
            WHERE close_price IS NOT NULL
              AND pair_code != 'USD_BASKET'
        )
        SELECT
            pair_code,
            price_date,
            (fwd_5d  / close_price - 1) AS ret_5d,
            (fwd_10d / close_price - 1) AS ret_10d,
            (fwd_21d / close_price - 1) AS ret_21d
        FROM prices
        WHERE fwd_5d IS NOT NULL
          OR  fwd_10d IS NOT NULL
          OR  fwd_21d IS NOT NULL
        ORDER BY pair_code, price_date
        """,
    )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["price_date"] = pd.to_datetime(df["price_date"]).dt.date
    for col in ("ret_5d", "ret_10d", "ret_21d"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


async def _load_policy_signals(session: AsyncSession) -> pd.DataFrame:
    rows = await _fetch_all(
        session,
        """
        SELECT signal_date AS date, country_code, currency_code AS currency, policy_score
        FROM processed.policy_signals
        ORDER BY signal_date, currency_code
        """,
    )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["policy_score"] = pd.to_numeric(df["policy_score"], errors="coerce")
    return df


# ── Validation sample builder ─────────────────────────────────────────────────

def _build_samples(
    components: pd.DataFrame,
    fx_returns: pd.DataFrame,
    use_precomputed_score: bool = True,
) -> pd.DataFrame:
    """Join CB scores with forward FX returns.

    use_precomputed_score=True  → use cb_strength_score column (default weights)
    use_precomputed_score=False → use recomputed_score column (optimal weights)
    """
    score_col = "cb_strength_score" if use_precomputed_score else "recomputed_score"

    pair_map = pd.DataFrame(
        CURRENCY_PAIR_MAP, columns=["pair_code", "currency", "return_sign"]
    )
    merged = components.merge(pair_map, on="currency", how="inner")
    merged = merged.merge(
        fx_returns,
        left_on=["date", "pair_code"],
        right_on=["price_date", "pair_code"],
        how="inner",
    )
    # Currency-direction-adjusted returns: positive means currency strengthened
    for h in HORIZONS:
        merged[f"currency_ret_{h}d"] = merged[f"ret_{h}d"] * merged["return_sign"]
        merged[f"signal_correct_{h}d"] = (
            merged[score_col].apply(_sign) == merged[f"currency_ret_{h}d"].apply(_sign)
        )
    return merged


# ── Grid search ───────────────────────────────────────────────────────────────

def _grid_search(
    components: pd.DataFrame,
    fx_returns: pd.DataFrame,
    config: ValidationConfig,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Exhaustive grid search over (inflation_w, labor_w, growth_w) on training set."""
    train_mask = (
        (components["date"] >= config.train_start)
        & (components["date"] <= config.train_end)
    )
    train = components[train_mask].copy()

    pair_map = pd.DataFrame(
        CURRENCY_PAIR_MAP, columns=["pair_code", "currency", "return_sign"]
    )
    train_fx = fx_returns[
        (fx_returns["price_date"] >= config.train_start)
        & (fx_returns["price_date"] <= config.train_end)
    ]

    results = []
    combos = list(itertools.product(INFLATION_GRID, LABOR_GRID, GROWTH_GRID))
    total = len(combos)

    for i, (infl_w, labor_w, growth_w) in enumerate(combos):
        if i % 20 == 0:
            print(f"  Grid search {i}/{total}…")

        scored = _recompute_scores(train, infl_w, labor_w, growth_w)
        merged = scored.merge(pair_map, on="currency", how="inner")
        merged = merged.merge(
            train_fx,
            left_on=["date", "pair_code"],
            right_on=["price_date", "pair_code"],
            how="inner",
        )
        merged[f"currency_ret_{config.optimise_horizon}d"] = (
            merged[f"ret_{config.optimise_horizon}d"] * merged["return_sign"]
        )
        merged[f"correct"] = (
            merged["recomputed_score"].apply(_sign)
            == merged[f"currency_ret_{config.optimise_horizon}d"].apply(_sign)
        )
        valid = merged.dropna(
            subset=["recomputed_score", f"currency_ret_{config.optimise_horizon}d"]
        )
        if len(valid) < 30:
            continue

        acc = valid["correct"].mean()
        corr = valid["recomputed_score"].corr(
            valid[f"currency_ret_{config.optimise_horizon}d"]
        )
        results.append(
            {
                "inflation_weight": infl_w,
                "labor_weight":     labor_w,
                "growth_weight":    growth_w,
                "directional_accuracy": round(acc, 4),
                "correlation":          round(corr, 4) if not math.isnan(corr) else None,
                "n_samples":            len(valid),
            }
        )

    results.sort(key=lambda x: x["directional_accuracy"], reverse=True)

    # Best weights by mandate type
    mandate_map = (
        components[["currency", "mandate_type"]]
        .drop_duplicates()
        .set_index("currency")["mandate_type"]
        .to_dict()
    )
    best_by_mandate: dict[str, Any] = {}
    for mandate in ("dual", "inflation", "single"):
        currencies = [c for c, m in mandate_map.items() if m == mandate]
        if not currencies:
            continue
        mandate_train = train[train["currency"].isin(currencies)]
        best = _best_weights_for_subset(mandate_train, train_fx, pair_map, config)
        best_by_mandate[mandate] = best

    return results, best_by_mandate


def _best_weights_for_subset(
    subset: pd.DataFrame,
    fx: pd.DataFrame,
    pair_map: pd.DataFrame,
    config: ValidationConfig,
) -> dict[str, Any]:
    best_acc = -1.0
    best = {"inflation_weight": 1.5, "labor_weight": 1.0, "growth_weight": 0.5}
    for infl_w, labor_w, growth_w in itertools.product(
        INFLATION_GRID, LABOR_GRID, GROWTH_GRID
    ):
        scored = _recompute_scores(subset, infl_w, labor_w, growth_w)
        merged = scored.merge(pair_map, on="currency", how="inner")
        merged = merged.merge(
            fx,
            left_on=["date", "pair_code"],
            right_on=["price_date", "pair_code"],
            how="inner",
        )
        merged["currency_ret"] = (
            merged[f"ret_{config.optimise_horizon}d"] * merged["return_sign"]
        )
        merged["correct"] = (
            merged["recomputed_score"].apply(_sign)
            == merged["currency_ret"].apply(_sign)
        )
        valid = merged.dropna(subset=["recomputed_score", "currency_ret"])
        if len(valid) < 20:
            continue
        acc = valid["correct"].mean()
        if acc > best_acc:
            best_acc = acc
            best = {
                "inflation_weight":    infl_w,
                "labor_weight":        labor_w,
                "growth_weight":       growth_w,
                "train_directional_accuracy": round(acc, 4),
                "n_samples":           len(valid),
            }
    return best


def _recompute_scores(
    df: pd.DataFrame,
    infl_w: float,
    labor_w: float,
    growth_w: float,
) -> pd.DataFrame:
    """Recompute CB pressure and z-score with given weights (in-memory, no DB)."""
    out = df.copy()
    total_w = infl_w + labor_w + growth_w
    out["cb_pressure"] = (
        out["inflation_score"].fillna(0) * infl_w
        + out["labor_score"].fillna(0)   * labor_w
        + out["growth_score"].fillna(0)  * growth_w
    ) / total_w

    # Cross-currency z-score per date + window
    grp = out.groupby(["date", "window_months"])["cb_pressure"]
    out["recomputed_score"] = (out["cb_pressure"] - grp.transform("mean")) / grp.transform("std")
    return out


def _apply_optimal_weights(
    components: pd.DataFrame,
    best_by_mandate: dict[str, Any],
) -> pd.DataFrame:
    """Recompute scores using optimal mandate weights for each currency group."""
    frames = []
    for mandate, best in best_by_mandate.items():
        mask = components["mandate_type"] == mandate
        subset = components[mask].copy()
        if subset.empty:
            continue
        scored = _recompute_scores(
            subset,
            best["inflation_weight"],
            best["labor_weight"],
            best["growth_weight"],
        )
        frames.append(scored)
    if not frames:
        return components.copy()
    return pd.concat(frames, ignore_index=True)


def _build_optimal_weight_rows(
    best_by_mandate: dict[str, Any], components: pd.DataFrame
) -> list[dict[str, Any]]:
    mandate_currencies = (
        components[["currency", "country_code", "mandate_type"]]
        .drop_duplicates()
        .groupby("mandate_type")
        .apply(lambda g: g[["currency", "country_code"]].to_dict("records"))
        .to_dict()
    )
    rows = []
    for mandate, best in best_by_mandate.items():
        for cc in mandate_currencies.get(mandate, []):
            rows.append(
                {
                    "currency":         cc["currency"],
                    "country_code":     cc["country_code"],
                    "mandate_type":     mandate,
                    "inflation_weight": best.get("inflation_weight"),
                    "labor_weight":     best.get("labor_weight"),
                    "growth_weight":    best.get("growth_weight"),
                    "train_directional_accuracy": best.get("train_directional_accuracy"),
                }
            )
    return rows


# ── Metrics aggregation ───────────────────────────────────────────────────────

def _aggregate_metrics(
    samples: pd.DataFrame,
    config: ValidationConfig,
) -> dict[str, Any]:
    periods = {
        "train": (config.train_start, config.train_end),
        "val":   (config.val_start,   config.val_end),
        "test":  (config.test_start,  config.test_end),
    }
    score_col = (
        "cb_strength_score" if "cb_strength_score" in samples.columns
        else "recomputed_score"
    )
    out: dict[str, Any] = {}
    for period_name, (start, end) in periods.items():
        period_data = samples[
            (samples["date"] >= start) & (samples["date"] <= end)
        ]
        if period_data.empty:
            out[period_name] = {"note": "no data in this period"}
            continue

        period_metrics: list[dict[str, Any]] = []
        for window in config.windows_months:
            w_data = period_data[period_data["window_months"] == window]
            for h in HORIZONS:
                ret_col = f"currency_ret_{h}d"
                correct_col = f"signal_correct_{h}d"
                if ret_col not in w_data.columns:
                    continue
                valid = w_data.dropna(subset=[score_col, ret_col])
                if len(valid) < 10:
                    continue
                corr = valid[score_col].corr(valid[ret_col])
                acc  = valid[correct_col].mean()
                aligned_ret = valid[ret_col] * valid[score_col].apply(_sign)
                sharpe = (
                    aligned_ret.mean() / aligned_ret.std()
                    if aligned_ret.std() > 0 else None
                )
                period_metrics.append(
                    {
                        "window_months":       window,
                        "horizon_days":        h,
                        "n_samples":           len(valid),
                        "directional_accuracy": round(acc, 4),
                        "correlation":          round(corr, 4) if not _isnan(corr) else None,
                        "sharpe_like":          round(sharpe, 4) if sharpe and not _isnan(sharpe) else None,
                        "status": (
                            "promising" if acc >= 0.53 and (sharpe or 0) > 0
                            else "weak"
                        ),
                    }
                )
        out[period_name] = period_metrics
    return out


def _policy_baseline_metrics(
    policy_df: pd.DataFrame,
    fx_returns: pd.DataFrame,
    config: ValidationConfig,
) -> dict[str, Any]:
    pair_map = pd.DataFrame(
        CURRENCY_PAIR_MAP, columns=["pair_code", "currency", "return_sign"]
    )
    # policy_df has currency_code column mapped via currency
    merged = policy_df.merge(pair_map, on="currency", how="inner")
    merged = merged.merge(
        fx_returns,
        left_on=["date", "pair_code"],
        right_on=["price_date", "pair_code"],
        how="inner",
    )
    for h in HORIZONS:
        merged[f"currency_ret_{h}d"]   = merged[f"ret_{h}d"] * merged["return_sign"]
        merged[f"correct_{h}d"] = (
            merged["policy_score"].apply(_sign)
            == merged[f"currency_ret_{h}d"].apply(_sign)
        )

    periods = {
        "train": (config.train_start, config.train_end),
        "val":   (config.val_start,   config.val_end),
        "test":  (config.test_start,  config.test_end),
    }
    out: dict[str, Any] = {}
    for period_name, (start, end) in periods.items():
        p = merged[(merged["date"] >= start) & (merged["date"] <= end)]
        if p.empty:
            out[period_name] = {"note": "no data"}
            continue
        period_rows = []
        for h in HORIZONS:
            valid = p.dropna(subset=["policy_score", f"currency_ret_{h}d"])
            if len(valid) < 10:
                continue
            acc = valid[f"correct_{h}d"].mean()
            period_rows.append(
                {
                    "horizon_days":        h,
                    "n_samples":           len(valid),
                    "directional_accuracy": round(acc, 4),
                    "status": "promising" if acc >= 0.53 else "weak",
                }
            )
        out[period_name] = period_rows
    return out


def _pair_divergence_metrics(
    components: pd.DataFrame,
    fx_returns: pd.DataFrame,
    config: ValidationConfig,
) -> dict[str, Any]:
    score_col = "recomputed_score" if "recomputed_score" in components.columns else "cb_strength_score"
    out: list[dict[str, Any]] = []
    periods = {
        "train": (config.train_start, config.train_end),
        "val":   (config.val_start,   config.val_end),
        "test":  (config.test_start,  config.test_end),
    }
    for pair_code, base_ccy, quote_ccy in PAIR_DIVERGENCE:
        pair_fx = fx_returns[fx_returns["pair_code"] == pair_code]
        for window in config.windows_months:
            base_scores  = components[
                (components["currency"] == base_ccy)
                & (components["window_months"] == window)
            ][["date", score_col]].rename(columns={score_col: "base_score"})
            quote_scores = components[
                (components["currency"] == quote_ccy)
                & (components["window_months"] == window)
            ][["date", score_col]].rename(columns={score_col: "quote_score"})

            div = base_scores.merge(quote_scores, on="date", how="inner")
            div["divergence"] = div["base_score"] - div["quote_score"]
            div = div.merge(pair_fx, left_on="date", right_on="price_date", how="inner")

            for period_name, (start, end) in periods.items():
                p = div[(div["date"] >= start) & (div["date"] <= end)]
                for h in HORIZONS:
                    ret_col = f"ret_{h}d"
                    valid = p.dropna(subset=["divergence", ret_col])
                    if len(valid) < 10:
                        continue
                    correct = (
                        valid["divergence"].apply(_sign) == valid[ret_col].apply(_sign)
                    )
                    acc = correct.mean()
                    corr = valid["divergence"].corr(valid[ret_col])
                    out.append(
                        {
                            "pair_code":           pair_code,
                            "base_currency":       base_ccy,
                            "quote_currency":      quote_ccy,
                            "window_months":       window,
                            "horizon_days":        h,
                            "period":              period_name,
                            "n_samples":           len(valid),
                            "directional_accuracy": round(acc, 4),
                            "correlation":          round(corr, 4) if not _isnan(corr) else None,
                            "status": "promising" if acc >= 0.53 else "weak",
                        }
                    )
    return out


# ── I/O helpers ───────────────────────────────────────────────────────────────

def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, default=json_default, indent=2),
        encoding="utf-8",
    )


def _write_metrics_csv(path: Path, report: dict[str, Any]) -> None:
    rows = []
    for metric_set in ("default_metrics", "optimal_metrics"):
        for period_name, period_rows in report.get(metric_set, {}).items():
            if not isinstance(period_rows, list):
                continue
            for row in period_rows:
                rows.append({"metric_set": metric_set, "period": period_name, **row})
    if not rows:
        return
    _write_csv(path, rows)


def _write_optimal_weights_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if rows:
        _write_csv(path, rows)


def _write_grid_csv(path: Path, results: list[dict[str, Any]]) -> None:
    if results:
        _write_csv(path, results)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


async def _fetch_all(
    session: AsyncSession,
    stmt: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    result = await session.execute(text(stmt), params or {})
    return [dict(row) for row in result.mappings().all()]


def _sign(x: float | None) -> int:
    if x is None or _isnan(x) or x == 0:
        return 0
    return 1 if x > 0 else -1


def _isnan(x: Any) -> bool:
    try:
        return math.isnan(x)
    except (TypeError, ValueError):
        return False
