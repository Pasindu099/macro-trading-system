import hashlib
import logging
from datetime import UTC, datetime

import feedparser
import httpx

from app.services.rss_sources import RSS_SOURCES

_SEEN_TTL_SECONDS = 2 * 60 * 60
_seen_hashes: set[str] = set()
_last_cleared = datetime.now(UTC)

logger = logging.getLogger(__name__)


async def fetch_new_headlines(tier: int) -> list[dict]:
    _clear_seen_hashes_if_needed()
    headlines: list[dict] = []
    sources = [source for source in RSS_SOURCES if source["tier"] == tier]

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for source in sources:
            try:
                response = await client.get(str(source["url"]))
                response.raise_for_status()
                feed = feedparser.parse(response.text)
                for entry in feed.entries:
                    title = str(getattr(entry, "title", "")).strip()
                    url = str(getattr(entry, "link", "")).strip()
                    if not title or not url:
                        continue

                    content_hash = _content_hash(title)
                    if content_hash in _seen_hashes:
                        continue
                    _seen_hashes.add(content_hash)

                    headlines.append(
                        {
                            "title": title,
                            "url": url,
                            "source_name": str(source["name"]),
                            "tier": int(source["tier"]),
                            "category": str(source["category"]),
                            "published_at": _published_at(entry),
                            "content_hash": content_hash,
                        }
                    )
            except Exception:
                logger.exception("Failed to fetch RSS source %s", source.get("name"))

    return headlines


def _clear_seen_hashes_if_needed() -> None:
    global _last_cleared

    now = datetime.now(UTC)
    if (now - _last_cleared).total_seconds() >= _SEEN_TTL_SECONDS:
        _seen_hashes.clear()
        _last_cleared = now


def _content_hash(title: str) -> str:
    normalised = _normalise_title(title)
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:64]


def _normalise_title(title: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else " " for char in title)
    return " ".join(cleaned.split())


def _published_at(entry: dict) -> datetime:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if parsed:
        return datetime(*parsed[:6], tzinfo=UTC)
    return datetime.now(UTC)
