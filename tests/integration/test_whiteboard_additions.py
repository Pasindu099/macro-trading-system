"""Focused upload validation and indicator range contracts, without PostgreSQL."""
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.session import get_session
from app.whiteboard import registry
from app.whiteboard import router as routes


@pytest.mark.asyncio
@pytest.mark.parametrize("mime,data,status", [
    ("image/gif", b"GIF89a", 415),
    ("image/png", b"not a PNG", 415),
    ("image/jpeg", b"\x89PNG\r\n\x1a\n", 415),
    ("image/png", b"\x89PNG\r\n\x1a\n" + b"x" * (5 * 1024 * 1024), 413),
    ("image/png", b"\x89PNG\r\n\x1a\n", 201),
    ("image/jpeg", b"\xff\xd8\xff\xe0", 201),
    ("image/webp", b"RIFF\x04\x00\x00\x00WEBP", 201),
], ids=["gif", "bad-magic", "mismatch", "oversize", "png", "jpeg", "webp"])
async def test_upload_validation(monkeypatch, mime, data, status):
    session = AsyncMock()
    image_result = MagicMock()
    image_result.scalar_one.return_value = 12
    item_result = MagicMock()
    item_result.first.return_value = MagicMock(_mapping={"id": 9, "kind": "image"})
    session.execute.side_effect = [image_result, item_result]
    monkeypatch.setattr(routes, "_board", AsyncMock(return_value={"id": 1}))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_session] = lambda: session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/boards/1/images", content=data, headers={"Content-Type": mime})
    assert response.status_code == status
    if status != 201:
        session.execute.assert_not_called()
        assert "5 MB" in response.text if status == 413 else "magic bytes" in response.text
    else:
        assert session.execute.call_args_list[0].args[1]["data"] == data
        session.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("range_name,expected", [
    ("6M", date(2026, 4, 28)), ("1Y", date(2025, 10, 28)),
    ("5Y", date(2021, 10, 28)), ("10Y", date(2016, 10, 28)), ("ALL", date.min),
])
async def test_indicator_range(monkeypatch, range_name, expected):
    class Today(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 28)

    monkeypatch.setattr(registry, "date", Today)
    monkeypatch.setattr(registry, "_countries", AsyncMock(return_value={"US"}))
    points = [(date(2026, 10, 1), 2.5)]
    history = AsyncMock(side_effect=lambda country, codes, since: {
        "cpi": [point for point in points if point[0] >= since]})
    monkeypatch.setattr(registry.desk_panels, "indicator_history", history)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=AsyncMock())
    context.__aexit__ = AsyncMock()
    monkeypatch.setattr(registry, "get_sessionmaker", lambda: lambda: context)
    monkeypatch.setattr(registry, "build_event_innovation_feed", AsyncMock(return_value={"rows": []}))
    result = await registry.widget_data("indicator", "USD", {"indicator_code": "cpi", "range": range_name})
    assert history.call_args.args[2] == expected
    assert result["series"] == [["2026-10-01", 2.5]]


@pytest.mark.asyncio
async def test_invalid_range_and_default(monkeypatch):
    data = AsyncMock(return_value={"series": []})
    monkeypatch.setattr(routes, "widget_data", data)
    app = FastAPI()
    app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/whiteboard/widget-data/indicator?currency=USD&range=2Y")).status_code == 422
        data.assert_not_awaited()
        assert (await client.get("/api/whiteboard/widget-data/indicator?currency=USD")).status_code == 200
        assert data.call_args.args[2]["range"] == "5Y"


@pytest.mark.asyncio
async def test_image_and_chart_settings_persist_in_postgres():
    import base64
    from uuid import uuid4
    from sqlalchemy import text
    from app.db.session import dispose_engine, get_sessionmaker

    app = FastAPI()
    app.include_router(routes.router)
    board_id = None
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jM1sAAAAASUVORK5CYII=")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        try:
            created = await client.post("/api/boards", json={"currency": "USD", "name": "image-test-" + uuid4().hex})
            assert created.status_code == 201
            board_id = created.json()["id"]
            base = f"/api/boards/{board_id}"
            uploaded = await client.post(base + "/images?x=123&y=44", content=png, headers={"Content-Type": "image/png"})
            assert uploaded.status_code == 201
            item = uploaded.json()
            config = {**item["config"], "caption": "Screenshot", "source": "IG client sentiment", "as_of": "2026-10-28"}
            changed = await client.patch(base + f"/items/{item['id']}", json={"config": config, "x": 240, "y": 160, "w": 640, "h": 480})
            assert changed.status_code == 200
            widget = await client.post(base + "/items", json={"kind": "widget", "widget_type": "indicator",
                "config": {"indicator_code": "cpi_headline_yoy", "chart_type": "bar", "range": "10Y"},
                "x": 0, "y": 0, "w": 420, "h": 280})
            assert widget.status_code == 201
            restored = (await client.get(base)).json()["items"]
            image = next(r for r in restored if r["kind"] == "image")
            assert image["config"] == config and (image["x"], image["y"], image["w"], image["h"]) == (240, 160, 640, 480)
            chart = next(r for r in restored if r["kind"] == "widget")
            assert chart["config"]["chart_type"] == "bar" and chart["config"]["range"] == "10Y"
            image_url = base + f"/images/{config['image_id']}"
            assert (await client.get(image_url)).content == png
            assert (await client.delete(base + f"/items/{item['id']}")).status_code == 204
            assert (await client.get(image_url)).status_code == 404
        finally:
            if board_id is not None:
                async with get_sessionmaker()() as session:
                    await session.execute(text("DELETE FROM whiteboards WHERE id=:id"), {"id": board_id})
                    await session.commit()
            await dispose_engine()
