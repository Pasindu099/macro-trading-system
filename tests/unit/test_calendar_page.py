from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.main import app
import app.auth as auth
from app.api.routes import calendar


def test_presets_and_custom_range():
    today = date(2026, 10, 14)
    assert calendar.resolve_range("today", today) == (today, today)
    assert calendar.resolve_range("last_week", today) == (date(2026, 10, 5), date(2026, 10, 11))
    assert calendar.resolve_range("last_30_days", today)[0] == date(2026, 9, 15)
    assert calendar.resolve_range("custom", today, "2020-01-01", "2020-02-01") == (date(2020, 1, 1), date(2020, 2, 1))


def test_default_today_in_selected_timezone_and_filters(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(auth_enabled=False))
    client = TestClient(app)
    client.cookies.set("calendar_tz_mode", "local")
    client.cookies.set("calendar_tz_name", "Pacific/Auckland")
    response = client.get("/calendar")
    assert response.status_code == 200
    today = datetime.now(ZoneInfo("Pacific/Auckland")).date().isoformat()
    assert f'name="start" value="{today}"' in response.text
    assert f'name="end" value="{today}"' in response.text
    assert "Pacific/Auckland" in response.text
    custom = client.get("/calendar?preset=custom&start=2020-01-01&end=2020-01-03&impact=all&currency=USD&surprise=1")
    assert 'name="start" value="2020-01-01"' in custom.text
    assert 'name="end" value="2020-01-03"' in custom.text
    assert 'name="surprise" value="1" checked' in custom.text


def test_day_fragment_progresses_one_day(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(auth_enabled=False))
    calls = []
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
    async def fake_events(_session, start, end, currencies, min_impact, **kwargs):
        calls.append((start, end, currencies, min_impact, kwargs))
        return []
    monkeypatch.setattr(calendar, "get_sessionmaker", lambda: lambda: Session())
    monkeypatch.setattr(calendar, "list_events", fake_events)
    response = TestClient(app).get("/calendar/day?preset=custom&start=2020-01-01&end=2020-01-03&day=2020-01-01")
    assert response.status_code == 200
    assert len(calls) == 1
    assert 'day=2020-01-02' in response.text
    assert 'day=2020-01-03' not in response.text
