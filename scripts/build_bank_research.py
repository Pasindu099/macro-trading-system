"""Build the bank research cache from a Google Drive folder."""

from __future__ import annotations

import argparse
import asyncio

from app.processing.bank_research import build_bank_research_cache, config_from_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and summarize bank research reports from Google Drive.",
    )
    parser.add_argument(
        "--folder-url",
        help="Google Drive folder URL or folder ID. Defaults to BANK_RESEARCH_DRIVE_FOLDER_URL.",
    )
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    config = config_from_settings(args.folder_url)
    result = await build_bank_research_cache(config)
    print("Bank research cache built.")
    print(f"Folder: {result.get('folder_url')}")
    print(f"Reports: {len(result.get('reports', []))}")
    if result.get("errors"):
        print("Warnings:")
        for error in result["errors"]:
            print(f"  - {error}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
