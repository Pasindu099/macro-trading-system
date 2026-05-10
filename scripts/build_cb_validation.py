"""Validate CB strength scores against forward FX returns.

Run after:
  1. build_fx_validation_layer.py  (requires processed.fx_price_history)
  2. build_cb_strength.py          (requires processed.cb_strength_score)

The script runs a train / validation / test split and a grid search over
mandate weights, then compares to the policy_signals baseline.

Usage:
    # Default split (train ≤2022, val 2023, test 2024+)
    python -m scripts.build_cb_validation

    # Custom split
    python -m scripts.build_cb_validation \\
        --train-start 2010-01-01 --train-end 2021-12-31 \\
        --val-start   2022-01-01 --val-end   2022-12-31 \\
        --test-start  2023-01-01

    # Skip grid search (faster — just evaluate default weights)
    python -m scripts.build_cb_validation --no-grid-search
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path

from app.db.session import dispose_engine
from app.processing.cb_strength_validation import (
    ValidationConfig,
    build_cb_strength_validation,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate CB strength scores against forward FX returns."
    )
    parser.add_argument("--output-dir", default="data/cb_strength")
    parser.add_argument("--train-start", default="2010-01-01")
    parser.add_argument("--train-end",   default="2022-12-31")
    parser.add_argument("--val-start",   default="2023-01-01")
    parser.add_argument("--val-end",     default="2023-12-31")
    parser.add_argument("--test-start",  default="2024-01-01")
    parser.add_argument("--test-end",    default="2099-12-31")
    parser.add_argument(
        "--optimise-horizon",
        type=int,
        default=21,
        help="Forward return horizon (days) used to pick best weights (default: 21).",
    )
    parser.add_argument(
        "--windows",
        nargs="+",
        type=int,
        default=[1, 2, 3],
        metavar="N",
    )
    return parser.parse_args()


def _d(s: str) -> date:
    return date.fromisoformat(s)


async def main() -> None:
    args = parse_args()
    config = ValidationConfig(
        train_start=_d(args.train_start),
        train_end=  _d(args.train_end),
        val_start=  _d(args.val_start),
        val_end=    _d(args.val_end),
        test_start= _d(args.test_start),
        test_end=   _d(args.test_end),
        windows_months=tuple(args.windows),
        optimise_horizon=args.optimise_horizon,
    )

    print("CB Strength Validation")
    print(f"  Train : {config.train_start} → {config.train_end}")
    print(f"  Val   : {config.val_start} → {config.val_end}")
    print(f"  Test  : {config.test_start} → {config.test_end}")
    print(f"  Optimise on {config.optimise_horizon}-day horizon\n")

    report = await build_cb_strength_validation(Path(args.output_dir), config)

    if "error" in report:
        print(f"ERROR: {report['error']}")
        await dispose_engine()
        return

    # ── Print optimal weights ──────────────────────────────────────────────────
    print("\nOptimal weights by mandate type (from grid search on training set):")
    for mandate, best in report.get("optimal_weights_by_mandate", {}).items():
        print(
            f"  {mandate:10s}  "
            f"inflation={best.get('inflation_weight')}  "
            f"labor={best.get('labor_weight')}  "
            f"growth={best.get('growth_weight')}  "
            f"train_acc={best.get('train_directional_accuracy', 'n/a')}"
        )

    # ── Print metrics summary ──────────────────────────────────────────────────
    for metric_set in ("default_metrics", "optimal_metrics"):
        label = "Default weights" if metric_set == "default_metrics" else "Optimal weights"
        print(f"\n{label}:")
        for period in ("train", "val", "test"):
            rows = report.get(metric_set, {}).get(period, [])
            if not isinstance(rows, list) or not rows:
                print(f"  {period:5s}: no data")
                continue
            best_row = max(rows, key=lambda r: r.get("directional_accuracy", 0))
            print(
                f"  {period:5s}: best acc={best_row['directional_accuracy']:.1%}  "
                f"(w={best_row['window_months']}m  h={best_row['horizon_days']}d  "
                f"n={best_row['n_samples']}  status={best_row['status']})"
            )

    # ── Policy baseline ────────────────────────────────────────────────────────
    baseline = report.get("policy_baseline", {})
    if baseline:
        print("\nPolicy signals baseline:")
        for period in ("train", "val", "test"):
            rows = baseline.get(period, [])
            if not isinstance(rows, list) or not rows:
                continue
            best = max(rows, key=lambda r: r.get("directional_accuracy", 0))
            print(
                f"  {period:5s}: best acc={best['directional_accuracy']:.1%}  "
                f"(h={best['horizon_days']}d  n={best['n_samples']})"
            )

    # ── Pair divergence highlights ─────────────────────────────────────────────
    div_rows = report.get("pair_divergence", [])
    promising = [r for r in div_rows if r.get("status") == "promising"]
    if promising:
        print(f"\nPromising pair-divergence signals ({len(promising)}):")
        for r in sorted(promising, key=lambda x: x["directional_accuracy"], reverse=True)[:10]:
            print(
                f"  {r['pair_code']:7s}  {r['period']:5s}  "
                f"w={r['window_months']}m  h={r['horizon_days']}d  "
                f"acc={r['directional_accuracy']:.1%}  n={r['n_samples']}"
            )

    print(f"\nFull report → {args.output_dir}/validation/cb_validation_report.json")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
