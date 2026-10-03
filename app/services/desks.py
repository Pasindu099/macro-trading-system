"""Currency desk configuration, indicator mapping validation and panel cache."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text

from app.db.session import get_sessionmaker

DESKS_CONFIG = Path("config/desks.yaml")
DESK_ORDER = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]
PANEL_CACHE_SECONDS = 60
_PANEL_CACHE_MAX = 256

_panel_cache: dict[tuple, tuple[float, str]] = {}


def load_desks(path: Path = DESKS_CONFIG) -> dict[str, dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["desks"]


def get_desk(currency: str) -> dict[str, Any] | None:
    """Enabled desk config, or None (route returns 404)."""
    desk = load_desks().get(currency.upper())
    return desk if desk and desk.get("enabled") else None


def desk_nav(active: str) -> list[dict[str, Any]]:
    desks = load_desks()
    return [{"code": code, "enabled": bool(desks.get(code, {}).get("enabled")), "active": code == active}
            for code in DESK_ORDER]


def configured_canonicals(desk: dict[str, Any]) -> list[str]:
    return [s["canonical"] for chart in desk.get("charts", []) for s in chart["series"] if s.get("canonical")]


def validate_indicator_mapping(desk: dict[str, Any], known: set[str]) -> dict[str, Any]:
    """Report configured series whose canonical name is not in the indicators table."""
    missing = []
    for chart in desk.get("charts", []):
        for series in chart["series"]:
            canonical = series.get("canonical")
            if not canonical or canonical not in known:
                missing.append({"chart": chart["id"], "series": series["name"], "canonical": canonical})
    return {"checked": len(configured_canonicals(desk)), "missing": missing}


async def known_canonicals(country: str) -> set[str]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(
            text("SELECT canonical_name FROM indicators WHERE country_code = :c"), {"c": country})
        return {r.canonical_name for r in rows}


# ── 60 s server-side cache for rendered panel HTML ────────────────────

def cache_get(key: tuple) -> str | None:
    hit = _panel_cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    _panel_cache.pop(key, None)
    return None


def cache_set(key: tuple, html: str, ttl: float = PANEL_CACHE_SECONDS) -> None:
    if len(_panel_cache) >= _PANEL_CACHE_MAX:
        now = time.monotonic()
        for stale in [k for k, (exp, _) in _panel_cache.items() if exp <= now] or list(_panel_cache)[:32]:
            _panel_cache.pop(stale, None)
    _panel_cache[key] = (time.monotonic() + ttl, html)


def cache_clear() -> None:
    _panel_cache.clear()
