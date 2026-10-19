from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.db.session import dispose_engine, get_sessionmaker
from app.services.myfxbook import INSERT


@pytest.mark.asyncio
async def test_snapshots_stored_and_daily_view_keeps_last_of_day():
    rows = [
        {"source": "test_src", "pair": "EURUSD", "taken_at": datetime(2040, 1, 1, h, 5, tzinfo=UTC),
         "long_pct": long, "short_pct": 100 - long, "long_positions": 10, "short_positions": 20,
         "avg_long_price": 1.1, "avg_short_price": 1.2}
        for h, long in ((0, 40), (4, 42), (20, 47))
    ] + [{"source": "test_src", "pair": "EURUSD", "taken_at": datetime(2040, 1, 2, 8, 5, tzinfo=UTC),
          "long_pct": 30, "short_pct": 70, "long_positions": None, "short_positions": None,
          "avg_long_price": None, "avg_short_price": None}]
    async with get_sessionmaker()() as session:
        await session.execute(INSERT, rows)
        await session.execute(INSERT, rows[:1])  # duplicate snapshot ignored
        stored = (await session.execute(text(
            "SELECT count(*) FROM retail_sentiment_snapshots WHERE source = 'test_src'"))).scalar()
        daily = (await session.execute(text("""
            SELECT day, long_pct FROM retail_sentiment_daily WHERE source = 'test_src' ORDER BY day
        """))).all()
        await session.rollback()
    await dispose_engine()
    assert stored == 4
    assert [(str(day), float(long)) for day, long in daily] == [("2040-01-01", 47.0), ("2040-01-02", 30.0)]
