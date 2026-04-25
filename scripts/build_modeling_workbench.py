"""Build feature-selection, modeling, and scenario-analysis artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.processing.modeling_workbench import build_modeling_workbench


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create macro forecasting modeling workbench outputs from EDA observations."
    )
    parser.add_argument("--data-path", default="data/eda/eda_observations.csv")
    parser.add_argument("--analysis-dir", default="data/eda/analysis")
    parser.add_argument("--output-dir", default="data/modeling")
    parser.add_argument(
        "--horizon",
        type=int,
        default=1,
        help="Forecast horizon in monthly periods after resampling. Defaults to 1.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = build_modeling_workbench(
        data_path=Path(args.data_path),
        analysis_dir=Path(args.analysis_dir),
        output_dir=Path(args.output_dir),
        horizon=args.horizon,
    )
    print("Modeling workbench built.")
    print(f"Targets found: {result['targets']}")
    print(f"Modeled targets: {result['modeled_targets']}")
    print(f"Selected feature rows: {result['feature_rows']}")
    print(f"Modeling matrix rows: {result['matrix_rows']}")
    print(f"Score rows: {result['score_rows']}")
    print(f"Output directory: {result['output_dir']}")
    print("Files:")
    for file_name in result["files"]:
        print(f"  - {file_name}")


if __name__ == "__main__":
    main()
