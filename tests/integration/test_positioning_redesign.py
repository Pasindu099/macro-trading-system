"""New layout controls, parameter validation, cache separation and live states."""
import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from httpx import ASGITransport, AsyncClient
from app.api.routes import positioning_page as routes
from app.db.session import dispose_engine
from app.services import desks


@pytest.fixture
async def client():
    await dispose_engine()
    desks._panel_cache.clear()
    app=FastAPI();app.include_router(routes.router)
    app.mount("/static",StaticFiles(directory="app/web/static"),name="static")
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client: yield client
    desks._panel_cache.clear();await dispose_engine()


@pytest.mark.asyncio
async def test_redesigned_controls_and_group_histories(client):
    response=await client.get("/positioning/panels/crowding")
    assert response.status_code == 200 and 'data-view="simple"' in response.text and 'data-view="bars"' in response.text
    assert "Show all trader groups" in response.text and "Slow money" in response.text
    price=await client.get("/positioning/panels/price?instrument=USDJPY&units=pct_oi&group=legacy_noncommercial&range_key=1Y")
    assert price.status_code == 200 and 'data-position-points' in price.text
    assert "Large speculators (as on most sites)" in price.text and 'value="USD/JPY" selected' in price.text
    assert all(f'data-series="{name}"' in price.text for name in ("net","long","short","price","oi","bands"))
    cross=await client.get("/positioning/panels/price?instrument=EURJPY&units=pct_oi")
    assert "two futures markets" in cross.text and "showing US dollars instead" in cross.text
    assert "Hedge funds" in cross.text and "Long-term investors" in cross.text
    squeeze=await client.get("/positioning/panels/squeeze")
    assert "who might be forced out" in squeeze.text and "Bets as of" in squeeze.text and "price as of" in squeeze.text
    assert "Big one-sided bet" in squeeze.text and "They are backing out" in squeeze.text


@pytest.mark.asyncio
async def test_invalid_chart_parameters_and_page_query_preservation(client):
    for query in ("instrument=USDEUR","group=invalid","units=raw","range_key=10Y","view=unknown"):
        assert (await client.get("/positioning?"+query)).status_code == 400
    response=await client.get("/positioning?instrument=USDJPY&units=pct_oi&range_key=5Y&group=legacy_noncommercial")
    assert response.status_code == 200
    assert "positioning.js" in response.text and "range_key=5Y" in response.text and "group=legacy_noncommercial" in response.text
