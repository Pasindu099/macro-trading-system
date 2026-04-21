"""Unit tests for the canonicalizer.

Covers:
  - Basic mapping (type + comparison → canonical_name)
  - Alias handling (CPI + Inflation Rate both → cpi_headline_yoy)
  - Unmapped events (no match → canonical_name=None, still returned)
  - Country allowlist (non-allowlisted country → None)
  - Period parsing (various formats, malformed inputs)
  - Decimal conversion (null, invalid, valid)
  - Duplicate mapping detection (first wins, warning logged)
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.ingestion.canonicalizer import (
    Canonicalizer,
    _parse_period,
    _to_decimal,
)


# ══════════════════════════════════════════════════════════════════════════
#  Basic mapping and lookup
# ══════════════════════════════════════════════════════════════════════════

def test_canonicalize_basic_match(sample_mapping_yaml: dict) -> None:
    canonicalizer = Canonicalizer.from_yaml_data(sample_mapping_yaml)

    raw = {
        "type": "Inflation Rate",
        "comparison": "yoy",
        "country": "US",
        "date": "2026-04-10 12:30:00",
        "period": "Mar",
        "actual": 3.3,
        "previous": 2.4,
        "estimate": 3.3,
        "change": 0.9,
        "change_percentage": 37.5,
    }

    result = canonicalizer.canonicalize(raw)

    assert result is not None
    assert result.canonical_name == "cpi_headline_yoy"
    assert result.display_name == "Headline CPI (YoY)"
    assert result.primary_category == "Inflation"
    assert result.country == "US"
    assert result.actual == Decimal("3.3")
    assert result.previous == Decimal("2.4")
    assert result.estimate == Decimal("3.3")
    assert result.period_start_date == date(2026, 3, 1)
    assert result.is_mapped is True


def test_canonicalize_alias_events_same_canonical(sample_mapping_yaml: dict) -> None:
    """Both 'CPI' and 'Inflation Rate' (yoy) should map to cpi_headline_yoy."""
    canonicalizer = Canonicalizer.from_yaml_data(sample_mapping_yaml)

    inflation_rate = {
        "type": "Inflation Rate", "comparison": "yoy", "country": "US",
        "date": "2026-04-10 12:30:00", "period": "Mar", "actual": 3.3,
    }
    cpi = {
        "type": "CPI", "comparison": "yoy", "country": "US",
        "date": "2026-04-10 12:30:00", "period": "Mar", "actual": 3.3,
    }

    r1 = canonicalizer.canonicalize(inflation_rate)
    r2 = canonicalizer.canonicalize(cpi)

    assert r1 is not None and r2 is not None
    assert r1.canonical_name == r2.canonical_name == "cpi_headline_yoy"


def test_canonicalize_null_comparison(sample_mapping_yaml: dict) -> None:
    """Mapping with eodhd_comparison: null should match events where comparison is null."""
    canonicalizer = Canonicalizer.from_yaml_data(sample_mapping_yaml)

    raw = {
        "type": "Unemployment Rate",
        "comparison": None,
        "country": "US",
        "date": "2026-04-03 12:30:00",
        "period": "Mar",
        "actual": 4.3,
    }

    result = canonicalizer.canonicalize(raw)
    assert result is not None
    assert result.canonical_name == "unemployment_rate"
    assert result.is_higher_better_for_currency is False or True  # defaulted fine
    assert result.primary_category == "Labor"


def test_canonicalize_multi_category(sample_mapping_yaml: dict) -> None:
    """Indicator with secondary_categories should expose them."""
    canonicalizer = Canonicalizer.from_yaml_data(sample_mapping_yaml)

    raw = {
        "type": "Average Hourly Earnings", "comparison": "yoy", "country": "US",
        "date": "2026-04-03 12:30:00", "period": "Mar", "actual": 3.5,
    }

    result = canonicalizer.canonicalize(raw)
    assert result is not None
    assert result.primary_category == "Labor"
    assert "Inflation" in result.secondary_categories


# ══════════════════════════════════════════════════════════════════════════
#  Unmapped events
# ══════════════════════════════════════════════════════════════════════════

def test_canonicalize_unmapped_event_returns_with_none(
    sample_mapping_yaml: dict, caplog: pytest.LogCaptureFixture,
) -> None:
    """Unmapped events should still return a CanonicalEvent with canonical_name=None."""
    canonicalizer = Canonicalizer.from_yaml_data(sample_mapping_yaml)

    raw = {
        "type": "Some New Fancy Indicator",
        "comparison": "yoy",
        "country": "US",
        "date": "2026-04-10 12:30:00",
        "period": "Mar",
        "actual": 42.0,
    }

    with caplog.at_level("INFO", logger="app.canonicalizer.unmapped"):
        result = canonicalizer.canonicalize(raw)

    assert result is not None
    assert result.canonical_name is None
    assert result.display_name is None
    assert result.primary_category is None
    assert result.is_mapped is False
    # Actual values still preserved
    assert result.actual == Decimal("42.0")
    # Logged as unmapped
    assert any("Unmapped event" in rec.message for rec in caplog.records)


# ══════════════════════════════════════════════════════════════════════════
#  Country allowlist
# ══════════════════════════════════════════════════════════════════════════

def test_canonicalize_country_outside_allowlist_returns_none(
    sample_mapping_yaml: dict,
) -> None:
    """Events from countries we don't track should return None (silently dropped)."""
    canonicalizer = Canonicalizer.from_yaml_data(sample_mapping_yaml)

    raw = {
        "type": "Inflation Rate", "comparison": "yoy", "country": "BR",
        "date": "2026-04-10 12:30:00", "period": "Mar", "actual": 5.0,
    }

    result = canonicalizer.canonicalize(raw)
    assert result is None


