"""Step 17 navigation, permanent redirects and real calendar targets."""
import re

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.routes import calendar, data
from app.db.session import dispose_engine, get_sessionmaker
from app.services import desks


@pytest.fixture
async def client():
    desks._panel_cache.clear()
    app = FastAPI()
    app.include_router(data.router)
    app.include_router(calendar.router)
    app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    desks._panel_cache.clear()
    await dispose_engine()


@pytest.mark.asyncio
async def test_permanent_redirects_and_query_preservation(client):
    for old, target in (("/country/us?theme=inflation", "/data/country/US?theme=inflation"),
                        ("/country/US/indicator/cpi_headline_yoy?start=2020-01-01",
                         "/data/indicator/US/cpi_headline_yoy?start=2020-01-01")):
        response = await client.get(old)
        assert response.status_code == 301 and response.headers["location"] == target
    assert (await client.get("/country/ZZ")).status_code == 404
    assert (await client.get("/country/ZZ/indicator/cpi")).status_code == 404
    response = await client.get("/country/CN", follow_redirects=True)
    assert response.status_code == 200 and 'data-content-url=' in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("currency,code,selection,expected", [
    ("USD", "US", "EZ", "US"), ("EUR", "EU", "EZ", "EU"),
    ("EUR", "EU", "DE", "DE"), ("EUR", "EU", "FR", "FR"),
    ("GBP", "UK", "EZ", "UK"), ("JPY", "JP", "EZ", "JP"),
    ("CAD", "CA", "EZ", "CA"), ("CHF", "CH", "EZ", "CH"),
    ("AUD", "AU", "EZ", "AU"), ("NZD", "NZ", "EZ", "NZ"),
])
async def test_key_data_links_even_when_unavailable(client, currency, code, selection, expected):
    html = data.templates.get_template("desk/panels/keydata.html").render(
        desk={"currency": currency, "country": code}, ctx={"state": "unavailable", "country": selection,
        "country_options": [], "message": "No history"}, url="/desks/test/panels/keydata")
    assert f'href="/data/country/{expected}"' in html and f"All {expected} indicators" in html
    assert (await client.get(f"/data/country/{expected}")).status_code == 200


@pytest.mark.asyncio
async def test_eur_monitor_links(client):
    html = data.templates.get_template("desk/panels/country.html").render(ctx={"state": "unavailable"})
    assert 'href="/data/country/DE"' in html and "Germany indicators" in html
    assert 'href="/data/country/FR"' in html and "France indicators" in html
    for code in ("DE", "FR"):
        assert (await client.get(f"/data/country/{code}")).status_code == 200


@pytest.mark.asyncio
async def test_calendar_full_history_target(client):
    async with get_sessionmaker()() as session:
        release_id = (await session.execute(text("""
            SELECT r.id FROM indicator_releases r JOIN indicators i ON i.id=r.indicator_id
            WHERE i.country_code='US' AND r.is_latest AND r.actual IS NOT NULL AND r.released_at <= now()
            ORDER BY r.released_at DESC, r.id DESC LIMIT 1
        """))).scalar_one()
    response = await client.get(f"/calendar/event/{release_id}")
    assert response.status_code == 200 and "Full history" in response.text
    match = re.search(r'href="(/data/indicator/US/[^"]+)"', response.text)
    assert match and (await client.get(match.group(1))).status_code == 200
