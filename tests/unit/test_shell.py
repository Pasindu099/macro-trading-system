from fastapi.testclient import TestClient
from types import SimpleNamespace
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from app.main import app
import app.auth as auth
from app.api.routes import auth as auth_routes
from app.api.routes import shell
from app.services import event_reaction_log
from starlette.requests import Request


def test_placeholder_sections_and_event_log_navigation(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(auth_enabled=False))
    async def board(_session):
        return {"rows": [{"currency": "USD", "overall_score": 0.75, "inflation_score": 0.5,
                          "labor_score": 0.25, "growth_score": 0.1, "date": "2026-10-04"},
                         {"currency": "EUR", "overall_score": -0.2, "inflation_score": -0.1,
                          "labor_score": 0.0, "growth_score": -0.3, "date": "2026-10-04"}]}
    monkeypatch.setattr(shell, "get_macro_state_board", board)
    client = TestClient(app)
    for path, title in (("/desks", "Desks"), ("/pairs", "Pairs"),
                        ("/calendar", "Calendar"),
                        ("/news", "News"), ("/data", "Data")):
        response = client.get(path)
        assert response.status_code == 200
        assert f"<h1>{title}</h1>" in response.text
        assert '<header class="site-header">' in response.text
    positioning_page = client.get("/positioning?ccy=EUR")                    # Step 14: no longer a placeholder
    assert positioning_page.status_code == 200 and '<header class="site-header">' in positioning_page.text
    assert 'hx-get="/positioning/panels/summary?lookback=3y&ccy=EUR&window=1W"' in positioning_page.text
    terminal = client.get("/central-banks?bank=SNB&view=projections")       # Step 12: no longer a placeholder
    assert terminal.status_code == 200 and 'aria-label="Central banks"' in terminal.text
    assert 'hx-get="/central-banks/panels/proj-formats?bank=SNB"' in terminal.text
    overview = client.get("/").text
    assert 'hx-get="/overview/panels/hero"' in overview
    assert '<header class="site-header">' in overview
    assert 'class="mobile-menu-toggle"' in overview
    assert overview.index('>Overview</a>') < overview.index('>Desks</summary>') < overview.index('>Pairs</a>')
    assert overview.index('>Central Banks</a>') < overview.index('>News</a>') < overview.index('>Calendar</summary>')
    assert 'href="/calendar/event-log"' in overview
    moved = client.get("/event-log", follow_redirects=False)
    assert moved.status_code == 307 and moved.headers["location"] == "/calendar/event-log"
    for path in ("/desks",):
        page = client.get(path).text
        assert 'href="/desks/USD"' in page and 'href="/desks/EUR"' in page
        assert "Macro score +0.75" in page and "Macro score -0.20" in page
        assert "Growth +0.10" in page and "Growth -0.30" in page
        assert "This section is being prepared" not in page
    assert "chart.umd.min.js" not in client.get("/").text


def test_news_geopolitics_filter_is_opt_in(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(auth_enabled=False))
    calls = []

    async def feed(*, show_no_fx_link=False, limit=100):
        calls.append(show_no_fx_link)
        return [{"published_at": __import__("datetime").datetime(2026, 10, 19),
                 "source": "al_jazeera", "news_type": "Geopolitics", "impact": "Low",
                 "url": "https://example.com/story", "title": "Ceasefire talks"}] if show_no_fx_link else []

    monkeypatch.setattr(shell.news, "tagged_feed", feed)
    client = TestClient(app)
    assert "Ceasefire talks" not in client.get("/news").text
    shown = client.get("/news?show_geopolitics_without_fx_link=1").text
    assert "Ceasefire talks" in shown and calls == [False, True]


