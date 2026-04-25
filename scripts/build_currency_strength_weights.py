"""Build initial indicator weights for the currency strength model."""

from __future__ import annotations

from app.processing.currency_strength_weights import build_currency_strength_weights


def main() -> None:
    result = build_currency_strength_weights()
    print("Currency strength weights built.")
    print(f"Targets: {result['targets']}")
    print(f"Correlation rows: {result['correlation_rows']}")
    print(f"Weight rows: {result['weight_rows']}")
    print(f"Key driver rows: {result['key_driver_rows']}")
    print(f"Output directory: {result['output_dir']}")
    print("Files:")
    for file_name in result["files"]:
        print(f"  - {file_name}")


if __name__ == "__main__":
    main()
