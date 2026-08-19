from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, time as datetime_time, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

import feedparser
import httpx
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    MetaData,
    Table,
    Text,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from news_pipeline.filter import _term_matches, passes_filter

logger = logging.getLogger(__name__)

RSS_INTERVAL_SECONDS = 45
CENTRAL_BANK_INTERVAL_SECONDS = 90
FRED_INTERVAL_SECONDS = 15 * 60
SOURCE_STALE_AFTER = timedelta(hours=3)
MACRO_FLASH_STREAM_NAME = "news:macro_flash"
MACRO_FLASH_TERMS = (
    "rate decision",
    "interest rate",
    "rate hike",
    "rate cut",
    "rate pause",
    "fomc",
    "federal reserve",
    "fed",
    "ecb",
    "boe",
    "boj",
    "rba",
    "rbnz",
    "boc",
    "cpi",
    "core cpi",
    "inflation",
    "core inflation",
    "pce",
    "core pce",
    "ppi",
    "gdp",
    "gross domestic product",
    "retail sales",
    "unemployment",
    "jobs report",
    "jobless claims",
    "nonfarm payroll",
    "nfp",
    "wage growth",
    "average hourly earnings",
    "pmi",
    "manufacturing pmi",
    "services pmi",
    "ism",
    "consumer confidence",
    "consumer sentiment",
    "industrial production",
    "durable goods",
    "trade balance",
    "crude oil inventories",
    "oil inventories",
    "crude inventories",
    "eia",
    "wti",
    "brent",
    "opec",
    "vix",
    "risk-off",
    "risk-on",
)
MACRO_FLASH_CATEGORIES = {
    "central_bank",
    "inflation",
    "employment",
    "growth",
    "energy",
    "risk_sentiment",
}

metadata = MetaData()

raw_news = Table(
    "raw_news",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("source", Text, nullable=False),
    Column("source_category", Text, nullable=True),
    Column("title", Text, nullable=False),
    Column("body", Text, nullable=True),
    Column("url", Text, nullable=True),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("raw_payload", JSONB, nullable=False),
    Column("is_gated_relevant", Boolean, nullable=False),
)

source_health = Table(
    "source_health",
    metadata,
    Column("source_name", Text, primary_key=True),
    Column("last_item_at", DateTime(timezone=True), nullable=True),
    Column("last_checked_at", DateTime(timezone=True), nullable=False),
    Column("is_healthy", Boolean, nullable=False),
)


@dataclass(frozen=True)
class FeedSource:
    source: str
    category: str
    url: str

    @property
    def health_key(self) -> str:
        return f"{self.source}:{self.category}"


AP_FEEDS = (
    FeedSource("ap_news", "economy", "https://apnews.com/hub/economy"),
    FeedSource("ap_news", "politics", "https://apnews.com/politics"),
    FeedSource("ap_news", "business", "https://apnews.com/business"),
)

AL_JAZEERA_FEEDS = (
    FeedSource("al_jazeera", "all_news", "https://www.aljazeera.com/xml/rss/all.xml"),
)

INVESTINGLIVE_FEEDS = (
    FeedSource("investinglive", "all", "https://investinglive.com/feed/"),
    FeedSource("investinglive", "news", "https://investinglive.com/feed/news/"),
    FeedSource("investinglive", "central_bank", "https://investinglive.com/feed/centralbank/"),
)

FED_FEEDS = (
    FeedSource(
        "federal_reserve",
        "press_releases",
        "https://www.federalreserve.gov/feeds/press_all.xml",
    ),
)

ECB_FEEDS = (
    FeedSource(
        "ecb",
        "press_releases",
        "https://www.ecb.europa.eu/rss/press.html",
    ),
)

_feed_cache: dict[str, dict[str, str]] = {}
_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


class APArticleLinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[dict[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return

        attr_map = {key: value for key, value in attrs}
        href = attr_map.get("href")
        if not href or "/article/" not in href:
            return

        self._current_href = urljoin(self.base_url, href)
        self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._current_href:
            return

        title = html.unescape(" ".join(self._current_text).strip())
        title = " ".join(title.split())
        if title:
            self.links.append({"url": self._current_href, "title": title})
        self._current_href = None
        self._current_text = []


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for news collectors")
    return database_url


def get_fred_api_key() -> str | None:
    return os.getenv("FRED_API_KEY") or None


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _engine, _sessionmaker

    if _sessionmaker is None:
        _engine = create_async_engine(
            get_database_url(),
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)

    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker

    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def collect_ap_news() -> None:
    await _run_collector("ap_news", _collect_ap_page_group(AP_FEEDS))


async def collect_al_jazeera() -> None:
    await _run_collector("al_jazeera", _collect_rss_group(AL_JAZEERA_FEEDS))


async def collect_investinglive() -> None:
    await _run_collector("investinglive", _collect_rss_group(INVESTINGLIVE_FEEDS))


async def collect_federal_reserve() -> None:
    await _run_collector("federal_reserve", _collect_rss_group(FED_FEEDS))


async def collect_ecb() -> None:
    await _run_collector("ecb", _collect_rss_group(ECB_FEEDS))


async def collect_fred_release_calendar() -> None:
    await _run_collector("fred", _collect_fred_release_calendar())


async def _collect_fred_release_calendar() -> None:
    api_key = get_fred_api_key()
    if not api_key:
        logger.warning("FRED_API_KEY is not configured; skipping FRED release calendar fetch")
        return

    today = date.today()
    params = {
        "api_key": api_key,
        "file_type": "json",
        "realtime_start": today.isoformat(),
        "realtime_end": (today + timedelta(days=14)).isoformat(),
        "include_release_dates_with_no_data": "true",
        "limit": 1000,
        "sort_order": "asc",
    }
    response = await _fetch_with_retries(
        "https://api.stlouisfed.org/fred/releases/dates",
        params=params,
        cache_key="fred:release_calendar",
    )
    if response is None:
        await _check_source_staleness("fred:release_calendar")
        return

    payload = response.json()
    fetched_at = datetime.now(UTC)
    rows = [_normalize_fred_release(item, fetched_at) for item in payload.get("release_dates", [])]
    inserted, last_item_at = await _insert_rows(rows)
    await _update_source_health("fred:release_calendar", last_item_at, fetched_at, True)

    logger.info("FRED release calendar fetch complete: inserted=%s total=%s", inserted, len(rows))


async def _collect_rss_group(feeds: tuple[FeedSource, ...]) -> None:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        results = await asyncio.gather(
            *(_collect_feed(client, feed) for feed in feeds),
            return_exceptions=True,
        )

    for feed, result in zip(feeds, results, strict=True):
        if isinstance(result, Exception):
            logger.warning("%s failed without blocking sibling feeds: %s", feed.health_key, result)


async def _collect_ap_page_group(feeds: tuple[FeedSource, ...]) -> None:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        results = await asyncio.gather(
            *(_collect_ap_page(client, feed) for feed in feeds),
            return_exceptions=True,
        )

    for feed, result in zip(feeds, results, strict=True):
        if isinstance(result, Exception):
            logger.warning("%s failed without blocking sibling feeds: %s", feed.health_key, result)


async def _run_collector(name: str, awaitable: Any) -> None:
    try:
        await awaitable
    except Exception as exc:
        logger.warning("%s collector failed without stopping the scheduler: %s", name, exc)


async def _collect_feed(client: httpx.AsyncClient, feed: FeedSource) -> None:
    fetched_at = datetime.now(UTC)
    response = await _fetch_with_retries(feed.url, client=client, cache_key=feed.url)
    if response is None:
        await _check_source_staleness(feed.health_key)
        return

    parsed = feedparser.parse(response.content)
    if parsed.bozo:
        _log_feed_parse_failure(feed, response, parsed.bozo_exception)

    rows = [_normalize_feed_entry(feed, entry, fetched_at) for entry in parsed.entries]
    if not rows and _looks_like_non_feed_response(response):
        _log_feed_parse_failure(feed, response, "no entries parsed from non-feed response")

    inserted, last_item_at = await _insert_rows(rows)
    await _update_source_health(feed.health_key, last_item_at, fetched_at, True)

    logger.info(
        "%s fetch complete: inserted=%s total=%s",
        feed.health_key,
        inserted,
        len(rows),
    )


async def _collect_ap_page(client: httpx.AsyncClient, feed: FeedSource) -> None:
    fetched_at = datetime.now(UTC)
    response = await _fetch_with_retries(feed.url, client=client, cache_key=feed.url)
    if response is None:
        await _check_source_staleness(feed.health_key)
        return

    parser = APArticleLinkParser(feed.url)
    parser.feed(response.text)

    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for item in parser.links:
        url = item["url"]
        if url in seen:
            continue
        seen.add(url)
        rows.append(_normalize_ap_page_entry(feed, item, fetched_at))

    if not rows:
        logger.warning(
            "%s AP page parse produced no article rows; status=%s content_type=%s first_200=%r",
            feed.health_key,
            response.status_code,
            response.headers.get("content-type"),
            _response_preview(response),
        )

    inserted, last_item_at = await _insert_rows(rows)
    await _update_source_health(feed.health_key, last_item_at, fetched_at, True)
    logger.info(
        "%s page fetch complete: inserted=%s total=%s",
        feed.health_key,
        inserted,
        len(rows),
    )


async def _fetch_with_retries(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    params: dict[str, Any] | None = None,
    cache_key: str,
) -> httpx.Response | None:
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=20.0, follow_redirects=True)
    headers = _conditional_headers(cache_key)

    try:
        for attempt in range(1, 4):
            try:
                response = await http_client.get(url, params=params, headers=headers)
                if response.status_code == httpx.codes.NOT_MODIFIED:
                    logger.debug("%s unchanged", cache_key)
                    return None

                response.raise_for_status()
                _remember_cache_headers(cache_key, response)
                return response
            except Exception as exc:
                if attempt == 3:
                    logger.warning("Fetch failed for %s after 3 attempts: %s", cache_key, exc)
                    return None

                delay = 2 ** (attempt - 1)
                logger.warning(
                    "Fetch attempt %s failed for %s; retrying in %ss: %s",
                    attempt,
                    cache_key,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
    finally:
        if owns_client:
            await http_client.aclose()

    return None


def _conditional_headers(cache_key: str) -> dict[str, str]:
    cached = _feed_cache.get(cache_key, {})
    headers: dict[str, str] = {
        "User-Agent": "macro-dashboard-news-pipeline/0.1",
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, application/json",
    }
    if etag := cached.get("etag"):
        headers["If-None-Match"] = etag
    if last_modified := cached.get("last_modified"):
        headers["If-Modified-Since"] = last_modified
    return headers


def _remember_cache_headers(cache_key: str, response: httpx.Response) -> None:
    cached = _feed_cache.setdefault(cache_key, {})
    if etag := response.headers.get("ETag"):
        cached["etag"] = etag
    if last_modified := response.headers.get("Last-Modified"):
        cached["last_modified"] = last_modified


def _normalize_feed_entry(
    feed: FeedSource,
    entry: feedparser.util.FeedParserDict,
    fetched_at: datetime,
) -> dict[str, Any]:
    url = _entry_value(entry, "link")
    published_at = _parse_entry_datetime(entry) or fetched_at
    raw_payload = _json_safe(dict(entry))
    raw_payload["url_hash"] = _url_hash(url)
    raw_payload["feed_url"] = feed.url
    title = _entry_value(entry, "title") or "(untitled)"
    body = _entry_value(entry, "summary") or _entry_value(entry, "description")
    is_gated_relevant, matched_categories = passes_filter(title, body or "")
    raw_payload["filter_matched_categories"] = matched_categories

    return {
        "source": feed.source,
        "source_category": feed.category,
        "title": title,
        "body": body,
        "url": url,
        "published_at": published_at,
        "fetched_at": fetched_at,
        "raw_payload": raw_payload,
        "is_gated_relevant": is_gated_relevant,
    }


def _normalize_ap_page_entry(
    feed: FeedSource,
    item: dict[str, str],
    fetched_at: datetime,
) -> dict[str, Any]:
    url = item.get("url")
    title = item.get("title") or "(untitled)"
    body = title
    raw_payload: dict[str, Any] = {
        "url_hash": _url_hash(url),
        "feed_url": feed.url,
        "source_format": "ap_section_html",
    }
    is_gated_relevant, matched_categories = passes_filter(title, body)
    raw_payload["filter_matched_categories"] = matched_categories

    return {
        "source": feed.source,
        "source_category": feed.category,
        "title": title,
        "body": body,
        "url": url,
        "published_at": fetched_at,
        "fetched_at": fetched_at,
        "raw_payload": raw_payload,
        "is_gated_relevant": is_gated_relevant,
    }


def _normalize_fred_release(item: dict[str, Any], fetched_at: datetime) -> dict[str, Any]:
    release_id = item.get("release_id")
    release_date = _parse_date(str(item.get("date") or ""))
    published_at = datetime.combine(release_date or fetched_at.date(), datetime_time.min, tzinfo=UTC)
    release_name = item.get("release_name") or f"FRED release {release_id}"
    url = f"https://fred.stlouisfed.org/release?rid={release_id}&date={published_at.date()}"
    raw_payload = _json_safe(dict(item))
    raw_payload["url_hash"] = _url_hash(url)
    title = f"Scheduled economic release: {release_name}"
    body = "Scheduled economic data release date from the FRED release calendar."
    is_gated_relevant, matched_categories = passes_filter(title, body)
    raw_payload["filter_matched_categories"] = matched_categories

    return {
        "source": "fred",
        "source_category": "release_calendar",
        "title": title,
        "body": body,
        "url": url,
        "published_at": published_at,
        "fetched_at": fetched_at,
        "raw_payload": raw_payload,
        "is_gated_relevant": is_gated_relevant,
    }


async def _insert_rows(rows: list[dict[str, Any]]) -> tuple[int, datetime | None]:
    if not rows:
        return 0, None

    inserted = 0
    last_item_at = max(row["published_at"] for row in rows)
    maker = get_sessionmaker()

    async with maker() as session:
        async with session.begin():
            for row in rows:
                url = row.get("url")
                if not url:
                    continue

                url_hash = _url_hash(url)
                exists = await session.scalar(
                    select(raw_news.c.source).where(func.md5(raw_news.c.url) == url_hash)
                )
                if exists:
                    continue

                result = await session.execute(
                    pg_insert(raw_news).values(**row).returning(raw_news.c.id)
                )
                raw_news_id = int(result.scalar_one())
                inserted += 1
                if _is_macro_flash(row):
                    await _publish_macro_flash(raw_news_id, row)

    return inserted, last_item_at


def _is_macro_flash(row: dict[str, Any]) -> bool:
    if not row.get("is_gated_relevant"):
        return False

    raw_payload = row.get("raw_payload") or {}
    categories = set(raw_payload.get("filter_matched_categories") or [])
    if not categories.intersection(MACRO_FLASH_CATEGORIES):
        return False

    text_value = f"{row.get('title') or ''} {row.get('body') or ''}".lower()
    return any(_term_matches(text_value, term) for term in MACRO_FLASH_TERMS)


async def _publish_macro_flash(raw_news_id: int, row: dict[str, Any]) -> None:
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return

    from redis.asyncio import Redis

    raw_payload = row.get("raw_payload") or {}
    redis = Redis.from_url(redis_url, decode_responses=True)
    try:
        await redis.xadd(
            MACRO_FLASH_STREAM_NAME,
            {
                "raw_news_id": str(raw_news_id),
                "headline": str(row.get("title") or "(untitled)"),
                "source": str(row.get("source") or ""),
                "source_category": str(row.get("source_category") or ""),
                "released_at": _json_safe(row.get("published_at")),
                "url": str(row.get("url") or ""),
                "matched_categories": json.dumps(
                    raw_payload.get("filter_matched_categories") or [],
                    ensure_ascii=False,
                ),
            },
        )
        logger.info("Published macro flash for raw_news_id=%s", raw_news_id)
    finally:
        await redis.aclose()


async def _update_source_health(
    source_name: str,
    last_item_at: datetime | None,
    last_checked_at: datetime,
    is_healthy: bool,
) -> None:
    maker = get_sessionmaker()
    values = {
        "source_name": source_name,
        "last_item_at": last_item_at,
        "last_checked_at": last_checked_at,
        "is_healthy": is_healthy,
    }

    async with maker() as session:
        async with session.begin():
            statement = pg_insert(source_health).values(**values)
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[source_health.c.source_name],
                    set_={
                        "last_item_at": func.coalesce(
                            statement.excluded.last_item_at,
                            source_health.c.last_item_at,
                        ),
                        "last_checked_at": statement.excluded.last_checked_at,
                        "is_healthy": statement.excluded.is_healthy,
                    },
                ),
            )

    await _warn_if_source_stale(source_name, last_item_at or await _get_last_item_at(source_name))


