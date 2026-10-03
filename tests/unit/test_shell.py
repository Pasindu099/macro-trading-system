from fastapi.testclient import TestClient
from types import SimpleNamespace

from app.main import app
import app.auth as auth
from app.api.routes import auth as auth_routes
from starlette.requests import Request


def test_placeholder_sections_and_event_log_navigation(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(auth_enabled=False))
    client = TestClient(app)
    for path, title in (("/", "Overview"), ("/desks", "Desks"), ("/pairs", "Pairs"),
                        ("/calendar", "Calendar"), ("/event-log", "Event Log"),
                        ("/news", "News"), ("/positioning", "Positioning"),
                        ("/central-banks", "Central Banks"), ("/data", "Data")):
        response = client.get(path)
        assert response.status_code == 200
        assert f"<h1>{title}</h1>" in response.text
    assert 'href="/event-log" class="nav-sub' in client.get("/").text
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
