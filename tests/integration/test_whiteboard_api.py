"""Whiteboard API persistence and validation against local PostgreSQL."""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.db.session import dispose_engine, get_sessionmaker
from app.whiteboard.router import router


@pytest.mark.asyncio
async def test_whiteboard_crud_round_trip_and_cascade():
    app = FastAPI()
    app.include_router(router)
    app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
    name = f"test-{uuid4().hex}"
    board_id = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        try:
            bad = await client.get("/whiteboard/XXX/boards")
            assert bad.status_code == 422
            bad = await client.post("/api/boards", json={"currency": "XXX", "name": name})
            assert bad.status_code == 422

            first = (await client.get("/whiteboard/CHF/boards")).json()
            second = (await client.get("/whiteboard/CHF/boards")).json()
            assert [board["id"] for board in first] == [board["id"] for board in second]
            assert sum(board["name"] == "Main" for board in second) == 1
            page = await client.get("/desks/CHF/whiteboard")
            assert page.status_code == 200 and 'data-currency="CHF"' in page.text

            response = await client.post("/api/boards", json={"currency": "USD", "name": name})
            assert response.status_code == 201
            board_id = response.json()["id"]
            assert (await client.get(f"/api/boards/{board_id}")).json()["items"] == []
            assert (await client.get("/api/boards/999999999")).status_code == 404
            bad = await client.post(f"/api/boards/{board_id}/items",
                                    json={"kind": "bad", "x": 0, "y": 0, "w": 100, "h": 100})
            assert bad.status_code == 422

            item = (await client.post(f"/api/boards/{board_id}/items", json={
                "kind": "note", "config": {"text": "first"}, "x": 20, "y": 40, "w": 240, "h": 180,
            })).json()
            target = (await client.post(f"/api/boards/{board_id}/items", json={
                "kind": "thesis", "config": {"short_term": "up"}, "x": 300, "y": 40,
                "w": 320, "h": 240,
            })).json()
            changed = await client.patch(f"/api/boards/{board_id}/items/{item['id']}",
                                         json={"x": 60, "w": 260, "config": {"text": "edited"}})
            assert changed.status_code == 200
            assert changed.json()["config"] == {"text": "edited"}
            assert changed.json()["x"] == 60
            link = await client.post(f"/api/boards/{board_id}/links", json={
                "from_item_id": item["id"], "to_item_id": target["id"], "label": "supports",
            })
            assert link.status_code == 201
            assert (await client.delete(
                f"/api/boards/{board_id}/links/{link.json()['id']}"
            )).status_code == 204
            assert (await client.post(f"/api/boards/{board_id}/links", json={
                "from_item_id": item["id"], "to_item_id": target["id"],
            })).status_code == 201
            viewport = await client.patch(f"/api/boards/{board_id}/viewport",
                                          json={"x": 12, "y": -24, "zoom": 1.25})
            assert viewport.json()["viewport"] == {"x": 12, "y": -24, "zoom": 1.25}
            restored = (await client.get(f"/api/boards/{board_id}")).json()
            assert len(restored["items"]) == 2 and len(restored["links"]) == 1
            assert restored["viewport"]["zoom"] == 1.25

            assert (await client.delete(f"/api/boards/{board_id}/items/{item['id']}")).status_code == 204
            assert (await client.get(f"/api/boards/{board_id}")).json()["links"] == []
            another = (await client.post(f"/api/boards/{board_id}/items", json={
                "kind": "note", "x": 0, "y": 0, "w": 100, "h": 100,
            })).json()
            assert (await client.post(f"/api/boards/{board_id}/links", json={
                "from_item_id": another["id"], "to_item_id": target["id"],
            })).status_code == 201

            assert (await client.get("/api/whiteboard/library?currency=USD")).status_code == 200
            partial = await client.get("/api/whiteboard/library/labor?currency=USD&q=earn")
            assert partial.status_code == 200 and "avg_hourly_earnings" in partial.text
            payload = await client.get("/api/whiteboard/widget-data/research_notes?currency=USD")
            assert payload.json()["status_chip"] == "data source pending"
        finally:
            if board_id is not None:
                async with get_sessionmaker()() as session:
                    await session.execute(text("DELETE FROM whiteboards WHERE id=:id"), {"id": board_id})
                    await session.commit()
                    counts = (await session.execute(text("""
                        SELECT (SELECT count(*) FROM whiteboard_items WHERE board_id=:id),
                               (SELECT count(*) FROM whiteboard_links WHERE board_id=:id)
                    """), {"id": board_id})).one()
                    assert counts == (0, 0)
            await dispose_engine()