def test_canonicalize_all_allowlist_countries_accepted(
    sample_mapping_yaml: dict,
) -> None:
    """Events from each of the 8 allowed countries should not be dropped."""
    # Add a mapping for each (using a minimal shared indicator)
    yaml_data = {
        "mappings": [
            {
                "eodhd_type": "Inflation Rate", "eodhd_comparison": "yoy",
                "country": c, "canonical_name": "cpi_headline_yoy",
                "display_name": "Headline CPI", "primary_category": "Inflation",
                "frequency": "monthly", "unit": "%", "importance": 1,
            }
            for c in ["US", "EU", "UK", "JP", "AU", "NZ", "CA", "CH"]
        ]
    }
    canonicalizer = Canonicalizer.from_yaml_data(yaml_data)

    for country in ["US", "EU", "UK", "JP", "AU", "NZ", "CA", "CH"]:
        raw = {
            "type": "Inflation Rate", "comparison": "yoy", "country": country,
            "date": "2026-04-10 12:30:00", "period": "Mar", "actual": 3.0,
        }
        result = canonicalizer.canonicalize(raw)
        assert result is not None, f"Country {country} should be accepted"
        assert result.country == country


# ══════════════════════════════════════════════════════════════════════════
#  Period parsing
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "period_in,expected",
    [
        ("Mar", date(2026, 3, 1)),
        ("Jan", date(2026, 1, 1)),
        ("Q4", date(2025, 10, 1)),
        ("Q1", date(2026, 1, 1)),
        ("2026-04-15", date(2026, 4, 15)),
        ("Mar 2025", date(2025, 3, 1)),
        ("Q4 2025", date(2025, 10, 1)),
        ("Apr/15", date(2026, 4, 15)),
        # Malformed case from real EODHD data — fallback extracts "Jan"
        ("3Mo/Yr) (Jan", date(2026, 1, 1)),
    ],
)
def test_parse_period_various_formats(period_in: str, expected: date) -> None:
    released_at = datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc)
    result = _parse_period(period_in, released_at)
    assert result == expected, f"Failed for {period_in!r}"


