"""Single read path for news (Step 13): pages, desks and Overview read stored items only here.

Collection: app/services/news_collector.py -> raw_news (permanent). The live RSS helpers below
are kept for reference/tests; pages no longer call them.
"""

from __future__ import annotations

import html
import asyncio
import re
from datetime import UTC, date, datetime, timedelta
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
              AND (NOT :relevant_only OR EXISTS (SELECT 1 FROM news_raw_status s
                   WHERE s.raw_news_id = raw_news.id AND s.status <> 'dropped'))
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


async def daily_counts(day: Any) -> dict[str, dict[str, int]]:
    """Kept / dropped / merged per source for one UTC publish day."""
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text("""
            SELECT r.source, s.status, count(*) AS n
            FROM news_raw_status s JOIN raw_news r ON r.id = s.raw_news_id
            WHERE r.published_at >= cast(:day AS date) AND r.published_at < cast(:day AS date) + 1
            GROUP BY 1, 2
        """), {"day": day})).mappings().all()
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        out.setdefault(row["source"], {"kept": 0, "dropped": 0, "merged": 0})[row["status"]] = row["n"]
    return out


async def tagged_feed(*, show_no_fx_link: bool = False, limit: int = 100,
                      query: str = "", currency: str = "", news_type: str = "",
                      impact: str = "", active_only: bool = False,
                      start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    """Latest deduplicated headlines; unlinked geopolitics is opt-in."""
    from app.services.news_collector import visible_source_ids

    escaped_query = query.replace("%", r"\%").replace("_", r"\_")
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text("""
            SELECT n.id, n.published_at, n.title, n.url, n.source, n.news_type, n.impact,
                   n.currencies, n.no_fx_link, n.speakers, n.situation_ids, n.drivers,
                   a.actual, a.consensus, coalesce(a.newswire_release_id, a.scheduled_release_id) AS release_id
            FROM news_items n LEFT JOIN newswire_actuals a ON a.news_item_id = n.id
            WHERE n.source = ANY(:visible_sources)
              AND (:show_no_fx_link OR NOT n.no_fx_link)
              AND (:query = '' OR n.title ILIKE :search ESCAPE '\' OR coalesce(n.summary, '') ILIKE :search ESCAPE '\')
              AND (:currency = '' OR :currency = ANY(n.currencies))
              AND (:news_type = '' OR n.news_type = :news_type)
              AND (:impact = '' OR n.impact = :impact)
              AND (CAST(:start AS date) IS NULL OR n.published_at >= CAST(:start AS date))
              AND (CAST(:end AS date) IS NULL OR n.published_at < CAST(:end AS date) + INTERVAL '1 day')
              AND (NOT :active_only OR EXISTS (
                  SELECT 1 FROM news_item_episodes ne JOIN situation_episodes e ON e.id = ne.episode_id
                  WHERE ne.news_item_id = n.id AND e.ended_at IS NULL AND e.started_at <= CURRENT_DATE))
            ORDER BY n.published_at DESC, n.id DESC LIMIT :limit
        """), {"show_no_fx_link": show_no_fx_link, "limit": limit,
                "query": query, "search": f"%{escaped_query}%",
                "currency": currency, "news_type": news_type, "impact": impact,
                "active_only": active_only, "start": start, "end": end,
                "visible_sources": list(visible_source_ids())})).mappings().all()
    items = [dict(row) for row in rows]
    from app.services.news_context import why_line
    from app.services.calendar_detail import _event_tracking
    from app.services.situations import get_situation_episodes
    from app.services.cb_tone import get_speakers

    tracking = await _event_tracking() if any(i["news_type"] == "Data" and i["actual"] is not None for i in items) else {}
    episodes = {e["situation_id"]: e for e in await get_situation_episodes()} if any(i["situation_ids"] for i in items) else {}
    focus = {}
    banks = {s["bank"] for item in items for s in item["speakers"] if s.get("bank")}
    for bank in banks:
        for speaker in (await get_speakers(bank))["speakers"]:
            mix = speaker.get("focus_mix") or {}
            if mix:
                focus[(bank, speaker["speaker_id"])] = max(mix, key=mix.get).replace("_", " ")
    for item in items:
        item["why"] = why_line(item, tracking=tracking, episodes=episodes, speaker_focus=focus)
    return items


async def source_health(day: date) -> list[dict[str, Any]]:
    """Enabled sources, today's filter counts and collector check status."""
    from app.services.news_collector import enabled_sources, visible_source_ids

    counts = await daily_counts(day)
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text("""
            SELECT split_part(source_name, ':', 1) AS source,
                   min(last_checked_at) AS last_checked, bool_and(is_healthy) AS healthy
            FROM source_health WHERE source_name LIKE '%:%' GROUP BY 1
        """))).mappings().all()
    checks = {row["source"]: row for row in rows}
    now = datetime.now(UTC)
    return [{"id": sid, "label": spec["label"], "counts": counts.get(sid, {"kept": 0, "dropped": 0, "merged": 0}),
             "last_checked": checks[sid]["last_checked"] if sid in checks else None,
             "status": ("awaiting check" if sid not in checks else "error" if not checks[sid]["healthy"] else
                        "current" if now - checks[sid]["last_checked"] <= timedelta(seconds=max(3 * int(spec["interval_seconds"]), 900))
                        else "stale")}
            for sid, spec in enabled_sources().items() if sid in visible_source_ids()]


async def today_context(day: date) -> dict[str, Any]:
    """Calendar releases, official speech calendars, blackouts and currency mentions."""
    from app.services.calendar_service import list_events
    from app.services.cb_upcoming import get_upcoming

    async with get_sessionmaker()() as session:
        releases = await list_events(session, day, day, min_impact=3)
        mentions = (await session.execute(text("""
            SELECT currency, count(*) AS n FROM news_items n
            CROSS JOIN LATERAL unnest(n.currencies) currency
            WHERE n.published_at >= CAST(:day AS date) AND n.published_at < CAST(:day AS date) + INTERVAL '1 day'
              AND NOT n.no_fx_link GROUP BY currency ORDER BY count(*) DESC, currency LIMIT 8
        """), {"day": day})).mappings().all()
    speakers, blackouts = [], []
    for bank in ("FED", "ECB"):
        try:
            upcoming = await asyncio.wait_for(get_upcoming(bank, today=day), timeout=4)
        except Exception:
            continue
        speakers.extend({"bank": bank, **event} for event in upcoming["speeches"].get("events", [])
                        if event["date"] == day)
        blackouts.extend({"bank": bank, "start": window["start"], "end": window["end"],
                          "verified": window.get("verified", False)}
                         for meeting in upcoming["meetings"]
                         for window in [meeting["blackout"]]
                         if window.get("start") and window["start"] <= day <= window["end"])
    return {"releases": releases[:8], "speakers": speakers[:8], "blackouts": blackouts,
            "mentions": [dict(row) for row in mentions]}