async def _check_source_staleness(source_name: str) -> None:
    await _warn_if_source_stale(source_name, await _get_last_item_at(source_name))


async def _get_last_item_at(source_name: str) -> datetime | None:
    maker = get_sessionmaker()
    async with maker() as session:
        return await session.scalar(
            select(source_health.c.last_item_at).where(source_health.c.source_name == source_name)
        )


async def _warn_if_source_stale(source_name: str, last_item_at: datetime | None) -> None:
    if last_item_at is None:
        logger.warning("%s has not produced any news items yet", source_name)
        return

    age = datetime.now(UTC) - _ensure_aware(last_item_at)
    if age > SOURCE_STALE_AFTER:
        logger.warning(
            "%s has not produced any news items in over 3 hours; last_item_at=%s",
            source_name,
            last_item_at.isoformat(),
        )


def _entry_value(entry: feedparser.util.FeedParserDict, key: str) -> str | None:
    value = entry.get(key)
    if value is None:
        return None
    return str(value).strip() or None


def _log_feed_parse_failure(
    feed: FeedSource,
    response: httpx.Response,
    error: Any,
) -> None:
    logger.warning(
        "Feed parse warning for %s: %s; status=%s content_type=%s first_200=%r",
        feed.health_key,
        error,
        response.status_code,
        response.headers.get("content-type"),
        _response_preview(response),
    )


def _looks_like_non_feed_response(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    preview = _response_preview(response).lstrip().lower()
    return "html" in content_type or preview.startswith("<!doctype html") or preview.startswith("<html")


def _response_preview(response: httpx.Response) -> str:
    return response.text[:200].replace("\n", " ").replace("\r", " ")


def _parse_entry_datetime(entry: feedparser.util.FeedParserDict) -> datetime | None:
    for key in ("published", "updated", "created"):
        value = entry.get(key)
        if value:
            try:
                return _ensure_aware(parsedate_to_datetime(str(value)))
            except (TypeError, ValueError):
                continue
    return None


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _url_hash(url: str | None) -> str | None:
    if not url:
        return None
    return hashlib.md5(url.encode("utf-8")).hexdigest()
