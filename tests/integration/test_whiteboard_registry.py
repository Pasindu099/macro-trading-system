"""Live whiteboard registry and released-indicator library checks."""

import json

import pytest
from sqlalchemy import text

from app.db.session import dispose_engine, get_sessionmaker
from app.settings import SUPPORTED_CURRENCIES
from app.whiteboard.categories import CATEGORY_ORDER
from app.whiteboard.registry import WIDGET_REGISTRY, indicator_library, search_library, widget_data


@pytest.mark.asyncio
async def test_registry_json_and_unique_library_for_all_currencies():
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""
            SELECT c.currency_code, i.country_code, i.canonical_name
            FROM indicators i JOIN countries c ON c.code = i.country_code
            WHERE c.currency_code = ANY(:currencies)
              AND EXISTS (SELECT 1 FROM indicator_releases r WHERE r.indicator_id = i.id)
        """), {"currencies": list(SUPPORTED_CURRENCIES)})
        expected = {}
        for currency, country, code in rows:
            expected.setdefault(currency, set()).add((country, code))
    for currency in SUPPORTED_CURRENCIES:
        library = await indicator_library(currency)
        keys = [(item["config"]["country_code"], item["config"]["indicator_code"])
                for item in library]
        assert len(keys) == len(set(keys))
        assert set(keys) == expected.get(currency, set())
        assert all(item["category"] in CATEGORY_ORDER for item in library)
        assert all(isinstance(item["cb_preferred"], bool) for item in library)
        assert all(item in search_library(library, item["secondary_categories"][0])
                   for item in library if item["secondary_categories"])
        config = library[0]["config"] if library else {}
        for widget_type in WIDGET_REGISTRY:
            payload = await widget_data(widget_type, currency,
                                        config if widget_type == "indicator" else {})
            assert set(payload) == {"title", "unit", "latest", "series", "surprise_sigma", "status_chip"}
            json.dumps(payload, allow_nan=False)
    usd = await indicator_library("USD")
    earnings = search_library(usd, "earn")
    assert earnings
    assert all("avg_hourly_earnings" in item["config"]["indicator_code"]
               or item["config"]["indicator_code"].startswith("employment_cost_")
               for item in earnings)
    assert any(item["config"]["indicator_code"].startswith("employment_cost_")
               for item in earnings)
    await dispose_engine()
