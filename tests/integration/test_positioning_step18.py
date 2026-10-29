"""New Positioning API payloads against the real local histories."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import positioning as routes
from app.db.session import dispose_engine


@pytest.mark.asyncio
async def test_live_pair_legacy_open_interest_and_squeeze(monkeypatch):
    monkeypatch.setattr(routes,"get_settings",lambda:SimpleNamespace(auth_enabled=False))
    app = FastAPI(); app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
        simple = await client.get("/api/positioning/simple?lookback=52w")
        assert simple.status_code == 200 and len(simple.json()["simple"]) == 8
        ranks = [row["rank"] for row in simple.json()["simple"]]
        assert ranks == sorted(ranks)
        pair = await client.get("/api/positioning/pair/USDJPY?weeks=156")
        assert pair.status_code == 200 and not pair.json()["implied"]
        assert len(pair.json()["groups"]["leveraged_funds"]["history"]) == 156
        legacy = await client.get("/api/positioning/history/JPY?group=legacy_noncommercial&units=pct_oi&range_key=2010")
        assert legacy.status_code == 200
        data = legacy.json()
        assert data["report_type"] == "legacy_futures_only" and data["units"] == "pct_oi"
        assert data["history"][0]["report_date"] == "2010-01-05"
        last = data["history"][-1]
        assert last["net"] == pytest.approx(last["long"]-last["short"])
        cross = await client.get("/api/positioning/history/EUR/JPY?units=pct_oi")
        assert cross.status_code == 200 and cross.json()["units"] == "usd" and cross.json()["explanation"]
        squeeze = await client.get("/api/positioning/squeeze?lookback=52w")
        assert squeeze.status_code == 200
        for card in squeeze.json()["watch_v2"]["cards"]:
            assert len(card["checklist"]) == 3 and "trend" not in card["label"].lower()
            assert card["report_date"] and card["price_basis"]
        extremes = await client.get("/api/positioning/extremes/JPY")
        assert extremes.status_code == 200 and "comparison_v2" in extremes.json()
        for band in extremes.json()["comparison_v2"]["bands"].values():
            if band["too_few"]: assert band["median_move_8w_pct"] is None
    await dispose_engine()
