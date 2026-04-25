"""Build production-candidate currency strength signals and validation docs."""

from __future__ import annotations

from app.processing.production_currency_strength import build_production_currency_strength


def main() -> None:
    result = build_production_currency_strength()
    print("Production currency strength package built.")
    print(f"Weights: {result['weights']}")
    print(f"Signals: {result['signals']}")
    print(f"Validation rows: {result['validation_rows']}")
    print(f"Comparison rows: {result['comparison_rows']}")
    print(f"Output directory: {result['output_dir']}")
    print("Files:")
    for file_name in result["files"]:
        print(f"  - {file_name}")


if __name__ == "__main__":
    main()
