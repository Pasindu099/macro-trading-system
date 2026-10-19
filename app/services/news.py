"""Single read path for news (Step 13): pages, desks and Overview read stored items only here.

Collection: app/services/news_collector.py -> raw_news (permanent). The live RSS helpers below
are kept for reference/tests; pages no longer call them.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any
from xml.etree import ElementTree

import httpx
from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.settings import get_settings

INVESTINGLIVE_RSS_URL = "https://investinglive.com/feed/"


def _rss_node_text(node: ElementTree.Element, name: str) -> str:
    child = node.find(name)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return html.unescape(re.sub(r"\s+", " ", text).strip())


def _news_category(categories: list[str], title: str, description: str) -> str:
    primary_text = " ".join([*categories, title]).lower()
    full_text = f"{primary_text} {description}".lower()
    rules = (
        (r"stock|shares|earnings|s&p|nasdaq|dow|ftse|dax|\bipo\b|dividend|equity|market cap", "Equities"),
        (r"\bfed\b|ecb|boj|boe|rba|rbnz|snb|central bank|rate decision|interest rate|monetary policy|powell|lagarde|inflation|taper|\bqe\b|\bqt\b", "Central banks"),
        (r"\beur\b|\bgbp\b|\bjpy\b|\baud\b|\bcad\b|\bchf\b|\bnzd\b|forex|currency|\busd\b|dollar|pound|yen|euro", "FX"),
        (r"war|sanction|conflict|military|nato|\bun\b|treaty|tariff|trade war|election|government|ministry|president|prime minister|diplomacy|geopolit|invasion|coup|protest|border|embargo", "Geopolitical"),
    )
    for text in (primary_text, full_text):
        for pattern, label in rules:
            if re.search(pattern, text):
                return label
    return "Macro"


def parse_rss_articles(xml_body: str, *, limit: int = 40) -> list[dict[str, Any]]:
    root = ElementTree.fromstring(xml_body)
    articles: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        title = html.unescape(_rss_node_text(item, "title")) or "Untitled headline"
        link = _rss_node_text(item, "link")
        if not link:
            continue
        description = _strip_html(_rss_node_text(item, "description"))
        categories = [
            (category.text or "").strip() for category in item.findall("category")
            if (category.text or "").strip()
        ]
        articles.append({
            "title": title, "link": link, "pubDate": _rss_node_text(item, "pubDate"),
            "description": description,
            "category": _news_category(categories, title, description),
            "source": "investinglive.com",
        })
        if len(articles) >= limit:
            break
    return articles


async def fetch_investinglive_articles(*, limit: int = 40) -> list[dict[str, Any]]:
    """Fetch and normalize live headlines without an HTTP-route dependency."""
    async with httpx.AsyncClient(
        timeout=get_settings().http_timeout_seconds, follow_redirects=True,
    ) as client:
        response = await client.get(
            INVESTINGLIVE_RSS_URL,
            headers={"User-Agent": "MacroDashboard/0.1 RSS reader"},
        )
        response.raise_for_status()
    return parse_rss_articles(response.text, limit=limit)


def normalize_news_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one EODHD news item for the News section."""
    title = item.get("title") or item.get("headline") or item.get("name")
    link = item.get("link") or item.get("url")
    if not title or not link:
        return None
    published_at = item.get("date") or item.get("publishedAt") or item.get("published_at")
    source = item.get("source") or item.get("site") or item.get("source_name") or "EODHD"
    summary = item.get("content") or item.get("text") or item.get("description") or item.get("snippet") or ""
    summary_text = str(summary).strip()
    if len(summary_text) > 180:
        summary_text = summary_text[:177].rstrip() + "..."
    category = str(item.get("category") or "Macro").strip()
    tags = item.get("tags") or item.get("symbols") or []
    if isinstance(tags, str):
        tags = [part.strip() for part in tags.split(",") if part.strip()]
    if not tags and category:
        tags = [category]
    return {
        "title": str(title).strip(), "link": str(link).strip(),
        "source": str(source).strip(), "published_at": published_at,
        "summary": summary_text, "category": category,
        "tags": list(tags)[:3] if isinstance(tags, list) else [],
    }


async def raw_headlines(*, since: datetime, until: datetime | None = None,
                        sources: tuple[str, ...] | None = ("investinglive",),
                        relevant_only: bool = False, limit: int = 400) -> list[dict[str, Any]]:
    """Stored headlines (id, title, body, url, source, published_at), newest first."""
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text("""
            SELECT id, title, body, url, source, source_category, published_at
            FROM raw_news
            WHERE published_at >= :since AND published_at <= coalesce(:until, now())
              AND (cast(:sources AS text[]) IS NULL OR source = ANY(cast(:sources AS text[])))
              AND (NOT :relevant_only OR is_gated_relevant)
            ORDER BY published_at DESC LIMIT :limit
        """), {"since": since, "until": until, "sources": list(sources) if sources else None,
               "relevant_only": relevant_only, "limit": limit})).mappings().all()
    return [dict(row) for row in rows]


async def latest_articles(*, source: str = "investinglive", limit: int = 40) -> list[dict[str, Any]]:
    """Stored replacement for the live investingLive fetch (same article shape)."""
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text("""
            SELECT DISTINCT ON (md5(url)) title, url, body, published_at, raw_payload
            FROM raw_news WHERE source = :source AND published_at <= now()
            ORDER BY md5(url), published_at DESC
        """), {"source": source})).mappings().all()
    rows = sorted(rows, key=lambda row: row["published_at"], reverse=True)[:limit]
    articles = []
    for row in rows:
        payload = row["raw_payload"] or {}
        categories = [t.get("term", "") for t in payload.get("tags", []) if isinstance(t, dict)]
        description = _strip_html(row["body"] or "")
        articles.append({
            "title": row["title"], "link": row["url"], "pubDate": row["published_at"].isoformat(),
            "description": description,
            "category": _news_category(categories, row["title"], description),
            "source": "investinglive.com" if source == "investinglive" else source,
        })
    return articles


async def legacy_alert_feed(*, limit: int) -> list[dict[str, Any]]:
    """Scored alerts from the paused LLM monitor (intelligence.news_alerts), last 24 h."""
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text("""
            SELECT headline, source, url, detected_at, relevance_score, severity,
                   affected_currencies, implied_tier, alert_text
            FROM intelligence.news_alerts
            WHERE detected_at >= NOW() - INTERVAL '24 hours'
            ORDER BY detected_at DESC LIMIT :limit
        """), {"limit": limit})).mappings().all()
    return [dict(row) for row in rows]


async def legacy_alerts_for_currency(currency: str) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""
            SELECT headline, url, source, detected_at, implied_tier, severity, alert_text
            FROM intelligence.news_alerts
            WHERE :ccy = ANY(affected_currencies)
            ORDER BY detected_at DESC LIMIT 40
        """), {"ccy": currency})
        return [dict(r) for r in rows.mappings().all()]
