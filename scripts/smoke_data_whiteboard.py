"""Authenticated localhost release smoke; never print session cookies or secrets."""
import asyncio
import base64
import json
from uuid import uuid4

import httpx
from sqlalchemy import text

from app.auth import create_session_token
from app.db.session import dispose_engine, get_sessionmaker
from app.settings import get_settings


async def main():
    board_id = None
    async with get_sessionmaker()() as session:
        user_id = (await session.execute(text("SELECT id FROM users WHERE is_active ORDER BY id LIMIT 1"))).scalar_one_or_none()
        assert user_id is not None, "No active account for authenticated smoke"
        canonical = (await session.execute(text("SELECT canonical_name FROM indicators WHERE country_code='US' ORDER BY id LIMIT 1"))).scalar_one()
    cookies = {get_settings().auth_session_cookie: create_session_token(user_id)}
    checks = 0
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", cookies=cookies, timeout=120) as client:
        try:
            for path in ("/health", "/data", "/data/country/US", "/data/country/CN", "/data/sources",
                         f"/data/indicator/US/{canonical}", "/data/datasets/fx_spot", "/desks/USD/whiteboard",
                         "/data/content?view=landing", "/data/content?view=country&country=US",
                         f"/data/content?view=indicator&country=US&canonical={canonical}",
                         "/data/content?view=sources", "/data/content?view=dataset&name=fx_spot&series=USD%2FJPY"):
                response = await client.get(path)
                assert response.status_code == 200, f"{path}: {response.status_code}"
                assert 'data-state="unavailable"' not in response.text, f"{path}: unavailable"
                checks += 1
            redirect = await client.get("/country/US")
            assert redirect.status_code == 301 and redirect.headers["location"] == "/data/country/US"
            for path in (f"/data/indicator/US/{canonical}/csv", "/data/datasets/fx_spot/csv?series=USD%2FJPY&start=2020-01-01"):
                response = await client.get(path)
                assert response.status_code == 200 and response.headers.get("x-export-row-limit") == "50000"
                assert response.headers["content-type"].startswith("text/csv")
                checks += 1
            response = await client.post("/api/boards", json={"currency": "USD", "name": "deploy-smoke-" + uuid4().hex})
            assert response.status_code == 201
            board_id = response.json()["id"]
            base = f"/api/boards/{board_id}"
            png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jM1sAAAAASUVORK5CYII=")
            uploaded = await client.post(base + "/images?x=80&y=80", content=png, headers={"Content-Type": "image/png"})
            assert uploaded.status_code == 201
            image = uploaded.json()
            config = {**image["config"], "caption": "Deployment smoke", "source": "QA", "as_of": "2026-10-28"}
            saved = await client.patch(base + f"/items/{image['id']}", json={"config": config, "x": 160, "y": 200, "w": 560, "h": 400})
            assert saved.status_code == 200
            widget = await client.post(base + "/items", json={"kind": "widget", "widget_type": "indicator",
                "config": {"indicator_code": canonical, "chart_type": "bar", "range": "10Y"},
                "x": 0, "y": 0, "w": 420, "h": 280})
            assert widget.status_code == 201
            restored = (await client.get(base)).json()["items"]
            stored_image = next(row for row in restored if row["kind"] == "image")
            assert stored_image["config"] == config and stored_image["w"] == 560 and stored_image["x"] == 160
            stored_chart = next(row for row in restored if row["kind"] == "widget")
            assert stored_chart["config"]["chart_type"] == "bar" and stored_chart["config"]["range"] == "10Y"
            image_url = base + f"/images/{config['image_id']}"
            assert (await client.get(image_url)).content == png
            assert (await client.delete(base + f"/items/{image['id']}")).status_code == 204
            assert (await client.get(image_url)).status_code == 404
            invalid = await client.get(f"/api/whiteboard/widget-data/indicator?currency=USD&range=2Y")
            assert invalid.status_code == 422
            print(json.dumps({"page_export_checks": checks, "redirect": "passed", "image_persistence_cleanup": "passed", "bar_10y_persistence": "passed"}))
        finally:
            if board_id is not None:
                async with get_sessionmaker()() as session:
                    await session.execute(text("DELETE FROM whiteboards WHERE id=:id"), {"id": board_id})
                    await session.commit()
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