def test_news_page_filters_past_date_range(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(auth_enabled=False))
    calls = []

    async def feed(**filters):
        calls.append(filters)
        return [{"published_at": __import__("datetime").datetime(2020, 3, 18, tzinfo=__import__("datetime").timezone.utc),
                 "source": "investinglive", "news_type": "Data", "impact": "High",
                 "url": "https://example.com/cpi", "title": "Historical CPI", "why": "Actual above consensus",
                 "currencies": ["USD"], "situation_ids": [], "speakers": [], "drivers": []}]

    async def sources(_day):
        return []

    async def today(_day):
        return {"releases": [], "speakers": [], "blackouts": [], "mentions": []}

    monkeypatch.setattr(shell.news, "tagged_feed", feed)
    monkeypatch.setattr(shell.news, "source_health", sources)
    monkeypatch.setattr(shell.news, "today_context", today)
    client = TestClient(app)
    page = client.get("/news?start=2020-03-01&end=2020-03-31&currency=USD&type=Data&impact=High&active=1")
    assert page.status_code == 200 and "Historical CPI" in page.text
    assert "2020-03-01" in page.text and "2020-03-31" in page.text
    assert 'css/news.css' in page.text and 'aria-label="Sources"' in page.text
    assert 'aria-label="Market-moving now"' in page.text and 'aria-label="Today"' in page.text
    assert page.headers["cache-control"] == "private, max-age=60"
    assert calls[0]["start"].isoformat() == "2020-03-01"
    assert calls[0]["end"].isoformat() == "2020-03-31"
    assert calls[0]["active_only"] is True and calls[0]["currency"] == "USD"
    assert client.get("/news?start=2020-04-01&end=2020-03-01").status_code == 400


def test_login_template_uses_new_tokens():
    response = TestClient(app).get("/login")
    assert response.status_code == 200
    assert "Sign in" in response.text
    assert "shell.css" in response.text


@pytest.mark.parametrize("path,active", [
    ("/", "Overview"), ("/desks", "Desks"), ("/desks/USD", "Desks"),
    ("/desks/EUR", "Desks"), ("/pairs", "Pairs"),
    ("/central-banks", "Central Banks"), ("/news", "News"),
    ("/calendar", "Calendar"), ("/calendar/event-log", "Calendar"),
    ("/positioning", "Positioning"), ("/data", "Data"),
])
def test_top_navigation_on_dashboard_pages(monkeypatch, path, active):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(auth_enabled=False))
    async def board(_session):
        return {"rows": []}
    async def no_entries(_session, **_filters):
        return []
    monkeypatch.setattr(shell, "get_macro_state_board", board)
    monkeypatch.setattr(event_reaction_log, "list_entries", no_entries)
    response = TestClient(app).get(path)
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")
    header = soup.select_one("header.site-header")
    assert header is not None
    assert header.select_one(".brand strong").get_text(strip=True) == "ForexCompass"
    assert header.select_one(".brand span").get_text(strip=True) == "Research Terminal"
    nav = header.select_one("nav.site-nav")
    items = nav.select(":scope > a, :scope > details > summary")
    assert [item.get_text(strip=True) for item in items] == [
        "Overview", "Desks", "Pairs", "Central Banks", "News", "Calendar",
        "Positioning", "Data"]
    assert [item.get_text(strip=True) for item in items if "active" in item.get("class", [])] == [active]
    assert header.select_one(".mobile-menu-toggle[aria-expanded='false']")
    assert header.select_one(".nav-freshness > summary")


def test_nav_subitems_and_mobile_breakpoint(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(auth_enabled=False))
    soup = BeautifulSoup(TestClient(app).get("/").text, "html.parser")
    desks = soup.select_one(".nav-group .nav-dropdown")
    assert [a.get_text(strip=True) for a in desks.select("a")] == ["USD", "EUR"]
    assert [row.get_text(" ", strip=True) for row in desks.select(".nav-disabled")] == [
        f"{code} pending" for code in ("GBP", "JPY", "AUD", "NZD", "CAD", "CHF")]
    calendar = soup.select(".nav-group .nav-dropdown")[1]
    assert [a.get_text(strip=True) for a in calendar.select("a")] == ["Calendar", "Event Log"]
    css = Path("app/web/static/css/navigation.css").read_text(encoding="utf-8")
    assert "@media(max-width:1023px)" in css
    assert ".mobile-menu-toggle{display:none}" in css
    assert ".nav-panel.is-open{display:flex" in css


async def _zero_users(session):
    return 0


def test_setup_template_renders_with_new_tokens(monkeypatch):
    monkeypatch.setattr(auth_routes, "user_count", _zero_users)
    request = Request({"type": "http", "method": "GET", "path": "/setup",
                       "headers": [], "query_string": b"", "server": ("testserver", 80),
                       "scheme": "http", "root_path": "", "app": app})
    response = __import__("asyncio").run(auth_routes.setup_page(request, session=None))
    assert response.status_code == 200
    assert "Create the admin account" in response.body.decode()
    assert "shell.css" in response.body.decode()
