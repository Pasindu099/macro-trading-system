"""InvestingLive RSS retrieval for the News page and public feed."""

from __future__ import annotations

import html
import re
from typing import Any
from xml.etree import ElementTree

import httpx

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
