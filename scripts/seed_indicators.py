"""Seed the `indicators` table from config/indicator_mapping.yaml.

Idempotent: safe to run multiple times. The mapping file can contain aliases
where multiple EODHD event types map to the same canonical indicator; this
script keeps one row per (canonical_name, country_code), using the first mapping
entry as the source of display metadata.

Usage:
    python scripts/seed_indicators.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import asdict

from sqlalchemy import select

from app.db import Country, Indicator, session_scope
from app.ingestion.canonicalizer import Canonicalizer, CanonicalMapping

logger = logging.getLogger(__name__)


def unique_indicator_mappings() -> list[CanonicalMapping]:
    """Return first-seen mapping metadata for each canonical indicator."""
    canonicalizer = Canonicalizer.from_default_config()
    seen: set[tuple[str, str]] = set()
    mappings: list[CanonicalMapping] = []

    for mapping in canonicalizer._mappings:
        key = (mapping.country, mapping.canonical_name)
        if key in seen:
            continue
        seen.add(key)
        mappings.append(mapping)

    return mappings


def mapping_values(mapping: CanonicalMapping) -> dict[str, object]:
    return {
        "display_name": mapping.display_name,
        "primary_category": mapping.primary_category,
        "secondary_categories": list(mapping.secondary_categories),
        "comparison": mapping.eodhd_comparison,
        "frequency": mapping.frequency,
        "unit": mapping.unit,
        "is_higher_better_for_currency": mapping.is_higher_better_for_currency,
        "importance": mapping.importance,
        "notes": mapping.notes,
    }


async def seed() -> tuple[int, int]:
    """Seed indicators. Returns (inserted, updated)."""
    mappings = unique_indicator_mappings()
    logger.info("Loaded %d unique indicators from mapping YAML", len(mappings))

    inserted = 0
    updated = 0

    async with session_scope() as session:
        countries_q = await session.execute(select(Country.code))
        existing_countries = set(countries_q.scalars().all())

        for mapping in mappings:
            if mapping.country not in existing_countries:
                logger.warning(
                    "Skipping %s/%s because country is missing",
                    mapping.country,
                    mapping.canonical_name,
                )
                continue

            indicator_q = await session.execute(
                select(Indicator).where(
                    Indicator.country_code == mapping.country,
                    Indicator.canonical_name == mapping.canonical_name,
                )
            )
            indicator = indicator_q.scalar_one_or_none()
            values = mapping_values(mapping)

            if indicator is None:
                session.add(
                    Indicator(
                        canonical_name=mapping.canonical_name,
                        country_code=mapping.country,
                        **values,
                    )
                )
                inserted += 1
                continue

            changed = False
            for field, value in values.items():
                if getattr(indicator, field) != value:
                    setattr(indicator, field, value)
                    changed = True

            if changed:
                logger.debug(
                    "Updated %s/%s from %s",
                    mapping.country,
                    mapping.canonical_name,
                    asdict(mapping),
                )
                updated += 1

    logger.info("Seed complete. inserted=%d updated=%d", inserted, updated)
    return inserted, updated


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