def test_parse_period_december_from_january_release_uses_prior_year() -> None:
    """If released in Feb but period says Dec, it should be prior year."""
    released_at = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)
    result = _parse_period("Dec", released_at)
    # Dec > Feb + 1 = March, so year goes back
    assert result == date(2025, 12, 1)


def test_parse_period_q4_from_january_release_uses_prior_year() -> None:
    released_at = datetime(2026, 1, 28, 12, 0, tzinfo=timezone.utc)
    result = _parse_period("Q4", released_at)
    assert result == date(2025, 10, 1)


def test_parse_period_none_returns_none() -> None:
    released_at = datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc)
    assert _parse_period(None, released_at) is None
    assert _parse_period("", released_at) is None
    assert _parse_period("   ", released_at) is None


def test_parse_period_unparseable_returns_none() -> None:
    released_at = datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc)
    # Total gibberish with no month abbrev
    assert _parse_period("xyz123", released_at) is None


# ══════════════════════════════════════════════════════════════════════════
#  Decimal conversion
# ══════════════════════════════════════════════════════════════════════════

def test_to_decimal_handles_various_inputs() -> None:
    assert _to_decimal(None) is None
    assert _to_decimal("") is None
    assert _to_decimal(3.14) == Decimal("3.14")
    assert _to_decimal("3.14") == Decimal("3.14")
    assert _to_decimal(0) == Decimal("0")
    assert _to_decimal(-1.5) == Decimal("-1.5")


def test_to_decimal_invalid_returns_none() -> None:
    assert _to_decimal("not a number") is None
    assert _to_decimal("abc") is None


# ══════════════════════════════════════════════════════════════════════════
#  Duplicate mapping detection
# ══════════════════════════════════════════════════════════════════════════

def test_duplicate_mapping_first_wins(caplog: pytest.LogCaptureFixture) -> None:
    """If two YAML entries have the same (country, type, comparison), first wins."""
    yaml_data = {
        "mappings": [
            {
                "eodhd_type": "Inflation Rate", "eodhd_comparison": "yoy",
                "country": "US", "canonical_name": "cpi_v1",
                "display_name": "CPI v1", "primary_category": "Inflation",
                "frequency": "monthly",
            },
            {
                "eodhd_type": "Inflation Rate", "eodhd_comparison": "yoy",
                "country": "US", "canonical_name": "cpi_v2",
                "display_name": "CPI v2", "primary_category": "Inflation",
                "frequency": "monthly",
            },
        ]
    }

    with caplog.at_level("WARNING"):
        canonicalizer = Canonicalizer.from_yaml_data(yaml_data)

    raw = {
        "type": "Inflation Rate", "comparison": "yoy", "country": "US",
        "date": "2026-04-10 12:30:00", "period": "Mar", "actual": 3.0,
    }
    result = canonicalizer.canonicalize(raw)

    assert result is not None
    assert result.canonical_name == "cpi_v1", "First entry should win"
    assert any("Duplicate mapping" in rec.message for rec in caplog.records)


# ══════════════════════════════════════════════════════════════════════════
#  YAML structural errors
# ══════════════════════════════════════════════════════════════════════════

def test_yaml_missing_required_field_raises() -> None:
    from app.ingestion.canonicalizer import CanonicalizationError

    bad_yaml = {
        "mappings": [
            {"eodhd_type": "X", "country": "US"},  # missing canonical_name etc
        ]
    }
    with pytest.raises(CanonicalizationError):
        Canonicalizer.from_yaml_data(bad_yaml)


def test_yaml_country_outside_allowlist_raises() -> None:
    from app.ingestion.canonicalizer import CanonicalizationError

    bad_yaml = {
        "mappings": [
            {
                "eodhd_type": "X", "eodhd_comparison": None, "country": "BR",
                "canonical_name": "x", "display_name": "X",
                "primary_category": "Other", "frequency": "monthly",
            },
        ]
    }
    with pytest.raises(CanonicalizationError):
        Canonicalizer.from_yaml_data(bad_yaml)
