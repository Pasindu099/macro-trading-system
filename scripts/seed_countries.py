"""Seed the `countries` table from config/countries.yaml.

Idempotent: safe to run multiple times. Updates existing rows if the YAML
changes, inserts new rows if countries are added.

Usage:
    python scripts/seed_countries.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from app.db import Country, session_scope

logger = logging.getLogger(__name__)

COUNTRIES_YAML_PATH = Path("config/countries.yaml")


def load_countries_yaml() -> list[dict[str, Any]]:
    """Load and validate the countries YAML."""
    if not COUNTRIES_YAML_PATH.exists():
        raise FileNotFoundError(
            f"Expected {COUNTRIES_YAML_PATH} to exist. "
            "Run this script from the project root."
        )

    with COUNTRIES_YAML_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    countries = data.get("countries")
    if not isinstance(countries, list):
        raise ValueError(
            "countries.yaml must have a top-level 'countries:' list"
        )

    required_fields = {
        "code", "currency_code", "name", "central_bank",
        "cb_mandate_type", "timezone",
    }
    for entry in countries:
        missing = required_fields - entry.keys()
        if missing:
            raise ValueError(
                f"Country {entry.get('code', '?')!r} missing fields: {missing}"
            )

    return countries


async def seed() -> int:
    """Seed countries. Returns number of rows inserted or updated."""
    countries = load_countries_yaml()
    logger.info("Loaded %d countries from YAML", len(countries))

    changes = 0
    async with session_scope() as session:
        for entry in countries:
            code = entry["code"]

            # Check if it exists
            result = await session.execute(
                select(Country).where(Country.code == code)
            )
            existing = result.scalar_one_or_none()

            target_values = {
                "currency_code": entry["currency_code"],
                "name": entry["name"],
                "central_bank": entry["central_bank"],
                "cb_inflation_target": (
                    Decimal(str(entry["cb_inflation_target"]))
                    if entry.get("cb_inflation_target") is not None
                    else None
                ),
                "cb_mandate_type": entry["cb_mandate_type"],
                "timezone": entry["timezone"],
            }

            if existing is None:
                new_country = Country(code=code, **target_values)
                session.add(new_country)
                logger.info("Inserting country %s", code)
                changes += 1
            else:
                # Update if anything differs
                updated = False
                for field, new_val in target_values.items():
                    if getattr(existing, field) != new_val:
                        setattr(existing, field, new_val)
                        updated = True
                if updated:
                    logger.info("Updating country %s", code)
                    changes += 1
                else:
                    logger.debug("Country %s unchanged", code)

    logger.info("Seed complete. %d insert/update(s).", changes)
    return changes


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        asyncio.run(seed())
    except Exception as exc:
        logger.exception("Seed failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())