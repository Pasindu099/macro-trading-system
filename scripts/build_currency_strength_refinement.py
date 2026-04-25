"""Refine currency strength weights using preliminary FX-return validation."""

from __future__ import annotations

from app.processing.currency_strength_refinement import build_currency_strength_refinement


def main() -> None:
    result = build_currency_strength_refinement()
    print("Currency strength refinement built.")
    print(f"Initial weight rows: {result['initial_weight_rows']}")
    print(f"Refined weight rows: {result['refined_weight_rows']}")
    print(f"Recommended subset rows: {result['subset_rows']}")
    print(f"Validation rows: {result['validation_rows']}")
    print(f"Sensitivity rows: {result['sensitivity_rows']}")
    print(f"Output directory: {result['output_dir']}")
    print("Files:")
    for file_name in result["files"]:
        print(f"  - {file_name}")


if __name__ == "__main__":
    main()
