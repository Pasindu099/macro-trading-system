"""Public API routes for the frontend-facing dashboard experience."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
import html
import json
import re
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    BiggestSurpriseItem,
    BiggestSurprisesPayload,
    CountriesPayload,
    CountryDetailPayload,
    CountrySummary,
    EconomicCalendarEvent,
    EconomicCalendarPayload,
    Envelope,
    IndicatorDetailPayload,
    IndicatorExplorerPayload,
    IndicatorLatestRelease,
    IndicatorMetadata,
    IndicatorSeriesPoint,
    IndicatorSnapshot,
    ResponseMeta,
)
from app.db.models import Country, Indicator, IndicatorRelease, IngestionRun
from app.db.session import get_session
from app.processing.cot import COT_PAIRS, get_all_cot_rows, get_cot_payload, normalize_cot_pair
from app.services.retail_sentiment import (
    configure_myfxbook,
    get_retail_sentiment,
    get_retail_sentiment_symbol,
    load_history,
    refresh_retail_sentiment,
    source_status,
)
from app.settings import get_settings

router = APIRouter(prefix="/api", tags=["public"])
RANGE_LOOKBACK_DAYS = {
    "1y": 365,
    "3y": 365 * 3,
    "5y": 365 * 5,
    "all": None,
}
CALENDAR_CATEGORIES = (
    "Inflation",
    "Growth",
    "Labor",
    "Monetary Policy",
    "Trade",
    "Sentiment",
    "Housing",
    "Other",
)
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
NEWS_SENTIMENT_MODEL = "gpt-4.1"
INVESTINGLIVE_RSS_URL = "https://investinglive.com/feed/"

RETAIL_SENTIMENT_CATEGORIES = {"all", "forex", "indices", "commodities", "crypto"}
RETAIL_SENTIMENT_SORTS = {"rank", "az", "long", "short", "net"}


class MyfxbookConfigRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)

CB_FEED_CACHE_TTL = 900  # 15 minutes
CB_RSS_FEEDS: dict[str, dict[str, Any]] = {
    "USD": {"name": "Fed", "urls": ["https://www.federalreserve.gov/feeds/press_all.xml"]},
    "EUR": {"name": "ECB", "urls": ["https://www.ecb.europa.eu/rss/press.html"]},
    "GBP": {
        "name": "BoE",
        "urls": [
            "https://www.bankofengland.co.uk/rss/news",
            "https://www.bankofengland.co.uk/rss/speeches",
            "https://www.bankofengland.co.uk/rss/publications",
        ],
    },
    "JPY": {
        "name": "BoJ",
        "urls": ["https://www.boj.or.jp/rss/whatsnew.xml"],
        "listing_url": "https://www.boj.or.jp/en/whatsnew/",
    },
    "AUD": {
        "name": "RBA",
        "urls": [
            "https://www.rba.gov.au/rss/rss-cb-media-releases.xml",
            "https://www.rba.gov.au/rss/rss-cb-speeches.xml",
        ],
        "listing_url": "https://www.rba.gov.au/media-releases/",
    },
    "CAD": {"name": "BoC", "urls": ["https://www.bankofcanada.ca/feed/"]},
    "CHF": {
        "name": "SNB",
        "urls": [
            "https://www.snb.ch/public/en/rss/news",
            "https://www.snb.ch/public/en/rss/news/publications/media-releases",
            "https://www.snb.ch/public/en/rss/news/publications/speeches",
        ],
        "listing_url": "https://www.snb.ch/en/news-publications/news",
    },
    "NZD": {
        "name": "RBNZ",
        "urls": ["https://www.rbnz.govt.nz/hub/news/rss"],
        "listing_url": "https://www.rbnz.govt.nz/news-and-events/news",
    },
}
_cb_feed_cache: dict[str, dict[str, Any]] = {}


class NewsSentimentRequest(BaseModel):
    headline: str = Field(..., min_length=1, max_length=500)
    description: str = Field(default="", max_length=6000)


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

    if re.search(
        r"stock|shares|earnings|s&p|nasdaq|dow|ftse|dax|\bipo\b|dividend|equity|market cap",
        primary_text,
    ):
        return "Equities"
    if re.search(
        r"\bfed\b|ecb|boj|boe|rba|rbnz|snb|central bank|rate decision|interest rate"
        r"|monetary policy|powell|lagarde|inflation|taper|\bqe\b|\bqt\b",
        primary_text,
    ):
        return "Central banks"
    if re.search(
        r"\beur\b|\bgbp\b|\bjpy\b|\baud\b|\bcad\b|\bchf\b|\bnzd\b|forex|currency"
        r"|\busd\b|dollar|pound|yen|euro",
        primary_text,
    ):
        return "FX"
    if re.search(
        r"war|sanction|conflict|military|nato|\bun\b|treaty|tariff|trade war|election"
        r"|government|ministry|president|prime minister|diplomacy|geopolit|invasion"
        r"|coup|protest|border|embargo",
        primary_text,
    ):
        return "Geopolitical"
    if re.search(
        r"stock|shares|earnings|s&p|nasdaq|dow|ftse|dax|\bipo\b|dividend|equity|market cap",
        full_text,
    ):
        return "Equities"
    if re.search(
        r"\bfed\b|ecb|boj|boe|rba|rbnz|snb|central bank|rate decision|interest rate"
        r"|monetary policy|powell|lagarde|inflation|taper|\bqe\b|\bqt\b",
        full_text,
    ):
        return "Central banks"
    if re.search(
        r"\beur\b|\bgbp\b|\bjpy\b|\baud\b|\bcad\b|\bchf\b|\bnzd\b|forex|currency"
        r"|\busd\b|dollar|pound|yen|euro",
        full_text,
    ):
        return "FX"
    if re.search(
        r"war|sanction|conflict|military|nato|\bun\b|treaty|tariff|trade war|election"
        r"|government|ministry|president|prime minister|diplomacy|geopolit|invasion"
        r"|coup|protest|border|embargo",
        full_text,
    ):
        return "Geopolitical"
    return "Macro"


def _parse_rss_articles(xml_body: str, *, limit: int = 40) -> list[dict[str, Any]]:
    root = ElementTree.fromstring(xml_body)
    articles: list[dict[str, Any]] = []

    for item in root.findall(".//item"):
        title = html.unescape(_rss_node_text(item, "title")) or "Untitled headline"
        link = _rss_node_text(item, "link")
        if not link:
            continue

        raw_description = _rss_node_text(item, "description")
        description = _strip_html(raw_description)
        categories = [
            (category.text or "").strip()
            for category in item.findall("category")
            if (category.text or "").strip()
        ]
        articles.append({
            "title": title,
            "link": link,
            "pubDate": _rss_node_text(item, "pubDate"),
            "description": description,
            "category": _news_category(categories, title, description),
            "source": "investinglive.com",
        })
        if len(articles) >= limit:
            break

    return articles


async def fetch_investinglive_articles(*, limit: int = 40) -> list[dict[str, Any]]:
    """Fetch and normalize InvestingLive RSS headlines."""
    try:
        async with httpx.AsyncClient(
            timeout=get_settings().http_timeout_seconds,
            follow_redirects=True,
        ) as client:
            response = await client.get(
                INVESTINGLIVE_RSS_URL,
                headers={"User-Agent": "MacroDashboard/0.1 RSS reader"},
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail="InvestingLive feed returned an error.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="InvestingLive feed is unavailable.") from exc

    try:
        return _parse_rss_articles(response.text, limit=limit)
    except ElementTree.ParseError as exc:
        raise HTTPException(status_code=502, detail="InvestingLive feed returned invalid RSS.") from exc


def _extract_response_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    chunks: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks).strip()


def _normalize_sentiment_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sentiment = str(payload.get("sentiment", "neutral")).lower()
    if sentiment not in {"bullish", "bearish", "neutral"}:
        sentiment = "neutral"

    def pct(key: str) -> int:
        value = payload.get(key, 0)
        try:
            return max(0, min(100, int(round(float(value)))))
        except (TypeError, ValueError):
            return 0

    assets = []
    for asset in payload.get("assets", []):
        if not isinstance(asset, dict):
            continue
        direction = str(asset.get("direction", "mixed")).lower()
        if direction not in {"up", "down", "mixed"}:
            direction = "mixed"
        assets.append({
            "symbol": str(asset.get("symbol", "Market"))[:60],
            "impact": pct_from(asset.get("impact")),
            "direction": direction,
            "label": str(asset.get("label", "~ Mixed"))[:80],
        })

    themes = [
        str(theme)[:50]
        for theme in payload.get("themes", [])
        if isinstance(theme, str) and theme.strip()
    ][:8]

    return {
        "sentiment": sentiment,
        "confidence": pct("confidence"),
        "bearish_pct": pct("bearish_pct"),
        "neutral_pct": pct("neutral_pct"),
        "bullish_pct": pct("bullish_pct"),
        "summary": str(payload.get("summary", ""))[:1000],
        "assets": assets[:8],
        "themes": themes,
    }


def pct_from(value: Any) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _meta(session: AsyncSession) -> ResponseMeta:
    latest_run_q = await session.execute(
        select(IngestionRun.finished_at)
        .where(
            IngestionRun.status.in_(["success", "partial"]),
            IngestionRun.finished_at.is_not(None),
        )
        .order_by(desc(IngestionRun.finished_at))
        .limit(1)
    )
    latest_finished_at = latest_run_q.scalar_one_or_none()

    cache_hint = None
    if latest_finished_at is not None:
        cache_hint = f"last_ingestion: {latest_finished_at.isoformat()}"
    return ResponseMeta(generated_at=_now(), cache_hint=cache_hint)


def _release_to_schema(
    release: IndicatorRelease | None,
) -> IndicatorLatestRelease | None:
    if release is None:
        return None

    return IndicatorLatestRelease(
        release_id=release.id,
        period=release.period,
        period_start_date=release.period_start_date,
        released_at=release.released_at,
        actual=float(release.actual) if release.actual is not None else None,
        previous=float(release.previous) if release.previous is not None else None,
        estimate=float(release.estimate) if release.estimate is not None else None,
        change=float(release.change) if release.change is not None else None,
        change_percentage=(
            float(release.change_percentage)
            if release.change_percentage is not None else None
        ),
        surprise=float(release.surprise) if release.surprise is not None else None,
        is_latest=release.is_latest,
    )


def _calendar_event_rank(event: EconomicCalendarEvent) -> tuple[int, int, int, int]:
    period_quality = 0
    if event.period:
        normalized_period = event.period.strip()
        if re.fullmatch(r"[A-Z][a-z]{2}", normalized_period):
            period_quality = 2
        elif re.fullmatch(r"Q[1-4](?:[ /-]\d{2,4})?", normalized_period):
            period_quality = 2
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized_period):
            period_quality = 2
        elif len(normalized_period) <= 8 and "(" not in normalized_period and ")" not in normalized_period:
            period_quality = 1

    return (
        1 if event.canonical_name else 0,
        sum(
            1 for value in (event.actual, event.estimate, event.previous, event.surprise)
            if value is not None
        ),
        period_quality,
        1 if event.status == "released" else 0,
        event.release_id,
    )


def _normalize_calendar_event_name(name: str) -> str:
    normalized = name.casefold().strip()
    for prefix in (
        "s&p global ",
        "westpac ",
        "markit ",
        "commbank ",
        "judo bank ",
    ):
        if normalized.startswith(prefix):
            normalized = normalized.removeprefix(prefix)
            break

    normalized = normalized.replace("(mom)", "(mom)")
    normalized = normalized.replace("(yoy)", "(yoy)")
    normalized = normalized.replace("(qoq)", "(qoq)")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _dedupe_calendar_events(
    events: list[EconomicCalendarEvent],
) -> list[EconomicCalendarEvent]:
    deduped: dict[tuple[object, ...], EconomicCalendarEvent] = {}

    for event in events:
        dedupe_name = _normalize_calendar_event_name(event.display_name)
        key = (
            event.country_code,
            dedupe_name,
            event.released_at,
            event.primary_category,
        )
        existing = deduped.get(key)
        if existing is None or _calendar_event_rank(event) > _calendar_event_rank(existing):
            deduped[key] = event

    return sorted(
        deduped.values(),
        key=lambda event: (event.released_at, event.importance, event.display_name),
    )


def _normalize_category_filter(category: str | None) -> str | None:
    if not category:
        return None

    normalized = category.strip().casefold()
    for allowed_category in CALENDAR_CATEGORIES:
        if allowed_category.casefold() == normalized:
            return allowed_category
    return category.strip().title()


def _latest_release_from_rows(
    releases: list[IndicatorRelease],
    *,
    actual_only: bool = False,
) -> IndicatorRelease | None:
    eligible = [
        release for release in releases
        if not actual_only or release.actual is not None
    ]
    if not eligible:
        return None

    eligible.sort(
        key=lambda release: (
            release.period_start_date or date.min,
            release.released_at,
            release.retrieved_at,
            release.id,
        )
    )
    return eligible[-1]


async def get_latest_indicator_release(
    session: AsyncSession,
    indicator_id: int,
    *,
    actual_only: bool = False,
) -> IndicatorRelease | None:
    releases_q = await session.execute(
        select(IndicatorRelease)
        .where(IndicatorRelease.indicator_id == indicator_id)
        .order_by(
            IndicatorRelease.period_start_date.desc().nullslast(),
            desc(IndicatorRelease.released_at),
            desc(IndicatorRelease.retrieved_at),
            desc(IndicatorRelease.id),
        )
        .limit(24)
    )
    return _latest_release_from_rows(
        list(releases_q.scalars().all()),
        actual_only=actual_only,
    )


async def _country_summary(
    session: AsyncSession,
    country: Country,
) -> CountrySummary:
    now = _now()
    indicator_count_q = await session.execute(
        select(func.count(Indicator.id)).where(Indicator.country_code == country.code)
    )
    indicator_count = indicator_count_q.scalar_one()

    latest_release_q = await session.execute(
        select(func.max(IndicatorRelease.released_at))
        .select_from(IndicatorRelease)
        .join(Indicator, Indicator.id == IndicatorRelease.indicator_id)
        .where(
            Indicator.country_code == country.code,
            IndicatorRelease.actual.is_not(None),
            IndicatorRelease.released_at <= now,
        )
    )
    latest_release_at = latest_release_q.scalar_one()

    return CountrySummary(
        code=country.code,
        name=country.name,
        currency_code=country.currency_code,
        central_bank=country.central_bank,
        cb_mandate_type=country.cb_mandate_type,
        cb_inflation_target=(
            float(country.cb_inflation_target)
            if country.cb_inflation_target is not None else None
        ),
        timezone=country.timezone,
        indicator_count=indicator_count,
        latest_release_at=latest_release_at,
    )


async def list_country_summaries(session: AsyncSession) -> list[CountrySummary]:
    """Shared helper for country-card summaries."""
    countries_q = await session.execute(select(Country).order_by(Country.code))
    countries = countries_q.scalars().all()
    return [await _country_summary(session, country) for country in countries]


async def list_biggest_surprises(
    session: AsyncSession,
    *,
    days: int = 7,
    limit: int = 5,
) -> list[BiggestSurpriseItem]:
    """Return the biggest recent mapped surprises for the landing strip."""
    cutoff = _now() - timedelta(days=days)
    surprises_q = await session.execute(
        select(Indicator, IndicatorRelease, Country)
        .join(
            IndicatorRelease,
            and_(
                IndicatorRelease.indicator_id == Indicator.id,
                IndicatorRelease.is_latest.is_(True),
            ),
        )
        .join(Country, Country.code == Indicator.country_code)
        .where(
            IndicatorRelease.surprise.is_not(None),
            IndicatorRelease.released_at >= cutoff,
        )
        .order_by(
            func.abs(IndicatorRelease.surprise).desc(),
            desc(IndicatorRelease.released_at),
        )
        .limit(limit)
    )

    items: list[BiggestSurpriseItem] = []
    for indicator, release, country in surprises_q.all():
        if release.surprise is None:
            continue
        items.append(BiggestSurpriseItem(
            country_code=country.code,
            country_name=country.name,
            currency_code=country.currency_code,
            indicator_id=indicator.id,
            canonical_name=indicator.canonical_name,
            display_name=indicator.display_name,
            surprise=float(release.surprise),
            actual=float(release.actual) if release.actual is not None else None,
            estimate=float(release.estimate) if release.estimate is not None else None,
            released_at=release.released_at,
        ))
    return items


async def list_calendar_events(
    session: AsyncSession,
    *,
    days_back: int = 1,
    days_forward: int = 7,
    country_code: str | None = None,
    category: str | None = None,
    importance: int | None = None,
    limit: int = 200,
) -> list[EconomicCalendarEvent]:
    """Return calendar-ready releases from the ingested EODHD event stream."""
    start_at = _now() - timedelta(days=days_back)
    end_at = _now() + timedelta(days=days_forward)
    normalized_category = _normalize_category_filter(category)

    mapped_filters = [
        IndicatorRelease.released_at >= start_at,
        IndicatorRelease.released_at <= end_at,
        IndicatorRelease.indicator_id.is_not(None),
    ]
    if country_code:
        mapped_filters.append(Indicator.country_code == country_code.upper())
    if normalized_category:
        mapped_filters.append(Indicator.primary_category == normalized_category)
    if importance is not None:
        mapped_filters.append(Indicator.importance == importance)

    calendar_q = await session.execute(
        select(IndicatorRelease, Indicator, Country)
        .join(Indicator, Indicator.id == IndicatorRelease.indicator_id)
        .join(Country, Country.code == Indicator.country_code)
        .where(*mapped_filters)
        .order_by(
            asc(IndicatorRelease.released_at),
            asc(Indicator.importance),
            asc(Indicator.display_name),
        )
        .limit(limit)
    )

    events: list[EconomicCalendarEvent] = []
    now = _now()
    for release, indicator, country in calendar_q.all():
        events.append(EconomicCalendarEvent(
            release_id=release.id,
            indicator_id=indicator.id,
            country_code=country.code,
            country_name=country.name,
            currency_code=country.currency_code,
            canonical_name=indicator.canonical_name,
            display_name=indicator.display_name,
            primary_category=indicator.primary_category,
            importance=indicator.importance,
            period=release.period,
            released_at=release.released_at,
            status=(
                "released"
                if release.actual is not None
                else "upcoming" if release.released_at >= now else "pending"
            ),
            actual=float(release.actual) if release.actual is not None else None,
            estimate=float(release.estimate) if release.estimate is not None else None,
            previous=float(release.previous) if release.previous is not None else None,
            surprise=float(release.surprise) if release.surprise is not None else None,
            unit=indicator.unit,
            is_positive_when_higher=indicator.is_higher_better_for_currency,
        ))

    country_rows = await session.execute(select(Country))
    countries = {country.code: country for country in country_rows.scalars().all()}

    unmapped_filters = [
        IndicatorRelease.indicator_id.is_(None),
        IndicatorRelease.released_at >= start_at,
        IndicatorRelease.released_at <= end_at,
    ]
    if country_code:
        unmapped_filters.append(
            IndicatorRelease.raw_payload["country"].astext == country_code.upper()
        )

    unmapped_q = await session.execute(
        select(IndicatorRelease)
        .where(*unmapped_filters)
        .order_by(asc(IndicatorRelease.released_at), asc(IndicatorRelease.id))
        .limit(limit)
    )

    for release in unmapped_q.scalars().all():
        raw_country = (release.raw_payload or {}).get("country")
        country = countries.get(raw_country)
        if country is None:
            continue

        raw_type = (release.raw_payload or {}).get("type") or "Unmapped event"
        raw_comparison = (release.raw_payload or {}).get("comparison")
        display_name = raw_type if raw_comparison in (None, "") else f"{raw_type} ({str(raw_comparison).upper()})"
        inferred_category = _infer_calendar_category(raw_type)
        inferred_importance = _infer_calendar_importance(raw_type)
        inferred_direction = _infer_calendar_positive_when_higher(raw_type)

        if normalized_category and inferred_category != normalized_category:
            continue
        if importance is not None and inferred_importance != importance:
            continue

        events.append(EconomicCalendarEvent(
            release_id=release.id,
            indicator_id=None,
            country_code=country.code,
            country_name=country.name,
            currency_code=country.currency_code,
            canonical_name=None,
            display_name=display_name,
            primary_category=inferred_category,
            importance=inferred_importance,
            period=release.period,
            released_at=release.released_at,
            status=(
                "released"
                if release.actual is not None
                else "upcoming" if release.released_at >= now else "pending"
            ),
            actual=float(release.actual) if release.actual is not None else None,
            estimate=float(release.estimate) if release.estimate is not None else None,
            previous=float(release.previous) if release.previous is not None else None,
            surprise=float(release.surprise) if release.surprise is not None else None,
            unit=None,
            is_positive_when_higher=inferred_direction,
        ))

    deduped_events = _dedupe_calendar_events(events)
    return deduped_events[:limit]


def _infer_calendar_category(raw_type: str) -> str:
    normalized = raw_type.lower()
    if any(token in normalized for token in ["interest rate", "central bank", "fed", "ecb", "boj", "rba", "rbnz", "boc", "snb"]):
        return "Monetary Policy"
    if any(token in normalized for token in ["sentiment", "confidence", "expectations", "survey", "zew"]):
        return "Sentiment"
    if any(token in normalized for token in ["house", "housing", "property", "rightmove"]):
        return "Housing"
    if any(token in normalized for token in ["cpi", "inflation", "ppi", "price"]):
        return "Inflation"
    if any(token in normalized for token in ["employment", "earnings", "payroll", "jobless", "claims", "unemployment"]):
        return "Labor"
    if any(token in normalized for token in ["trade", "import", "export", "balance"]):
        return "Trade"
    return "Growth"


def _infer_calendar_importance(raw_type: str) -> int:
    normalized = raw_type.lower()
    if any(token in normalized for token in ["inflation", "cpi", "payroll", "unemployment", "interest rate"]):
        return 1
    if any(token in normalized for token in ["trade", "export", "import", "house", "housing", "price index"]):
        return 2
    return 3


def _infer_calendar_positive_when_higher(raw_type: str) -> bool | None:
    normalized = raw_type.lower()
    if any(token in normalized for token in ["unemployment", "jobless", "claimant", "claims"]):
        return False
    if any(token in normalized for token in [
        "cpi", "inflation", "ppi", "price", "payroll", "earnings",
        "employment", "pmi", "confidence", "sales", "gdp", "trade",
        "export", "import", "credit", "production",
    ]):
        return True
    return None


async def get_country(
    session: AsyncSession,
    country_code: str,
) -> Country | None:
    """Return a single tracked country by code."""
    country_q = await session.execute(
        select(Country).where(Country.code == country_code.upper())
    )
    return country_q.scalar_one_or_none()


async def get_indicator_by_country_and_name(
    session: AsyncSession,
    country_code: str,
    canonical_name: str,
) -> Indicator | None:
    """Look up an indicator by country + canonical name."""
    indicator_q = await session.execute(
        select(Indicator).where(
            Indicator.country_code == country_code.upper(),
            Indicator.canonical_name == canonical_name,
        )
    )
    return indicator_q.scalar_one_or_none()


def _release_sort_key(release: IndicatorRelease) -> tuple[date | None, datetime, int]:
    return (
        release.period_start_date or date.min,
        release.released_at,
        release.id,
    )


def _pick_revision_row(
    releases: list[IndicatorRelease],
    revision_mode: str,
    *,
    actual_only: bool = False,
) -> IndicatorRelease:
    eligible_releases = [
        release for release in releases
        if not actual_only or release.actual is not None
    ]
    if eligible_releases:
        releases = eligible_releases

    if revision_mode == "latest":
        latest_releases = [
            release for release in releases
            if release.is_latest
        ]
        latest = (
            sorted(
                latest_releases,
                key=lambda release: (
                    release.retrieved_at or release.released_at,
                    release.id,
                ),
                reverse=True,
            )[0]
            if latest_releases else None
        )
        if latest is not None:
            return latest
        return sorted(releases, key=lambda release: release.id, reverse=True)[0]

    # "as_reported" means earliest retrieved version for the same period.
    return sorted(
        releases,
        key=lambda release: (
            release.retrieved_at or release.released_at,
            release.id,
        ),
    )[0]


def _release_to_series_point(release: IndicatorRelease) -> IndicatorSeriesPoint:
    return IndicatorSeriesPoint(
        release_id=release.id,
        period=release.period,
        period_start_date=release.period_start_date,
        released_at=release.released_at,
        retrieved_at=release.retrieved_at,
        actual=float(release.actual) if release.actual is not None else None,
        previous=float(release.previous) if release.previous is not None else None,
        estimate=float(release.estimate) if release.estimate is not None else None,
        surprise=float(release.surprise) if release.surprise is not None else None,
        change=float(release.change) if release.change is not None else None,
        change_percentage=(
            float(release.change_percentage)
            if release.change_percentage is not None else None
        ),
        is_latest=release.is_latest,
    )


async def get_indicator_explorer_payload(
    session: AsyncSession,
    country_code: str,
    canonical_name: str,
    *,
    revision_mode: str = "latest",
    range_key: str = "all",
) -> IndicatorExplorerPayload | None:
    """Build the full indicator-detail payload for the Step 6 page."""
    country = await get_country(session, country_code)
    if country is None:
        return None

    indicator = await get_indicator_by_country_and_name(
        session, country.code, canonical_name
    )
    if indicator is None:
        return None

    releases_q = await session.execute(
        select(IndicatorRelease)
        .where(IndicatorRelease.indicator_id == indicator.id)
        .order_by(
            IndicatorRelease.period_start_date.desc().nullslast(),
            desc(IndicatorRelease.released_at),
            desc(IndicatorRelease.retrieved_at),
            desc(IndicatorRelease.id),
        )
    )
    release_rows = releases_q.scalars().all()

    grouped: dict[tuple[object, ...], list[IndicatorRelease]] = {}
    for release in release_rows:
        key = (
            release.period,
            release.period_start_date,
            release.released_at if release.period is None and release.period_start_date is None else None,
        )
        grouped.setdefault(key, []).append(release)

    picked_releases = [
        _pick_revision_row(group_releases, revision_mode, actual_only=True)
        for group_releases in grouped.values()
    ]
    picked_releases.sort(key=_release_sort_key)

    lookback_days = RANGE_LOOKBACK_DAYS[range_key]
    if lookback_days is not None:
        cutoff = _now() - timedelta(days=lookback_days)
        picked_releases = [
            release for release in picked_releases
            if release.released_at >= cutoff
        ]

    now = _now()
    actual_releases = [
        release for release in picked_releases
        if release.actual is not None and release.released_at <= now
    ]

    series = [_release_to_series_point(release) for release in actual_releases]
    recent_prints = list(reversed(series[-12:]))

    return IndicatorExplorerPayload(
        indicator=IndicatorMetadata(
            id=indicator.id,
            canonical_name=indicator.canonical_name,
            display_name=indicator.display_name,
            country_code=indicator.country_code,
            primary_category=indicator.primary_category,
            secondary_categories=list(indicator.secondary_categories or []),
            comparison=indicator.comparison,
            frequency=indicator.frequency,
            unit=indicator.unit,
            importance=indicator.importance,
            is_higher_better_for_currency=indicator.is_higher_better_for_currency,
            notes=indicator.notes,
        ),
        country=await _country_summary(session, country),
        revision_mode=revision_mode,
        range_key=range_key,
        total_points=len(series),
        cb_target=(
            float(country.cb_inflation_target)
            if country.cb_inflation_target is not None else None
        ),
        series=series,
        recent_prints=recent_prints,
    )


async def get_country_detail_payload(
    session: AsyncSession,
    country_code: str,
) -> CountryDetailPayload | None:
    """Shared helper for country detail data."""
    normalized_country_code = country_code.upper()
    country = await get_country(session, normalized_country_code)
    if country is None:
        return None

    release_count_q = await session.execute(
        select(func.count(IndicatorRelease.id))
        .select_from(IndicatorRelease)
        .join(Indicator, Indicator.id == IndicatorRelease.indicator_id)
        .where(Indicator.country_code == normalized_country_code)
    )
    release_count = release_count_q.scalar_one()

    indicators_q = await session.execute(
        select(Indicator)
        .where(Indicator.country_code == normalized_country_code)
        .order_by(
            Indicator.importance.asc(),
            Indicator.primary_category.asc(),
            Indicator.display_name.asc(),
        )
    )

    indicators = []
    for indicator in indicators_q.scalars().all():
        release = await get_latest_indicator_release(
            session,
            indicator.id,
            actual_only=True,
        )
        indicators.append(IndicatorSnapshot(
            id=indicator.id,
            canonical_name=indicator.canonical_name,
            display_name=indicator.display_name,
            primary_category=indicator.primary_category,
            secondary_categories=list(indicator.secondary_categories or []),
            comparison=indicator.comparison,
            frequency=indicator.frequency,
            unit=indicator.unit,
            importance=indicator.importance,
            is_higher_better_for_currency=indicator.is_higher_better_for_currency,
            latest_release=_release_to_schema(release),
        ))

    return CountryDetailPayload(
        country=await _country_summary(session, country),
        release_count=release_count,
        indicators=indicators,
    )


@router.get("/countries", response_model=Envelope[CountriesPayload])
async def list_countries(
    session: AsyncSession = Depends(get_session),
) -> Envelope[CountriesPayload]:
    """List all tracked countries with lightweight landing-page summaries."""
    payload = CountriesPayload(countries=await list_country_summaries(session))
    return Envelope(data=payload, meta=await _meta(session))


@router.get("/surprises", response_model=Envelope[BiggestSurprisesPayload])
async def get_biggest_surprises(
    days: int = Query(default=7, ge=1, le=30),
    limit: int = Query(default=5, ge=1, le=10),
    session: AsyncSession = Depends(get_session),
) -> Envelope[BiggestSurprisesPayload]:
    """Return the most significant recent surprises for landing-page use."""
    payload = BiggestSurprisesPayload(
        days=days,
        items=await list_biggest_surprises(session, days=days, limit=limit),
    )
    return Envelope(data=payload, meta=await _meta(session))


@router.get("/retail-sentiment")
async def retail_sentiment_snapshot(
    category: str = Query(default="all"),
    sort: str = Query(default="rank"),
    limit: int = Query(default=100, ge=1, le=300),
) -> dict[str, Any]:
    """Return merged retail positioning from Myfxbook, OANDA, Binance, and IG."""
    category = category.lower()
    sort = sort.lower()
    if category not in RETAIL_SENTIMENT_CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid retail sentiment category")
    if sort not in RETAIL_SENTIMENT_SORTS:
        raise HTTPException(status_code=400, detail="Invalid retail sentiment sort")
    return await get_retail_sentiment(category=category, sort=sort, limit=limit)


@router.get("/retail-sentiment/sources")
async def retail_sentiment_sources() -> dict[str, Any]:
    """Return source availability and cache state for retail sentiment feeds."""
    return source_status()


@router.post("/retail-sentiment/refresh")
async def retail_sentiment_refresh() -> dict[str, str]:
    """Force a retail sentiment cache refresh."""
    await refresh_retail_sentiment(force=True)
    return {"status": "ok", "message": "Retail sentiment refreshed"}


@router.post("/retail-sentiment/config/myfxbook")
async def retail_sentiment_config_myfxbook(payload: MyfxbookConfigRequest) -> dict[str, str]:
    """Authenticate Myfxbook for the current process and refresh sentiment data."""
    ok = await configure_myfxbook(payload.username.strip(), payload.password.strip())
    if not ok:
        raise HTTPException(status_code=401, detail="Myfxbook login failed")
    return {"status": "ok", "message": "Myfxbook authenticated"}


@router.get("/retail-sentiment/history/{symbol}")
async def retail_sentiment_history(
    symbol: str,
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    """Return persisted retail sentiment history for one symbol."""
    history = await load_history(symbol, days=days)
    return {"symbol": symbol.upper(), "days": days, "count": len(history), "history": history}


@router.get("/retail-sentiment/{symbol}")
async def retail_sentiment_symbol(symbol: str) -> dict[str, Any]:
    """Return one retail sentiment snapshot plus persisted history."""
    payload = await get_retail_sentiment_symbol(symbol)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Symbol '{symbol.upper()}' not found")
    return payload


@router.get("/calendar", response_model=Envelope[EconomicCalendarPayload])
async def get_economic_calendar(
    days_back: int = Query(default=1, ge=0, le=14),
    days_forward: int = Query(default=7, ge=1, le=30),
    country: str | None = Query(default=None, min_length=2, max_length=2),
    category: str | None = Query(
        default=None,
        pattern=(
            "^(Inflation|Growth|Labor|Monetary Policy|Trade|"
            "Sentiment|Housing|Other)$"
        ),
    ),
    importance: int | None = Query(default=None, ge=1, le=3),
    limit: int = Query(default=200, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> Envelope[EconomicCalendarPayload]:
    """Return upcoming and recent calendar events from ingested EODHD data."""
    events = await list_calendar_events(
        session,
        days_back=days_back,
        days_forward=days_forward,
        country_code=country,
        category=category,
        importance=importance,
        limit=limit,
    )
    payload = EconomicCalendarPayload(
        days_back=days_back,
        days_forward=days_forward,
        total_events=len(events),
        events=events,
    )
    return Envelope(data=payload, meta=await _meta(session))


@router.get("/cot")
async def get_cot_positioning(
    pair: str = Query(default="EURUSD", min_length=3, max_length=12),
) -> dict[str, Any]:
    """Return current and trailing CFTC COT positioning for a supported market."""
    pair_key = normalize_cot_pair(pair)
    if pair_key not in COT_PAIRS:
        raise HTTPException(status_code=404, detail="Unsupported COT pair.")
    try:
        return await get_cot_payload(pair_key)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail="CFTC COT download failed.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="CFTC COT data is unavailable.") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/cot/rows")
async def get_cot_rows() -> dict[str, Any]:
    """Return raw COT row history for all supported markets for the chart dashboard."""
    try:
        return await get_all_cot_rows()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail="CFTC COT download failed.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="CFTC COT data is unavailable.") from exc


@router.get("/news/feed")
async def get_news_feed(
    limit: int = Query(default=40, ge=1, le=80),
) -> dict[str, Any]:
    """Return normalized InvestingLive RSS headlines for the dashboard feed."""
    return {
        "source": "investinglive.com",
        "articles": await fetch_investinglive_articles(limit=limit),
        "fetched_at": _now().isoformat(),
    }


def _parse_cb_feed(xml_body: str, currency: str, bank_name: str, *, limit: int = 12) -> list[dict[str, Any]]:
    """Parse RSS/RDF/Atom feeds into normalized article rows."""
    try:
        root = ElementTree.fromstring(xml_body)
    except ElementTree.ParseError:
        return []

    articles: list[dict[str, Any]] = []
    is_atom = root.tag.endswith("feed")

    def child_text(node: ElementTree.Element, names: tuple[str, ...]) -> str:
        for child in node:
            local_name = child.tag.rsplit("}", 1)[-1]
            if local_name in names and child.text:
                return child.text.strip()
        return ""

    def first_link(node: ElementTree.Element) -> str:
        for child in node:
            if child.tag.rsplit("}", 1)[-1] != "link":
                continue
            href = child.get("href", "") or child.text or ""
            if href:
                return href.strip()
        about = node.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about", "")
        return about.strip()

    if is_atom:
        for entry in root.findall(".//{*}entry"):
            title = child_text(entry, ("title",)) or "Untitled"
            link = first_link(entry)
            if not link:
                continue
            description = _strip_html(child_text(entry, ("summary", "content", "description")))
            pub_date = child_text(entry, ("published", "updated", "date"))
            articles.append({"title": title, "link": link, "pubDate": pub_date, "description": description[:500], "currency": currency, "bank": bank_name})
            if len(articles) >= limit:
                break
    else:
        for item in root.findall(".//{*}item") or root.findall(".//item"):
            title = child_text(item, ("title",)) or "Untitled"
            link = first_link(item)
            if not link:
                continue
            description = _strip_html(child_text(item, ("description", "summary", "content")))
            pub_date = child_text(item, ("pubDate", "date", "dc:date"))
            articles.append({"title": title, "link": link, "pubDate": pub_date, "description": description[:500], "currency": currency, "bank": bank_name})
            if len(articles) >= limit:
                break

    return articles


def _parse_cb_listing(html_body: str, currency: str, bank_name: str, base_url: str, *, limit: int = 12) -> list[dict[str, Any]]:
    """Fallback parser for official central-bank listing pages when RSS is unavailable."""
    anchors = re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html_body, flags=re.IGNORECASE | re.DOTALL)
    articles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for href, raw_title in anchors:
        title = _strip_html(raw_title)
        if len(title) < 12:
            continue
        link = urljoin(base_url, html.unescape(href))
        if link in seen:
            continue
        title_l = title.lower()
        if not any(token in title_l for token in ("rate", "policy", "inflation", "speech", "statement", "release", "financial", "stability", "monetary", "bank")):
            continue
        seen.add(link)
        articles.append({"title": title[:220], "link": link, "pubDate": "", "description": "", "currency": currency, "bank": bank_name})
        if len(articles) >= limit:
            break
    return articles


async def _fetch_one_cb_feed(currency: str, now: datetime) -> dict[str, Any]:
    """Return cached or freshly fetched feed for one central bank."""
    config = CB_RSS_FEEDS[currency]
    cached = _cb_feed_cache.get(currency)
    if cached and (now - cached["fetched_at"]).total_seconds() < CB_FEED_CACHE_TTL:
        return cached

    articles: list[dict[str, Any]] = []
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        for url in config.get("urls", [config.get("url")]):
            if not url:
                continue
            try:
                response = await client.get(
                    url,
                    headers={"User-Agent": "MacroDashboard/1.0 (macro research dashboard)"},
                )
                response.raise_for_status()
                articles.extend(_parse_cb_feed(response.text, currency, config["name"]))
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}")
            if len(articles) >= 12:
                break

        if not articles and config.get("listing_url"):
            try:
                listing_url = str(config["listing_url"])
                response = await client.get(
                    listing_url,
                    headers={"User-Agent": "MacroDashboard/1.0 (macro research dashboard)"},
                )
                response.raise_for_status()
                articles = _parse_cb_listing(response.text, currency, config["name"], listing_url)
            except Exception as exc:
                errors.append(f"{config['listing_url']}: {type(exc).__name__}")

    result: dict[str, Any] = {
        "currency": currency,
        "name": config["name"],
        "articles": articles[:12],
        "fetched_at": now,
        "status": "ok" if articles else "empty",
        "error": "; ".join(errors[-2:]) if errors and not articles else "",
    }
    _cb_feed_cache[currency] = result
    return result


@router.get("/cb/feeds")
async def get_cb_feeds(bank: str | None = Query(default=None)) -> dict[str, Any]:
    """Fetch and cache RSS feeds from the eight main central banks."""
    now = _now()
    currencies = [bank.upper()] if bank and bank.upper() in CB_RSS_FEEDS else list(CB_RSS_FEEDS.keys())
    results = await asyncio.gather(*[_fetch_one_cb_feed(c, now) for c in currencies], return_exceptions=True)
    feeds = [
        {
            "currency": r["currency"],
            "name": r["name"],
            "articles": r["articles"],
            "fetchedAt": r["fetched_at"].isoformat(),
            "status": r.get("status", "ok"),
            "error": r.get("error", ""),
        }
        for r in results
        if isinstance(r, dict)
    ]
    return {"feeds": feeds, "generatedAt": now.isoformat()}


@router.post("/cb/analysis")
async def analyze_cb_feeds(bank: str | None = Query(default=None)) -> dict[str, Any]:
    """Run OpenAI analysis over cached CB feed articles for the selected bank(s)."""
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured.")

    now = _now()
    currencies = [bank.upper()] if bank and bank.upper() in CB_RSS_FEEDS else list(CB_RSS_FEEDS.keys())

    # Use cache where available; fetch missing banks
    missing = [c for c in currencies if c not in _cb_feed_cache]
    if missing:
        await asyncio.gather(*[_fetch_one_cb_feed(c, now) for c in missing], return_exceptions=True)

    snippets: list[str] = []
    for c in currencies:
        cached = _cb_feed_cache.get(c)
        if not cached:
            continue
        for article in cached.get("articles", [])[:3]:
            snippets.append(f"[{c}/{cached['name']}] {article['title']}: {article['description'][:200]}")

    if not snippets:
        raise HTTPException(status_code=404, detail="No CB feed articles available for analysis. Try refreshing feeds first.")

    bank_label = CB_RSS_FEEDS[bank.upper()]["name"] if bank and bank.upper() in CB_RSS_FEEDS else "G8 Central Banks"
    input_text = "\n\n".join(snippets[:18])

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                OPENAI_RESPONSES_URL,
                headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
                json={
                    "model": NEWS_SENTIMENT_MODEL,
                    "instructions": (
                        f"You are a senior macro analyst specialising in central bank policy. "
                        f"Analyse these recent communications from {bank_label}. "
                        "Return ONLY valid JSON, no markdown, no explanation. "
                        'Shape: {"stance":"hawkish"|"dovish"|"neutral","confidence":<0-100>,'
                        '"summary":"<3-4 sentence analysis of policy direction and market implications>",'
                        '"key_themes":["<theme1>","<theme2>","<theme3>"],'
                        '"fx_implication":"<1 sentence on expected currency impact>",'
                        '"rate_bias":"cut"|"hold"|"hike",'
                        '"risk_factors":["<risk1>","<risk2>"]}'
                    ),
                    "input": input_text,
                    "max_output_tokens": 900,
                },
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="OpenAI CB analysis failed.") from exc

    text = _extract_response_text(response.json())
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="OpenAI returned invalid analysis JSON.") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="OpenAI returned an invalid analysis payload.")

    return {
        "bank": bank_label,
        "stance": str(parsed.get("stance", "neutral")).lower(),
        "confidence": pct_from(parsed.get("confidence", 50)),
        "summary": str(parsed.get("summary", ""))[:1000],
        "key_themes": [str(t)[:60] for t in parsed.get("key_themes", []) if isinstance(t, str)][:5],
        "fx_implication": str(parsed.get("fx_implication", ""))[:250],
        "rate_bias": str(parsed.get("rate_bias", "hold")).lower(),
        "risk_factors": [str(r)[:100] for r in parsed.get("risk_factors", []) if isinstance(r, str)][:4],
        "generated_at": now.isoformat(),
    }


@router.post("/news/sentiment")
async def analyze_news_sentiment(
    request: NewsSentimentRequest,
) -> dict[str, Any]:
    """Analyze a selected headline server-side so the OpenAI key never reaches the browser."""
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured.")

    user_input = (
        f"Headline: {request.headline.strip()}\n\n"
        f"Body: {request.description.strip()}"
    )
    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            response = await client.post(
                OPENAI_RESPONSES_URL,
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": NEWS_SENTIMENT_MODEL,
                    "instructions": (
                        "You are a financial market analyst. Analyse the news headline and body "
                        "provided. Return ONLY valid JSON, no markdown fences, no explanation, "
                        "no preamble. Return this exact JSON shape: "
                        '{"sentiment":"bullish"|"bearish"|"neutral","confidence":<integer 0-100>,'
                        '"bearish_pct":<integer 0-100>,"neutral_pct":<integer 0-100>,'
                        '"bullish_pct":<integer 0-100>,'
                        '"summary":"<2-3 sentence plain-English trader summary>",'
                        '"assets":[{"symbol":"<e.g. DXY, EUR/USD, Gold, S&P 500>",'
                        '"impact":<integer 0-100>,"direction":"up"|"down"|"mixed",'
                        '"label":"<e.g. \\u2191 Bullish, \\u2193 Bearish, ~ Mixed>"}],'
                        '"themes":["<theme1>","<theme2>"]} '
                        "bearish_pct + neutral_pct + bullish_pct must sum to 100."
                    ),
                    "input": user_input,
                    "max_output_tokens": 1000,
                },
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = "OpenAI sentiment analysis failed."
        try:
            error_payload = exc.response.json()
            detail = error_payload.get("error", {}).get("message", detail)
        except ValueError:
            pass
        raise HTTPException(status_code=502, detail=detail) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="OpenAI sentiment analysis is unavailable.") from exc

    text = _extract_response_text(response.json())
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="OpenAI returned invalid sentiment JSON.") from exc

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="OpenAI returned an invalid sentiment payload.")
    return _normalize_sentiment_payload(parsed)


@router.get("/countries/{country_code}", response_model=Envelope[CountryDetailPayload])
async def get_country_detail(
    country_code: str,
    session: AsyncSession = Depends(get_session),
) -> Envelope[CountryDetailPayload]:
    """Return one country plus its indicators and current values."""
    payload = await get_country_detail_payload(session, country_code)
    if payload is None:
        raise HTTPException(status_code=404, detail="Country not found")
    return Envelope(data=payload, meta=await _meta(session))


@router.get("/indicators/{indicator_id}", response_model=Envelope[IndicatorDetailPayload])
async def get_indicator_detail(
    indicator_id: int,
    history_limit: int = Query(default=24, ge=1, le=240),
    session: AsyncSession = Depends(get_session),
) -> Envelope[IndicatorDetailPayload]:
    """Return indicator metadata plus the most recent release history."""
    indicator_q = await session.execute(
        select(Indicator).where(Indicator.id == indicator_id)
    )
    indicator = indicator_q.scalar_one_or_none()
    if indicator is None:
        raise HTTPException(status_code=404, detail="Indicator not found")

    total_releases_q = await session.execute(
        select(func.count(IndicatorRelease.id)).where(
            IndicatorRelease.indicator_id == indicator_id
        )
    )
    total_releases = total_releases_q.scalar_one()

    history_q = await session.execute(
        select(IndicatorRelease)
        .where(IndicatorRelease.indicator_id == indicator_id)
        .order_by(
            IndicatorRelease.period_start_date.desc().nullslast(),
            desc(IndicatorRelease.released_at),
            desc(IndicatorRelease.id),
        )
        .limit(history_limit)
    )
    history_rows = history_q.scalars().all()
    latest_release = next((row for row in history_rows if row.is_latest), None)
    if latest_release is None and history_rows:
        latest_release = history_rows[0]
    history = [
        release_schema
        for row in history_rows
        if (release_schema := _release_to_schema(row)) is not None
    ]

    payload = IndicatorDetailPayload(
        indicator=IndicatorMetadata(
            id=indicator.id,
            canonical_name=indicator.canonical_name,
            display_name=indicator.display_name,
            country_code=indicator.country_code,
            primary_category=indicator.primary_category,
            secondary_categories=list(indicator.secondary_categories or []),
            comparison=indicator.comparison,
            frequency=indicator.frequency,
            unit=indicator.unit,
            importance=indicator.importance,
            is_higher_better_for_currency=indicator.is_higher_better_for_currency,
            notes=indicator.notes,
        ),
        total_releases=total_releases,
        latest_release=_release_to_schema(latest_release),
        history=history,
    )
    return Envelope(data=payload, meta=await _meta(session))


@router.get(
    "/country/{country_code}/indicator/{canonical_name}",
    response_model=Envelope[IndicatorExplorerPayload],
)
async def get_indicator_explorer(
    country_code: str,
    canonical_name: str,
    revision_mode: str = Query(default="latest", pattern="^(latest|as_reported)$"),
    range_key: str = Query(default="all", pattern="^(1y|3y|5y|all)$"),
    session: AsyncSession = Depends(get_session),
) -> Envelope[IndicatorExplorerPayload]:
    """Return chart-ready indicator detail data for the Step 6 page."""
    payload = await get_indicator_explorer_payload(
        session,
        country_code,
        canonical_name,
        revision_mode=revision_mode,
        range_key=range_key,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="Indicator not found")
    return Envelope(data=payload, meta=await _meta(session))
