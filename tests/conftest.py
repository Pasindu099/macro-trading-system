"""Shared pytest fixtures.

pytest automatically discovers this file and makes fixtures available to
all test files in the same directory tree.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def sample_mapping_yaml() -> dict[str, object]:
    """A small in-memory mapping YAML for canonicalizer tests.

    Deliberately includes alias cases and edge cases we care about:
    - CPI / Inflation Rate aliases (both → cpi_headline_yoy)
    - An indicator with no comparison (unemployment rate)
    - Multiple countries
    """
    return {
        "mappings": [
            {
                "eodhd_type": "Inflation Rate",
                "eodhd_comparison": "yoy",
                "country": "US",
                "canonical_name": "cpi_headline_yoy",
                "display_name": "Headline CPI (YoY)",
                "primary_category": "Inflation",
                "frequency": "monthly",
                "unit": "%",
                "importance": 1,
            },
            {
                # Alias: same canonical_name
                "eodhd_type": "CPI",
                "eodhd_comparison": "yoy",
                "country": "US",
                "canonical_name": "cpi_headline_yoy",
                "display_name": "Headline CPI (YoY)",
                "primary_category": "Inflation",
                "frequency": "monthly",
                "unit": "%",
                "importance": 1,
            },
            {
                "eodhd_type": "Unemployment Rate",
                "eodhd_comparison": None,
                "country": "US",
                "canonical_name": "unemployment_rate",
                "display_name": "Unemployment Rate",
                "primary_category": "Labor",
                "frequency": "monthly",
                "unit": "%",
                "is_higher_better_for_currency": False,
                "importance": 1,
            },
            {
                "eodhd_type": "Average Hourly Earnings",
                "eodhd_comparison": "yoy",
                "country": "US",
                "canonical_name": "avg_hourly_earnings_yoy",
                "display_name": "Avg Hourly Earnings (YoY)",
                "primary_category": "Labor",
                "secondary_categories": ["Inflation"],  # multi-category
                "frequency": "monthly",
                "unit": "%",
                "importance": 2,
            },
            {
                "eodhd_type": "Inflation Rate",
                "eodhd_comparison": "yoy",
                "country": "UK",
                "canonical_name": "cpi_headline_yoy",
                "display_name": "Headline CPI (YoY)",
                "primary_category": "Inflation",
                "frequency": "monthly",
                "unit": "%",
                "importance": 1,
            },
        ]
    }