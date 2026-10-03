"""Step 7 Part B: US real GDP and ISM services sub-index mappings."""

import pytest

from app.ingestion.canonicalizer import Canonicalizer


@pytest.fixture(scope="module")
def canonicalizer() -> Canonicalizer:
    return Canonicalizer.from_default_config()


def _event(event_type: str, comparison: str | None = None, period: str = "Aug") -> dict:
    return {"date": "2026-09-03 14:00:00", "type": event_type, "country": "US", "comparison": comparison,
            "period": period, "actual": 52.0, "previous": 51.0, "estimate": None}


@pytest.mark.parametrize(("event_type", "comparison", "expected"), [
    ("GDP Growth Rate", "qoq", "real_gdp_qoq_annualised"),
    ("ISM Services New Orders", None, "ism_services_new_orders"),
    ("ISM Non-Manufacturing New Orders", None, "ism_services_new_orders"),
    ("ISM Services Prices", None, "ism_services_prices"),
    ("ISM Non-Manufacturing Prices", None, "ism_services_prices"),
    ("ISM Services Employment", None, "ism_services_employment"),
    ("ISM Non-Manufacturing Employment", None, "ism_services_employment"),
    ("ISM Non-Manufacturing Business Activity", None, "ism_services_business_activity"),
])
def test_new_us_mappings(canonicalizer, event_type, comparison, expected):
    period = "Q2" if expected.startswith("real_gdp") else "Aug"
    event = canonicalizer.canonicalize(_event(event_type, comparison, period))
    assert event is not None and event.canonical_name == expected


def test_us_gdp_mapping_is_top_tier_quarter_and_does_not_capture_other_countries(canonicalizer):
    us = canonicalizer.canonicalize(_event("GDP Growth Rate", "qoq", "Q2"))
    assert us.importance == 1 and us.primary_category == "Growth"
    assert us.period_start_date is not None and us.period_start_date.month == 4  # Q2 starts in April
    uk = canonicalizer.canonicalize({**_event("GDP Growth Rate", "qoq", "Q2"), "country": "UK"})
    assert uk.canonical_name == "gdp_qoq"


def test_no_substitute_for_ism_manufacturing_production(canonicalizer):
    # EODHD publishes no ISM Manufacturing Production; the Fed's Manufacturing Production
    # is a different series and must not be mapped to the ISM name.
    assert canonicalizer.canonicalize(_event("ISM Manufacturing Production")).canonical_name is None
    mapped = canonicalizer.canonicalize(_event("Manufacturing Production", "mom"))
    assert mapped is None or mapped.canonical_name != "ism_manufacturing_production"
