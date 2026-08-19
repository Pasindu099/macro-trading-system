"""Walk-forward backtest for the US ISM Manufacturing PMI forecast model."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.db.session import dispose_engine
from app.processing.pmi_forecast_backtest import build_pmi_forecast_backtest


async def main() -> None:
    result = await build_pmi_forecast_backtest(Path("data/indicator_correlations"))
    print(f"Predictions: {result['n_predictions']}")
    print(f"Model         MAE: {result['model_mae']:.3f}  RMSE: {result['model_rmse']:.3f}")
    print(
        f"Naive(last)   MAE: {result['naive_last_value_mae']:.3f}  "
        f"RMSE: {result['naive_last_value_rmse']:.3f}"
    )
    print(
        f"Naive(avg3)   MAE: {result['naive_trailing_avg3_mae']:.3f}  "
        f"RMSE: {result['naive_trailing_avg3_rmse']:.3f}"
    )
    print(f"CSV: {result['csv_path']}")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
