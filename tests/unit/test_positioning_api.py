from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.routes import positioning as routes


def test_positioning_routes_registered_with_fixed_paths_first():
    paths = [route.path for route in routes.router.routes]
    expected = [
        "/api/positioning/crowding", "/api/positioning/flows", "/api/positioning/squeeze",
        "/api/positioning/extremes/{currency}", "/api/positioning/pair/{pair:path}",
        "/api/positioning/{currency}",
    ]
    assert paths == expected


def test_positioning_routes_require_viewer_when_auth_enabled(monkeypatch):
    monkeypatch.setattr(routes, "get_settings", lambda: SimpleNamespace(auth_enabled=True))
    request = Request({"type": "http", "method": "GET", "path": "/api/positioning/squeeze"})
    with pytest.raises(HTTPException) as exc:
        routes._require_viewer(request)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_unknown_currency_is_404():
    with pytest.raises(HTTPException) as exc:
        await routes.extremes("XXX")
    assert exc.value.status_code == 404
