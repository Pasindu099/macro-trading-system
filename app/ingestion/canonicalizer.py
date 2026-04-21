"""Canonicalize raw EODHD events into our domain vocabulary.

Reads config/indicator_mapping.yaml at startup and builds fast lookup tables.
Every incoming EODHD event goes through canonicalize() which returns a
CanonicalEvent with either a matched canonical_name or None for unmapped.

Key responsibilities:
    1. Match (eodhd_type, comparison, country) → canonical_name
    2. Normalize malformed period strings to period_start_date
    3. Filter events from countries outside our tracked-country allowlist
    4. Parse EODHD's date string into a timezone-aware datetime
    5. Log unmapped events to a dedicated logger for review

Usage:
    from app.ingestion.canonicalizer import Canonicalizer

    canonicalizer = Canonicalizer.from_default_config()
    for raw_event in raw_events:
        canonical = canonicalizer.canonicalize(raw_event)
        if canonical is None:
            continue  # country outside allowlist; silently skipped
        # canonical.canonical_name may still be None if type is unmapped
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from app.ingestion.eodhd_client import ALLOWED_COUNTRIES

logger = logging.getLogger(__name__)
unmapped_logger = logging.getLogger("app.canonicalizer.unmapped")

DEFAULT_MAPPING_PATH = Path("config/indicator_mapping.yaml")

# EODHD's date format. Example: "2026-04-10 12:30:00"
EODHD_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Month name → month number
_MONTH_ABBREV = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# Regex patterns for EODHD period formats.
# Order matters in _parse_period — check more specific patterns first.
_PATTERN_ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")    # "2026-04-15"
_PATTERN_MONTH = re.compile(r"^([A-Z][a-z]{2})$")                    # "Mar"
_PATTERN_QUARTER = re.compile(r"^Q([1-4])$")                         # "Q4"
_PATTERN_MONTH_SLASH_DAY = re.compile(r"^([A-Z][a-z]{2})/(\d{1,2})$")  # "Apr/15"
# Note: MONTH_YEAR only accepts space or dash separator (NOT slash), so it
# doesn't wrongly match "Apr/15" as "Apr + year 2015".
_PATTERN_MONTH_YEAR = re.compile(r"^([A-Z][a-z]{2})[ -](\d{2,4})$")  # "Mar 2026"
_PATTERN_QUARTER_YEAR = re.compile(r"^Q([1-4])[ /-](\d{2,4})$")      # "Q4 2026"


@dataclass(frozen=True)
class CanonicalMapping:
    """One entry from indicator_mapping.yaml, parsed and validated."""

    eodhd_type: str
    eodhd_comparison: str | None
    country: str
    canonical_name: str
    display_name: str
    primary_category: str
    secondary_categories: tuple[str, ...]
    frequency: str
    unit: str | None
    is_higher_better_for_currency: bool
    importance: int
    notes: str | None


@dataclass(frozen=True)
class CanonicalEvent:
    """A canonicalized event ready for storage.

    When canonical_name is None, the event's raw type isn't in our mapping
    yet — it's logged and can still be stored (with indicator_id=NULL) so
    we don't lose the data. A future mapping addition + reprocessing pass
    will retroactively categorize it.
    """

    # Canonical fields — None if unmapped
    canonical_name: str | None
    display_name: str | None
    primary_category: str | None
    secondary_categories: tuple[str, ...]
    importance: int
    is_higher_better_for_currency: bool

    # Parsed fields
    country: str
    released_at: datetime            # timezone-aware
    period_raw: str | None
    period_start_date: date | None   # None if unparseable
    actual: Decimal | None
    previous: Decimal | None
    estimate: Decimal | None
    change: Decimal | None
    change_percentage: Decimal | None

    # Original fields for audit
    raw_payload: dict[str, Any]

    @property
    def is_mapped(self) -> bool:
        return self.canonical_name is not None


class CanonicalizationError(Exception):
    """Raised when a mapping file is structurally invalid (not at match time)."""


class Canonicalizer:
    """Reads YAML mapping rules and canonicalizes events.

    Thread-safe after construction (all state is immutable).
    """

    def __init__(self, mappings: list[CanonicalMapping]) -> None:
        self._mappings = mappings
        # Primary lookup: (country, eodhd_type, eodhd_comparison) → CanonicalMapping
        # If multiple YAML entries match the same key, the FIRST one wins
        # (precedence by YAML order). Later ones are ignored, with a warning.
        self._lookup: dict[tuple[str, str, str | None], CanonicalMapping] = {}
        for m in mappings:
            key = (m.country, m.eodhd_type, m.eodhd_comparison)
            if key in self._lookup:
                logger.warning(
                    "Duplicate mapping key %s. First entry wins (%s); "
                    "subsequent entry %s ignored.",
                    key, self._lookup[key].canonical_name, m.canonical_name,
                )
                continue
            self._lookup[key] = m
        logger.info(
            "Canonicalizer ready: %d mappings across %d lookup keys",
            len(mappings), len(self._lookup),
        )

    @classmethod
    def from_default_config(cls) -> Canonicalizer:
        return cls.from_yaml_path(DEFAULT_MAPPING_PATH)

    @classmethod
    def from_yaml_path(cls, path: Path) -> Canonicalizer:
        if not path.exists():
            raise FileNotFoundError(f"Mapping file not found: {path}")
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls.from_yaml_data(data)

    @classmethod
    def from_yaml_data(cls, data: dict[str, Any]) -> Canonicalizer:
        raw_mappings = data.get("mappings")
        if not isinstance(raw_mappings, list):
            raise CanonicalizationError(
                "Expected top-level 'mappings:' list in YAML"
            )

        mappings: list[CanonicalMapping] = []
        for idx, entry in enumerate(raw_mappings):
            try:
                mappings.append(_parse_mapping_entry(entry))
            except Exception as exc:
                raise CanonicalizationError(
                    f"Invalid mapping entry at index {idx}: {entry!r}"
                ) from exc
        return cls(mappings)

    def canonicalize(self, raw_event: dict[str, Any]) -> CanonicalEvent | None:
        """Canonicalize one raw EODHD event.

        Returns:
            CanonicalEvent if event's country is in the allowlist.
            None if event is from a country we don't track (silently dropped).

        Notes:
            If the (type, comparison, country) isn't in our mapping, the
            returned event has canonical_name=None and is logged as unmapped.
            The event is still returned so the caller can store it with
            indicator_id=NULL (we don't want to lose data).
        """
        country = raw_event.get("country")
        if country not in ALLOWED_COUNTRIES:
            # Silently skip — this is the defensive allowlist filter.
            return None

        eodhd_type = raw_event.get("type")
        eodhd_comparison = raw_event.get("comparison")
        if not eodhd_type:
            logger.warning("Event missing 'type' field: %s", raw_event)
            return None

        lookup_key = (country, eodhd_type, eodhd_comparison)
        mapping = self._lookup.get(lookup_key)

        if mapping is None:
            # Log unmapped event so admin page can surface it
            unmapped_logger.info(
                "Unmapped event: country=%s type=%r comparison=%r",
                country, eodhd_type, eodhd_comparison,
            )
            return _build_event(
                mapping=None,
                country=country,
                raw_event=raw_event,
            )

        return _build_event(mapping=mapping, country=country, raw_event=raw_event)

    @property
    def mapping_count(self) -> int:
        return len(self._mappings)


# ══════════════════════════════════════════════════════════════════════════
#  Module-level helpers (pure functions, easy to test)
# ══════════════════════════════════════════════════════════════════════════

def _parse_mapping_entry(entry: dict[str, Any]) -> CanonicalMapping:
    """Validate and convert one YAML mapping entry into a CanonicalMapping."""
    required = ["eodhd_type", "country", "canonical_name", "display_name",
                "primary_category", "frequency"]
    missing = [k for k in required if k not in entry]
    if missing:
        raise ValueError(f"Missing required fields: {missing}")

    country = entry["country"]
    if country not in ALLOWED_COUNTRIES:
        raise ValueError(
            f"country={country!r} not in allowlist {sorted(ALLOWED_COUNTRIES)}"
        )

    secondary = tuple(entry.get("secondary_categories") or [])

    return CanonicalMapping(
        eodhd_type=entry["eodhd_type"],
        eodhd_comparison=entry.get("eodhd_comparison"),
        country=country,
        canonical_name=entry["canonical_name"],
        display_name=entry["display_name"],
        primary_category=entry["primary_category"],
        secondary_categories=secondary,
        frequency=entry["frequency"],
        unit=entry.get("unit"),
        is_higher_better_for_currency=bool(
            entry.get("is_higher_better_for_currency", True)
        ),
        importance=int(entry.get("importance", 2)),
        notes=entry.get("notes"),
    )


def _build_event(
    mapping: CanonicalMapping | None,
    country: str,
    raw_event: dict[str, Any],
) -> CanonicalEvent:
    """Convert a raw event + optional mapping into a CanonicalEvent."""
    released_at = _parse_released_at(raw_event.get("date"))
    period_raw = raw_event.get("period")
    period_start_date = _parse_period(period_raw, released_at)

    return CanonicalEvent(
        canonical_name=mapping.canonical_name if mapping else None,
        display_name=mapping.display_name if mapping else None,
        primary_category=mapping.primary_category if mapping else None,
        secondary_categories=mapping.secondary_categories if mapping else (),
        importance=mapping.importance if mapping else 2,
        is_higher_better_for_currency=(
            mapping.is_higher_better_for_currency if mapping else True
        ),
        country=country,
        released_at=released_at,
        period_raw=period_raw,
        period_start_date=period_start_date,
        actual=_to_decimal(raw_event.get("actual")),
        previous=_to_decimal(raw_event.get("previous")),
        estimate=_to_decimal(raw_event.get("estimate")),
        change=_to_decimal(raw_event.get("change")),
        change_percentage=_to_decimal(raw_event.get("change_percentage")),
        raw_payload=raw_event,
    )


def _parse_released_at(raw: Any) -> datetime:
    """Parse EODHD's 'date' field into a UTC datetime."""
    if not raw:
        # Fall back to 'now' if EODHD omits date. Shouldn't happen.
        logger.warning("Event missing 'date' field, defaulting to now()")
        return datetime.now(timezone.utc)
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.strptime(str(raw), EODHD_DATETIME_FORMAT)
        # EODHD dates appear to be in UTC but aren't explicitly tagged.
        return parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        logger.warning("Unparseable date %r, defaulting to now()", raw)
        return datetime.now(timezone.utc)


def _parse_period(period: str | None, released_at: datetime) -> date | None:
    """Normalize EODHD's period string to a proper date.

    EODHD uses many period formats:
        "Mar"           → month abbreviation, year inferred from released_at
        "Mar 2026"      → month + explicit year (space or dash separator)
        "Q4"            → quarter, year inferred
        "Q4 2026"       → quarter + year
        "Apr/15"        → month abbrev + day, year inferred
        "2026-04-15"    → ISO date
        "3Mo/Yr) (Jan"  → malformed; we try to extract "Jan"

    Order of pattern checks matters — more specific patterns first.

    Returns None if we can't parse.
    """
    if not period:
        return None
    period = period.strip()
    if not period:
        return None

    year_hint = released_at.year

    # 1. Try ISO date "2026-04-15"
    m = _PATTERN_ISO_DATE.match(period)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            pass

    # 2. Try "Apr/15" (month abbrev + day) — BEFORE MONTH_YEAR so slash
    # separator is interpreted as day, not year.
    m = _PATTERN_MONTH_SLASH_DAY.match(period)
    if m:
        month = _MONTH_ABBREV.get(m[1])
        if month:
            day = int(m[2])
            try:
                return date(year_hint, month, day)
            except ValueError:
                pass

    # 3. Try "Mar" (month alone)
    m = _PATTERN_MONTH.match(period)
    if m:
        month = _MONTH_ABBREV.get(m[1])
        if month:
            # If released_at is Jan and period is Dec, assume prior year.
            # Simple heuristic: if period month > released_at month + 1,
            # roll back a year.
            year = year_hint
            if month > released_at.month + 1:
                year -= 1
            return date(year, month, 1)

    # 4. Try "Q4" (quarter alone)
    m = _PATTERN_QUARTER.match(period)
    if m:
        quarter = int(m[1])
        month = (quarter - 1) * 3 + 1
        year = year_hint
        if month > released_at.month + 1:
            year -= 1
        return date(year, month, 1)

    # 5. Try "Mar 2026" or "Mar-2026" (space/dash separator, NOT slash)
    m = _PATTERN_MONTH_YEAR.match(period)
    if m:
        month = _MONTH_ABBREV.get(m[1])
        if month:
            year_str = m[2]
            year = int(year_str) if len(year_str) == 4 else 2000 + int(year_str)
            return date(year, month, 1)

    # 6. Try "Q4 2026"
    m = _PATTERN_QUARTER_YEAR.match(period)
    if m:
        quarter = int(m[1])
        year_str = m[2]
        year = int(year_str) if len(year_str) == 4 else 2000 + int(year_str)
        month = (quarter - 1) * 3 + 1
        return date(year, month, 1)

    # 7. Fallback: search for any month abbreviation inside the string.
    # Handles malformed cases like "3Mo/Yr) (Jan".
    for abbrev, num in _MONTH_ABBREV.items():
        if abbrev in period:
            logger.debug(
                "Period %r parsed via fallback to month=%d", period, num,
            )
            return date(year_hint, num, 1)

    logger.debug("Could not parse period %r", period)
    return None


def _to_decimal(value: Any) -> Decimal | None:
    """Safe Decimal conversion. Returns None for null/invalid inputs."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        logger.warning("Could not convert %r to Decimal", value)
        return None
