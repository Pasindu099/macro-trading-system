from datetime import datetime, timedelta, timezone

from app.ingestion.scheduler import fast_poll_pending


def test_fast_poll_window_and_confirmation():
    now = datetime(2026, 10, 14, 13, tzinfo=timezone.utc)
    event = {"impact": 1, "released_at": now, "source_status": "scheduled"}
    assert fast_poll_pending([event], now) == [event]
    assert fast_poll_pending([event | {"released_at": now - timedelta(minutes=9, seconds=59)}], now)
    assert not fast_poll_pending([event | {"released_at": now - timedelta(minutes=10)}], now)
    assert not fast_poll_pending([event | {"released_at": now + timedelta(seconds=1)}], now)
    assert not fast_poll_pending([event | {"source_status": "confirmed"}], now)
    assert not fast_poll_pending([event | {"impact": 2}], now)
