"""Honest identifying User-Agent and robots.txt check for official-source fetchers (Step 12 decision).

No browser spoofing: every central-bank request identifies the dashboard.
"""

from __future__ import annotations

from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.settings import get_settings

PRODUCT = "ForexCompass/1.0"


def user_agent() -> str:
    email = get_settings().contact_email
    return f"{PRODUCT} (research dashboard; {email})" if email else f"{PRODUCT} (research dashboard)"


def headers() -> dict[str, str]:
    return {"User-Agent": user_agent()}


async def robots_allows(client: httpx.AsyncClient, url: str) -> tuple[bool, str]:
    """(allowed, note). An unreadable robots.txt (4xx) allows; a 403 on robots.txt itself means blocked."""
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    response = await client.get(robots_url)
    if response.status_code == 403:
        return False, f"robots.txt HTTP 403"
    if response.status_code >= 400:
        return True, f"robots.txt HTTP {response.status_code}"
    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser.can_fetch(PRODUCT, url), "robots.txt read"
