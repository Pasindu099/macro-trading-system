"""Step 13 newswire collection: config-driven RSS -> raw_news (the single news storage path).

Sources live in config/news_sources.yaml. Every request carries the honest identifying
User-Agent and each feed URL is checked against robots.txt (cached per host for a day).
Rows keep the news_pipeline shape (url_hash, feed_url in raw_payload) so both writers
dedupe on md5(url) during the side-by-side parity window.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import feedparser
import httpx
import yaml
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from app.db.session import session_scope
from app.services import source_identity

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "news_sources.yaml"
MIN_INTERVAL_SECONDS = 45
THIRD_PARTY_MIN_INTERVAL_SECONDS = 300
ROBOTS_TTL = timedelta(hours=24)
COLLECTOR_TAG = "app"

_robots_cache: dict[str, tuple[datetime, bool, str]] = {}
_conditional: dict[str, dict[str, str]] = {}


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    validate_config(data)
    return data


def validate_config(data: dict[str, Any]) -> None:
    for source_id, source in data["sources"].items():
        if "licensed_for_redistribution" not in source:
            raise ValueError(f"{source_id}: licensed_for_redistribution is required")
        interval = int(source["interval_seconds"])
        floor = MIN_INTERVAL_SECONDS if source_id == "investinglive" else THIRD_PARTY_MIN_INTERVAL_SECONDS
        if source_id in {"federal_reserve", "ecb"}:
            floor = 90  # official central-bank feeds, cadence carried over from news_pipeline
        if interval < floor:
            raise ValueError(f"{source_id}: interval {interval}s below polite minimum {floor}s")
        if source.get("enabled") and not all(feed.get("url") for feed in source["feeds"]):
            raise ValueError(f"{source_id}: enabled source with an empty feed URL")


def enabled_sources() -> dict[str, dict[str, Any]]:
    return {sid: s for sid, s in load_config()["sources"].items() if s.get("enabled")}


def visible_source_ids(show_unlicensed: bool | None = None) -> set[str]:
    """Sources the UI may show; the single switch hides all unlicensed sources."""
    config = load_config()
    show = config.get("show_unlicensed_sources", True) if show_unlicensed is None else show_unlicensed
    return {
        sid for sid, s in config["sources"].items()
        if show or s.get("licensed_for_redistribution")
    }


def source_label(source_id: str) -> str:
    source = load_config()["sources"].get(source_id)
    return source["label"] if source else source_id


def url_hash(url: str | None) -> str | None:
    return hashlib.md5(url.encode("utf-8")).hexdigest() if url else None


async def robots_allowed(client: httpx.AsyncClient, url: str, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    host = urlsplit(url).netloc
    key = f"{host}{urlsplit(url).path}"
    cached = _robots_cache.get(key)
    if cached and now - cached[0] < ROBOTS_TTL:
        return cached[1]
    try:
        allowed, note = await source_identity.robots_allows(client, url)
    except httpx.HTTPError as exc:
        allowed, note = False, f"robots.txt unreachable: {exc}"
    _robots_cache[key] = (now, allowed, note)
    if not allowed:
        logger.warning("news feed %s skipped: %s", url, note)
    return allowed


def _entry_value(entry: Any, key: str) -> str | None:
    value = entry.get(key)
    return str(value).strip() if value else None


def _entry_datetime(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime(*parsed[:6], tzinfo=UTC)
    for key in ("published", "updated"):
        raw = entry.get(key)
        if raw:
            try:
                value = parsedate_to_datetime(raw)
                return value if value.tzinfo else value.replace(tzinfo=UTC)
            except (TypeError, ValueError):
                continue
    return None


def normalize_entry(source_id: str, category: str, feed_url: str, entry: Any, fetched_at: datetime) -> dict[str, Any] | None:
    url = _entry_value(entry, "link")
    if not url:
        return None
    published_at = _entry_datetime(entry) or fetched_at
    return {
        "source": source_id,
        "source_category": category,
        "title": _entry_value(entry, "title") or "(untitled)",
        "body": _entry_value(entry, "summary") or _entry_value(entry, "description"),
        "url": url,
        "published_at": min(published_at, fetched_at + timedelta(minutes=5)),
        "fetched_at": fetched_at,
        "raw_payload": {
            "url_hash": url_hash(url),
            "feed_url": feed_url,
            "guid": _entry_value(entry, "id"),
            "collector": COLLECTOR_TAG,
            "seen_by_app": True,
        },
    }


def parse_feed(source_id: str, category: str, feed_url: str, content: bytes, fetched_at: datetime) -> list[dict[str, Any]]:
    parsed = feedparser.parse(content)
    rows = [normalize_entry(source_id, category, feed_url, e, fetched_at) for e in parsed.entries]
    return [row for row in rows if row]


INSERT_RAW = text(
    """
    INSERT INTO raw_news (source, source_category, title, body, url, published_at, fetched_at,
                          raw_payload, is_gated_relevant)
    SELECT :source, :source_category, :title, :body, :url, :published_at, :fetched_at,
           :raw_payload, false
    WHERE NOT EXISTS (SELECT 1 FROM raw_news WHERE md5(url) = :url_hash)
    RETURNING id
    """
).bindparams(bindparam("raw_payload", type_=JSONB))

MARK_SEEN = text(
    """
    UPDATE raw_news SET raw_payload = raw_payload || '{"seen_by_app": true}'::jsonb
    WHERE md5(url) = :url_hash AND NOT (raw_payload ? 'seen_by_app')
    """
)

UPSERT_HEALTH = text(
    """
    INSERT INTO source_health (source_name, last_item_at, last_checked_at, is_healthy)
    VALUES (:name, :last_item_at, :checked_at, :healthy)
    ON CONFLICT (source_name) DO UPDATE SET
        last_item_at = GREATEST(source_health.last_item_at, EXCLUDED.last_item_at),
        last_checked_at = EXCLUDED.last_checked_at,
        is_healthy = EXCLUDED.is_healthy
    """
)


async def store_rows(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    inserted = 0
    async with session_scope() as session:
        for row in rows:
            params = {**row, "url_hash": row["raw_payload"]["url_hash"]}
            result = await session.execute(INSERT_RAW, params)
            if result.scalar_one_or_none() is not None:
                inserted += 1
            else:
                await session.execute(MARK_SEEN, {"url_hash": params["url_hash"]})
    return inserted


async def _fetch(client: httpx.AsyncClient, url: str) -> httpx.Response | None:
    request_headers = dict(_conditional.get(url, {}))
    response = await client.get(url, headers=request_headers)
    if response.status_code == httpx.codes.NOT_MODIFIED:
        return None
    response.raise_for_status()
    cache = _conditional.setdefault(url, {})
    if etag := response.headers.get("ETag"):
        cache["If-None-Match"] = etag
    if modified := response.headers.get("Last-Modified"):
        cache["If-Modified-Since"] = modified
    return response


async def collect_source(source_id: str) -> dict[str, int]:
    """Fetch every feed of one source; failures of one feed do not block siblings."""
    source = load_config()["sources"][source_id]
    counts = {"feeds": 0, "parsed": 0, "inserted": 0, "skipped_robots": 0, "failed": 0}
    async with httpx.AsyncClient(
        timeout=20.0, follow_redirects=True, headers={
            **source_identity.headers(),
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
        },
    ) as client:
        for feed in source["feeds"]:
            url = feed.get("url")
            if not url:
                continue
            counts["feeds"] += 1
            health_key = f"{source_id}:{feed['category']}"
            checked_at = datetime.now(UTC)
            if not await robots_allowed(client, url, checked_at):
                counts["skipped_robots"] += 1
                await _health(health_key, None, checked_at, False)
                continue
            try:
                response = await _fetch(client, url)
            except httpx.HTTPError as exc:
                counts["failed"] += 1
                logger.warning("news feed %s failed: %s", health_key, exc)
                await _health(health_key, None, checked_at, False)
                continue
            if response is None:
                await _health(health_key, None, checked_at, True)
                continue
            rows = parse_feed(source_id, feed["category"], url, response.content, checked_at)
            counts["parsed"] += len(rows)
            counts["inserted"] += await store_rows(rows)
            last = max((r["published_at"] for r in rows), default=None)
            await _health(health_key, last, checked_at, True)
    logger.info("news %s: %s", source_id, counts)
    return counts


async def _health(name: str, last_item_at: datetime | None, checked_at: datetime, healthy: bool) -> None:
    async with session_scope() as session:
        await session.execute(UPSERT_HEALTH, {
            "name": name, "last_item_at": last_item_at, "checked_at": checked_at, "healthy": healthy,
        })
