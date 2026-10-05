from fastapi.testclient import TestClient
from types import SimpleNamespace

from app.main import app
import app.auth as auth
from app.api.routes import auth as auth_routes
from app.api.routes import shell
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
                        ("/calendar", "Calendar"), ("/event-log", "Event Log"),
                        ("/news", "News"), ("/positioning", "Positioning"),
                        ("/central-banks", "Central Banks"), ("/data", "Data")):
        response = client.get(path)
        assert response.status_code == 200
        assert f"<h1>{title}</h1>" in response.text
        assert '<header class="site-header">' in response.text
    overview = client.get("/").text
    assert 'hx-get="/overview/panels/hero"' in overview
    assert '<header class="site-header">' in overview
    assert 'class="mobile-menu"' in overview
    assert overview.index('>Overview</a>') < overview.index('>Desks</a>') < overview.index('>Pairs</a>')
    assert overview.index('>Central Banks</a>') < overview.index('>News</a>') < overview.index('>Calendar</summary>')
    assert 'href="/event-log"' in overview
    for path in ("/desks",):
        page = client.get(path).text
        assert 'href="/desks/USD"' in page and 'href="/desks/EUR"' in page
        assert "Macro score +0.75" in page and "Macro score -0.20" in page
        assert "This section is being prepared" not in page
    assert "chart.umd.min.js" not in client.get("/").text


def test_login_template_uses_new_tokens():
    response = TestClient(app).get("/login")
    assert response.status_code == 200
    assert "Sign in" in response.text
    assert "shell.css" in response.text


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
