"""Every live panel renders its complete, escaped takeaway contract."""
import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from httpx import ASGITransport, AsyncClient
from app.api.routes import positioning_page as routes
from app.db.session import dispose_engine
from app.services import desks


@pytest.mark.asyncio
async def test_all_live_panels_have_takeaways():
    desks._panel_cache.clear()
    app = FastAPI(); app.include_router(routes.router)
    app.mount("/static",StaticFiles(directory="app/web/static"),name="static")
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test",timeout=120) as client:
        for panel in routes.ORDER:
            response = await client.get(f"/positioning/panels/{panel}?ccy=JPY")
            assert response.status_code == 200
            assert '<aside class="takeaway"' in response.text, panel
            assert "What this shows" in response.text and "What it says now" in response.text
            assert 'state-error' not in response.text, panel
    desks._panel_cache.clear()
    await dispose_engine()
