from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.responses import Response

import app.auth as auth
from app.api.routes.auth import _safe_next_path
from app.auth import (
    AuthUser,
    create_session_token,
    hash_password,
    parse_session_token,
    verify_password,
)


def test_password_hash_roundtrip() -> None:
    stored = hash_password("a very strong password")

    assert verify_password("a very strong password", stored)
    assert not verify_password("wrong password", stored)


def test_session_token_roundtrip() -> None:
    token = create_session_token(42)

    assert parse_session_token(token) == 42
    assert parse_session_token(token + "tampered") is None


def test_role_hierarchy() -> None:
    admin = AuthUser(id=1, email="admin@example.com", display_name="Admin", role="admin")
    viewer = AuthUser(id=2, email="viewer@example.com", display_name="Viewer", role="viewer")

    assert admin.has_role("analyst")
    assert not viewer.has_role("analyst")


@pytest.mark.asyncio
async def test_unauthenticated_home_goes_to_login_with_home_return(monkeypatch) -> None:
    @asynccontextmanager
    async def session_scope():
        yield object()

    async def user_count(_session):
        return 1

    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(
        auth_enabled=True, auth_session_cookie="fc_session"))
    monkeypatch.setattr(auth, "session_scope", session_scope)
    monkeypatch.setattr(auth, "user_count", user_count)
    request = Request({"type": "http", "method": "GET", "path": "/",
                       "query_string": b"", "headers": [], "scheme": "http",
                       "server": ("testserver", 80)})
    response = await auth.AuthMiddleware(lambda *_args: None).dispatch(
        request, lambda _request: Response("should not load"))
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/"


def test_login_return_never_targets_fragment():
    assert _safe_next_path("/data-freshness") == "/"
    assert _safe_next_path("/overview/panels/hero") == "/"
    assert _safe_next_path("/desks/USD/panels/verdict") == "/"
    assert _safe_next_path("/desks/USD") == "/desks/USD"
    assert _safe_next_path("//elsewhere.example") == "/"
