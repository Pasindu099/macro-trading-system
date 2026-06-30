"""Public API routes for the frontend-facing dashboard experience."""

from __future__ import annotations

import asyncio
import html
import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, asc, desc, func, select, text
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
from app.db.models import (
    Country, CbPolicyReport, CbEconomicProjection, CbPolicyDocument,
    Indicator, IndicatorRelease, IngestionRun,
)
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
DB_SESSION = Depends(get_session)
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

CB_FEED_CACHE_TTL = 600  # 10 minutes
CB_RSS_FEEDS: dict[str, dict[str, Any]] = {
    "USD": {
        "name": "Fed",
        "urls": [
            "https://www.federalreserve.gov/feeds/press_all.xml",
            "https://www.federalreserve.gov/feeds/speeches.xml",
            # FOMC statements, minutes, Monetary Policy Report (semi-annual)
            "https://www.federalreserve.gov/feeds/press_monetary.xml",
        ],
        "members": ["Powell", "Jefferson", "Waller", "Cook", "Kugler", "Barr",
                    "Williams", "Daly", "Kashkari", "Bostic", "Barkin", "Goolsbee",
                    "Musalem", "Schmid", "Collins", "Logan"],
    },
    "EUR": {
        "name": "ECB",
        "urls": [
            "https://www.ecb.europa.eu/rss/press.html",
            "https://www.ecb.europa.eu/rss/speech.html",
            # ECB Economic Bulletin (published every 6 weeks)
            "https://www.ecb.europa.eu/rss/publications.html",
        ],
        "members": ["Lagarde", "de Guindos", "Lane", "Schnabel", "Cipollone",
                    "Nagel", "Villeroy", "Wunsch", "Knot", "Centeno",
                    "Holzmann", "Kazimir", "Muller", "Vasle", "Simkus",
                    "Stournaras", "Rehn", "Herodotou"],
    },
    "GBP": {
        "name": "BoE",
        "urls": [
            "https://www.bankofengland.co.uk/rss/news",
            "https://www.bankofengland.co.uk/rss/speeches",
            # Monetary Policy Report, MPC minutes, Financial Stability Report
            "https://www.bankofengland.co.uk/rss/publications",
        ],
        "members": ["Bailey", "Broadbent", "Ramsden", "Mann", "Haskel",
                    "Greene", "Dhingra", "Taylor", "Lombardelli"],
    },
    "JPY": {
        "name": "BoJ",
        "urls": [
            "https://www.boj.or.jp/rss/whatsnew.xml",
            "https://www.boj.or.jp/rss/speech.xml",
            # Outlook for Economic Activity and Prices (quarterly)
            "https://www.boj.or.jp/rss/release.xml",
        ],
        "listing_url": "https://www.boj.or.jp/en/whatsnew/",
        "members": ["Ueda", "Himino", "Uchida", "Tamura", "Nakamura",
                    "Noguchi", "Adachi", "Takata", "Nakagawa"],
    },
    "AUD": {
        "name": "RBA",
        "urls": [
            "https://www.rba.gov.au/rss/rss-cb-media-releases.xml",
            "https://www.rba.gov.au/rss/rss-cb-speeches.xml",
            # Statement on Monetary Policy (quarterly)
            "https://www.rba.gov.au/rss/rss-cb-publications.xml",
        ],
        "listing_url": "https://www.rba.gov.au/media-releases/",
        "members": ["Bullock", "Hauser", "Hunter", "Kohler", "Jones",
                    "Hu", "Kent", "Schwartz"],
    },
    "CAD": {
        "name": "BoC",
        "urls": [
            "https://www.bankofcanada.ca/feed/",
            "https://www.bankofcanada.ca/publications/speeches/feed/",
            # Monetary Policy Report (quarterly) + Summary of Deliberations
            "https://www.bankofcanada.ca/publications/mpr/feed/",
        ],
        "members": ["Macklem", "Rogers", "Gravelle", "Kozicki", "Mendes", "Selody"],
    },
    "CHF": {
        "name": "SNB",
        "urls": [
            "https://www.snb.ch/public/en/rss/news",
            "https://www.snb.ch/public/en/rss/news/publications/media-releases",
            "https://www.snb.ch/public/en/rss/news/publications/speeches",
        ],
        "listing_url": "https://www.snb.ch/en/news-publications/news",
        "members": ["Schlegel", "Tschannen", "Danthine"],
    },
    "NZD": {
        "name": "RBNZ",
        "urls": [
            "https://www.rbnz.govt.nz/hub/news/rss",
            "https://www.rbnz.govt.nz/hub/speeches/rss",
            # Monetary Policy Statement (quarterly)
            "https://www.rbnz.govt.nz/hub/publications/monetary-policy-statement/rss",
        ],
        "listing_url": "https://www.rbnz.govt.nz/news-and-events/news",
        "members": ["Orr", "Hawkesby", "Silk", "Conway", "Ranchhod"],
    },
}

# Keywords that identify a monetary policy report / decision document
# (vs a regular press release or speech)
_POLICY_REPORT_KEYWORDS: frozenset[str] = frozenset([
    "monetary policy report", "monetary policy statement", "mpr",
    "fomc minutes", "fomc statement", "mpc minutes", "mpc statement",
    "summary of deliberations", "summary of economic projections",
    "economic bulletin", "economic outlook", "outlook for economic activity",
    "statement on monetary policy", "inflation report",
    "rate decision", "interest rate decision", "policy rate",
    "policy assessment", "monetary policy decision",
    "beige book", "financial stability report",
    "minutes of the", "record of the monetary policy",
])
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
    return datetime.now(UTC)


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
        if (
            re.fullmatch(r"[A-Z][a-z]{2}", normalized_period)
            or re.fullmatch(r"Q[1-4](?:[ /-]\d{2,4})?", normalized_period)
            or re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized_period)
        ):
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
    session: AsyncSession = DB_SESSION,
) -> Envelope[CountriesPayload]:
    """List all tracked countries with lightweight landing-page summaries."""
    payload = CountriesPayload(countries=await list_country_summaries(session))
    return Envelope(data=payload, meta=await _meta(session))


@router.get("/surprises", response_model=Envelope[BiggestSurprisesPayload])
async def get_biggest_surprises(
    days: int = Query(default=7, ge=1, le=30),
    limit: int = Query(default=5, ge=1, le=10),
    session: AsyncSession = DB_SESSION,
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


@router.get("/intelligence/alerts/latest", response_class=HTMLResponse)
async def latest_intelligence_alerts(
    session: AsyncSession = DB_SESSION,
) -> HTMLResponse:
    """Return the latest surfaced news alert banner fragment for HTMX polling."""
    result = await session.execute(
        text(
            """
            SELECT
                alert_text,
                severity,
                affected_currencies,
                detected_at
            FROM intelligence.news_alerts
            WHERE was_surfaced = true
              AND detected_at >= NOW() - interval '30 minutes'
            ORDER BY detected_at DESC
            LIMIT 3
            """
        )
    )
    alerts = [dict(row) for row in result.mappings().all()]
    return HTMLResponse(content=_render_news_alert_banner(alerts), media_type="text/html")


def _render_news_alert_banner(alerts: list[dict[str, Any]]) -> str:
    if not alerts:
        return '<div id="news-alert-banner"></div>'

    bars = []
    has_critical = False
    now = datetime.now(UTC)
    for alert in alerts:
        severity = str(alert.get("severity") or "").upper()
        has_critical = has_critical or severity == "CRITICAL"
        detected_at = _as_utc_datetime(alert.get("detected_at")) or now
        minutes_ago = max(0, int((now - detected_at).total_seconds() // 60))
        currencies = " ".join(str(code) for code in alert.get("affected_currencies") or [])
        bars.append(
            "\n".join(
                [
                    f'<div class="news-alert-bar news-alert-{html.escape(severity.lower())}">',
                    f'  <span class="news-alert-label">{html.escape(severity)}</span>',
                    "  <span class=\"news-alert-text\">"
                    f"{html.escape(str(alert.get('alert_text') or ''))}</span>",
                    "  <span class=\"news-alert-currencies\">"
                    f"{html.escape(currencies)}</span>",
                    f'  <span class="news-alert-time">{minutes_ago}m ago</span>',
                    '  <button class="news-alert-dismiss" '
                    'onclick="this.parentElement.parentElement.remove()">✕</button>',
                    "</div>",
                ]
            )
        )

    sound_trigger = (
        '<div id="news-alert-sound-trigger" data-critical="true"></div>'
        if has_critical
        else ""
    )
    return "\n".join(
        [
            '<div id="news-alert-banner">',
            *bars,
            sound_trigger,
            "</div>",
        ]
    )


def _as_utc_datetime(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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
    session: AsyncSession = DB_SESSION,
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
    limit: int = Query(default=40, ge=1, le=100),
    session: AsyncSession = DB_SESSION,
) -> dict[str, Any]:
    """Return scored intelligence monitor headlines for the dashboard feed."""
    result = await session.execute(
        text(
            """
            SELECT
                headline,
                source,
                url,
                detected_at,
                relevance_score,
                severity,
                affected_currencies,
                implied_tier,
                alert_text
            FROM intelligence.news_alerts
            WHERE detected_at >= NOW() - INTERVAL '24 hours'
            ORDER BY detected_at DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    articles = [
        _news_alert_row_to_article(row)
        for row in result.mappings().all()
    ]
    return {
        "source": "intelligence_monitor",
        "articles": articles,
        "fetched_at": _now().isoformat(),
    }


def _news_alert_row_to_article(row: Any) -> dict[str, Any]:
    title = str(row.get("headline") or "Untitled headline")
    detected_at = _as_utc_datetime(row.get("detected_at")) or _now()
    description = str(row.get("alert_text") or title)
    return {
        "title": title,
        "link": str(row.get("url") or ""),
        "pubDate": detected_at.isoformat(),
        "description": description,
        "category": _news_alert_category(row.get("implied_tier"), row.get("source")),
        "source": str(row.get("source") or ""),
        "score": int(row.get("relevance_score") or 0),
        "severity": str(row.get("severity") or ""),
        "currencies": list(row.get("affected_currencies") or []),
    }


def _news_alert_category(implied_tier: Any, source: Any) -> str:
    tier_map = {
        "GEOPOLITICAL_SHOCK": "Geopolitical",
        "RISK_SENTIMENT": "Macro",
        "CB_POLICY_DIVERGENCE": "Central banks",
        "ECONOMIC_DATA": "Macro",
        "POSITIONING": "FX",
        "TECHNICAL": "FX",
    }
    tier = str(implied_tier or "").upper()
    if tier in tier_map:
        return tier_map[tier]

    normalized_source = str(source or "").casefold()
    if any(
        token in normalized_source
        for token in ("financialjuice.com", "financial juice", "forexlive.com", "forexlive", "investing.com", "investinglive")
    ):
        return "FX"
    if any(
        token in normalized_source
        for token in ("reuters.com", "reuters", "apnews.com", "ap news", "bbc.co.uk", "bbc", "aljazeera.com", "al jazeera")
    ):
        return "Geopolitical"
    return "Macro"


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

    members: list[str] = CB_RSS_FEEDS.get(currency, {}).get("members", [])

    def tag_speaker(title: str, description: str) -> str | None:
        text_blob = f"{title} {description}".lower()
        for member in members:
            if member.lower() in text_blob:
                return member
        return None

    def tag_policy_report(title: str, description: str) -> bool:
        text_blob = f"{title} {description}".lower()
        return any(kw in text_blob for kw in _POLICY_REPORT_KEYWORDS)

    if is_atom:
        for entry in root.findall(".//{*}entry"):
            title = child_text(entry, ("title",)) or "Untitled"
            link = first_link(entry)
            if not link:
                continue
            description = _strip_html(child_text(entry, ("summary", "content", "description")))
            pub_date = child_text(entry, ("published", "updated", "date"))
            speaker = tag_speaker(title, description)
            articles.append({
                "title": title, "link": link, "pubDate": pub_date,
                "description": description[:500], "currency": currency,
                "bank": bank_name, "speaker": speaker,
                "is_speech": speaker is not None or "speech" in title.lower(),
                "is_policy_report": tag_policy_report(title, description),
            })
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
            speaker = tag_speaker(title, description)
            articles.append({
                "title": title, "link": link, "pubDate": pub_date,
                "description": description[:500], "currency": currency,
                "bank": bank_name, "speaker": speaker,
                "is_speech": speaker is not None or "speech" in title.lower(),
                "is_policy_report": tag_policy_report(title, description),
            })
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
    session: AsyncSession = DB_SESSION,
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
    session: AsyncSession = DB_SESSION,
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
    session: AsyncSession = DB_SESSION,
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


# ── CB Policy Tracker endpoints ───────────────────────────────────────────────

@router.post("/cb/policy-reports/refresh")
async def refresh_cb_policy_reports(
    bank: str | None = Query(default=None, description="Specific bank code or omit for all"),
    reanalyze: bool = Query(default=False, description="Re-run AI on already-analyzed rows"),
    session: AsyncSession = DB_SESSION,
) -> dict[str, Any]:
    """Scrape and AI-analyze CB policy statements, storing results in cb_policy_reports.

    This endpoint can take several minutes on first run (8 banks × ~7 statements each).
    Call it from a background task or a manual refresh button.
    """
    from app.processing.cb_policy_scraper import scrape_all_banks, scrape_bank
    from app.processing.cb_policy_analyzer import run_full_pipeline

    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured.")

    try:
        if bank:
            records = await scrape_bank(bank.upper())
        else:
            records = await scrape_all_banks(lookback_months=12)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Scraping failed: {exc}") from exc

    counts = await run_full_pipeline(session, records, reanalyze=reanalyze)
    return {
        "bank": bank or "all",
        "scraped": len(records),
        **counts,
        "generated_at": _now().isoformat(),
    }


@router.get("/cb/policy-reports")
async def get_cb_policy_reports(
    bank: str | None = Query(default=None),
    session: AsyncSession = DB_SESSION,
) -> dict[str, Any]:
    """Return analyzed CB policy reports for all (or one) bank(s)."""
    from datetime import date as _date, timedelta
    cutoff = _date.today() - timedelta(days=13 * 31)

    q = select(CbPolicyReport).where(
        CbPolicyReport.meeting_date >= cutoff,
        CbPolicyReport.analyzed_at.is_not(None),
    ).order_by(CbPolicyReport.bank.asc(), CbPolicyReport.meeting_date.desc())

    if bank:
        q = q.where(CbPolicyReport.bank == bank.upper())

    rows = (await session.execute(q)).scalars().all()

    return {
        "reports": [
            {
                "bank": r.bank,
                "meeting_date": r.meeting_date.isoformat(),
                "report_type": r.report_type,
                "source_url": r.source_url,
                "pdf_url": f"/api/cb/policy-reports/{r.bank}/{r.meeting_date.isoformat()}/pdf",
                "tone_score": float(r.tone_score) if r.tone_score is not None else None,
                "tone_label": r.tone_label,
                "inflation_outlook": r.inflation_outlook,
                "inflation_summary": r.inflation_summary,
                "growth_outlook": r.growth_outlook,
                "growth_summary": r.growth_summary,
                "labor_outlook": r.labor_outlook,
                "labor_summary": r.labor_summary,
                "key_phrases": r.key_phrases or [],
                "tone_change_vs_prior": r.tone_change_vs_prior,
                "retail_bullets": r.retail_bullets or [],
                "analyzed_at": r.analyzed_at.isoformat() if r.analyzed_at else None,
            }
            for r in rows
        ],
        "count": len(rows),
        "generated_at": _now().isoformat(),
    }


@router.get("/cb/policy-reports/{bank}/{meeting_date}/pdf")
async def get_cb_policy_report_pdf(
    bank: str,
    meeting_date: str,
    session: AsyncSession = DB_SESSION,
) -> Response:
    """Serve the actual CB-published PDF for a policy report.

    PDFs are downloaded from the central bank website during the analysis
    pipeline and cached to disk. Only the last 2 PDFs per bank are kept.
    If the PDF is not cached, redirects to the source_pdf_url when available,
    or falls back to the HTML source page.
    """
    from datetime import date as _date
    from fastapi.responses import Response as _Response, RedirectResponse
    from app.processing.cb_policy_analyzer import pdf_path

    try:
        mtg_date = _date.fromisoformat(meeting_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format — use YYYY-MM-DD")

    bank = bank.upper()

    # Serve cached file if available
    cached = pdf_path(bank, mtg_date)
    if cached.exists():
        return _Response(
            content=cached.read_bytes(),
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{bank}_{meeting_date}.pdf"'},
        )

    # Not cached — redirect to the CB's own PDF URL or HTML page
    report = await session.scalar(
        select(CbPolicyReport).where(
            CbPolicyReport.bank == bank,
            CbPolicyReport.meeting_date == mtg_date,
        )
    )
    if report is None:
        raise HTTPException(status_code=404, detail="No report found for this bank and date")

    redirect_url = report.source_pdf_url or report.source_url
    if not redirect_url:
        raise HTTPException(status_code=404, detail="No PDF or source URL available for this report")

    return RedirectResponse(url=redirect_url, status_code=302)


# ── CB Document ingestion endpoints ───────────────────────────────────────────

@router.post("/cb/documents/ingest")
async def ingest_cb_documents(
    bank: str | None = Query(default=None),
    reanalyze: bool = Query(default=False),
    session: AsyncSession = DB_SESSION,
) -> dict[str, Any]:
    """Scan data/policy/ and process PDFs from manually downloaded files."""
    from app.processing.cb_document_ingester import ingest_documents

    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured.")

    counts = await ingest_documents(
        session,
        bank=bank.upper() if bank else None,
        reanalyze=reanalyze,
    )
    return {"status": "ok", **counts}


@router.post("/cb/documents/upload")
async def upload_cb_document(
    bank: str = Form(...),
    doc_date: str = Form(...),  # YYYY-MM-DD
    doc_type: str = Form(default="upload"),
    file: UploadFile = File(...),
    session: AsyncSession = DB_SESSION,
) -> dict[str, Any]:
    """Upload a PDF and run AI analysis on it."""
    from datetime import date as _date
    from app.processing.cb_document_ingester import ingest_uploaded_file

    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured.")

    try:
        parsed_date = _date.fromisoformat(doc_date)
    except ValueError:
        raise HTTPException(400, "Invalid date — use YYYY-MM-DD")

    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    file_bytes = await file.read()
    doc = await ingest_uploaded_file(
        session,
        bank=bank.upper(),
        doc_date=parsed_date,
        doc_type=doc_type,
        filename=file.filename or f"{bank.upper()}_{doc_date}.pdf",
        file_bytes=file_bytes,
    )
    return {
        "status": "ok",
        "bank": doc.bank,
        "doc_date": doc.doc_date.isoformat(),
        "doc_type": doc.doc_type,
        "tone_score": float(doc.tone_score) if doc.tone_score else None,
        "tone_label": doc.tone_label,
        "analyzed": doc.analyzed_at is not None,
    }


@router.get("/cb/projections")
async def get_cb_projections(
    bank: str | None = Query(default=None),
    session: AsyncSession = DB_SESSION,
) -> dict[str, Any]:
    """Return all economic projections, optionally filtered by bank."""
    q = select(CbEconomicProjection).order_by(
        CbEconomicProjection.bank.asc(),
        CbEconomicProjection.projection_date.asc(),
        CbEconomicProjection.horizon_year.asc(),
    )
    if bank:
        q = q.where(CbEconomicProjection.bank == bank.upper())

    rows = (await session.execute(q)).scalars().all()
    return {
        "projections": [
            {
                "bank": r.bank,
                "projection_date": r.projection_date.isoformat(),
                "horizon_label": r.horizon_label,
                "horizon_year": r.horizon_year,
                "inflation_forecast": float(r.inflation_forecast) if r.inflation_forecast else None,
                "gdp_forecast": float(r.gdp_forecast) if r.gdp_forecast else None,
                "unemployment_forecast": float(r.unemployment_forecast) if r.unemployment_forecast else None,
            }
            for r in rows
        ],
        "count": len(rows),
    }


@router.get("/cb/projections/comparison")
async def get_cb_projection_comparison(
    session: AsyncSession = DB_SESSION,
) -> dict[str, Any]:
    """Return latest CB projections for current/next year vs actual indicator values."""
    from datetime import date as _date

    # Bank → indicator canonical_name + country_code mappings
    BANK_INDICATOR_MAP: dict[str, dict[str, tuple[str, str] | None]] = {
        "FED":  {
            "inflation": ("cpi_headline_yoy", "US"),
            "gdp": ("gdp_qoq", "US"),
            "unemployment": ("unemployment_rate", "US"),
        },
        "ECB":  {
            "inflation": ("cpi_headline_yoy", "EU"),
            "gdp": ("gdp_qoq", "EU"),
            "unemployment": ("unemployment_rate", "EU"),
        },
        "BOE":  {
            "inflation": ("cpi_headline_yoy", "UK"),
            "gdp": ("gdp_qoq", "UK"),
            "unemployment": ("unemployment_rate", "UK"),
        },
        "BOJ":  {
            "inflation": ("cpi_headline_yoy", "JP"),
            "gdp": ("gdp_qoq", "JP"),
            "unemployment": ("unemployment_rate", "JP"),
        },
        "RBA":  {
            "inflation": ("cpi_headline_yoy", "AU"),
            "gdp": None,
            "unemployment": ("unemployment_rate", "AU"),
        },
        "BOC":  {
            "inflation": ("cpi_headline_yoy", "CA"),
            "gdp": ("gdp_mom", "CA"),
            "unemployment": ("unemployment_rate", "CA"),
        },
        "SNB":  {
            "inflation": ("cpi_headline_yoy", "CH"),
            "gdp": ("gdp_qoq", "CH"),
            "unemployment": ("unemployment_rate", "CH"),
        },
        "RBNZ": {
            "inflation": ("cpi_headline_qoq", "NZ"),
            "gdp": None,
            "unemployment": ("unemployment_rate", "NZ"),
        },
    }

    current_year = _date.today().year
    next_year = current_year + 1

    # Get the latest projection per bank for current/next year
    # We do this by loading all projections and filtering in Python
    proj_rows = (await session.execute(
        select(CbEconomicProjection)
        .where(CbEconomicProjection.horizon_year.in_([current_year, next_year]))
        .order_by(
            CbEconomicProjection.bank.asc(),
            CbEconomicProjection.projection_date.desc(),
        )
    )).scalars().all()

    # Group by (bank, horizon_year) — take the most recent projection_date
    latest_by_bank_year: dict[tuple[str, int], CbEconomicProjection] = {}
    for row in proj_rows:
        if row.horizon_year is None:
            continue
        key = (row.bank, row.horizon_year)
        if key not in latest_by_bank_year:
            latest_by_bank_year[key] = row

    # Cache indicator actuals
    indicator_cache: dict[tuple[str, str], float | None] = {}

    async def _get_actual(canonical_name: str, country_code: str) -> float | None:
        key = (canonical_name, country_code)
        if key in indicator_cache:
            return indicator_cache[key]

        ind_q = await session.execute(
            select(Indicator).where(
                Indicator.canonical_name == canonical_name,
                Indicator.country_code == country_code,
            )
        )
        ind = ind_q.scalar_one_or_none()
        if ind is None:
            indicator_cache[key] = None
            return None

        rel_q = await session.execute(
            select(IndicatorRelease)
            .where(
                IndicatorRelease.indicator_id == ind.id,
                IndicatorRelease.actual.is_not(None),
            )
            .order_by(
                IndicatorRelease.period_start_date.desc().nullslast(),
                IndicatorRelease.released_at.desc(),
            )
            .limit(1)
        )
        rel = rel_q.scalar_one_or_none()
        value = float(rel.actual) if rel and rel.actual is not None else None
        indicator_cache[key] = value
        return value

    comparison_rows: list[dict[str, Any]] = []

    for (bank_code, horizon_year), proj in sorted(latest_by_bank_year.items()):
        bank_map = BANK_INDICATOR_MAP.get(bank_code, {})

        # Inflation
        if proj.inflation_forecast is not None and bank_map.get("inflation"):
            canon, country = bank_map["inflation"]  # type: ignore[misc]
            actual = await _get_actual(canon, country)
            if actual is not None:
                projected = float(proj.inflation_forecast)
                deviation = actual - projected
                deviation_pct = (deviation / projected * 100) if projected else None
                comparison_rows.append({
                    "bank": bank_code,
                    "metric": "Inflation",
                    "projection_date": proj.projection_date.isoformat(),
                    "horizon": str(horizon_year),
                    "projected_value": projected,
                    "actual_value": actual,
                    "deviation": round(deviation, 3),
                    "deviation_pct": round(deviation_pct, 1) if deviation_pct is not None else None,
                })

        # GDP
        if proj.gdp_forecast is not None and bank_map.get("gdp"):
            canon, country = bank_map["gdp"]  # type: ignore[misc]
            actual = await _get_actual(canon, country)
            if actual is not None:
                projected = float(proj.gdp_forecast)
                deviation = actual - projected
                deviation_pct = (deviation / projected * 100) if projected else None
                comparison_rows.append({
                    "bank": bank_code,
                    "metric": "GDP",
                    "projection_date": proj.projection_date.isoformat(),
                    "horizon": str(horizon_year),
                    "projected_value": projected,
                    "actual_value": actual,
                    "deviation": round(deviation, 3),
                    "deviation_pct": round(deviation_pct, 1) if deviation_pct is not None else None,
                })

        # Unemployment
        if proj.unemployment_forecast is not None and bank_map.get("unemployment"):
            canon, country = bank_map["unemployment"]  # type: ignore[misc]
            actual = await _get_actual(canon, country)
            if actual is not None:
                projected = float(proj.unemployment_forecast)
                deviation = actual - projected
                deviation_pct = (deviation / projected * 100) if projected else None
                comparison_rows.append({
                    "bank": bank_code,
                    "metric": "Unemployment",
                    "projection_date": proj.projection_date.isoformat(),
                    "horizon": str(horizon_year),
                    "projected_value": projected,
                    "actual_value": actual,
                    "deviation": round(deviation, 3),
                    "deviation_pct": round(deviation_pct, 1) if deviation_pct is not None else None,
                })

    return {
        "comparison": comparison_rows,
        "count": len(comparison_rows),
        "generated_at": _now().isoformat(),
    }


# ── CB Policy Divergence Comparison ───────────────────────────────────────────

_BANK_CURRENCY = {
    "FED": "USD", "ECB": "EUR", "BOE": "GBP", "BOJ": "JPY",
    "RBA": "AUD", "BOC": "CAD", "SNB": "CHF", "RBNZ": "NZD",
}

_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"


async def _compare_openai(doc_a: dict[str, Any], doc_b: dict[str, Any], settings: Any) -> dict[str, Any]:
    """Call OpenAI to compare two CB policy documents and identify divergences."""
    prompt = f"""You are a senior FX macro analyst. Compare these two central bank policy analyses and identify monetary policy divergences that matter for FX trading.

BANK A — {doc_a['bank']} ({doc_a['currency']}) | Latest document: {doc_a['doc_date']} | Type: {doc_a['doc_type']}
Tone: {doc_a['tone_label']} (score: {doc_a['tone_score']})
Inflation outlook: {doc_a['inflation_outlook']}
Inflation summary: {doc_a['inflation_summary']}
Growth outlook: {doc_a['growth_outlook']}
Growth summary: {doc_a['growth_summary']}
Labor outlook: {doc_a['labor_outlook']}
Labor summary: {doc_a['labor_summary']}
Key phrases: {', '.join(doc_a['key_phrases'] or [])}
Tone vs prior: {doc_a['tone_change_vs_prior']}

BANK B — {doc_b['bank']} ({doc_b['currency']}) | Latest document: {doc_b['doc_date']} | Type: {doc_b['doc_type']}
Tone: {doc_b['tone_label']} (score: {doc_b['tone_score']})
Inflation outlook: {doc_b['inflation_outlook']}
Inflation summary: {doc_b['inflation_summary']}
Growth outlook: {doc_b['growth_outlook']}
Growth summary: {doc_b['growth_summary']}
Labor outlook: {doc_b['labor_outlook']}
Labor summary: {doc_b['labor_summary']}
Key phrases: {', '.join(doc_b['key_phrases'] or [])}
Tone vs prior: {doc_b['tone_change_vs_prior']}

Return a JSON object with exactly this structure:
{{
  "overall_divergence": "mild" | "moderate" | "significant",
  "divergence_direction": "{doc_a['bank']} more hawkish" | "{doc_b['bank']} more hawkish" | "broadly aligned",
  "summary": "<2-3 sentence macro summary of the policy gap>",
  "key_divergences": [
    {{
      "topic": "<e.g. Rate trajectory / Inflation stance / Growth concern / Labor market>",
      "bank_a": "<brief stance for {doc_a['bank']}>",
      "bank_b": "<brief stance for {doc_b['bank']}>",
      "significance": "low" | "medium" | "high"
    }}
  ],
  "fx_pair": "{doc_a['currency']}/{doc_b['currency']}",
  "fx_bias": "<e.g. Bullish {doc_a['currency']}/{doc_b['currency']}>",
  "trading_implications": ["<bullet 1>", "<bullet 2>", "<bullet 3>"],
  "risk_factors": ["<key risk to the divergence thesis>"]
}}

Focus on rate path expectations, inflation tolerance, and growth concerns. Be concise and actionable."""

    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(
            _OPENAI_CHAT_URL,
            headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
            json={
                "model": settings.openai_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]
        return json.loads(raw)


@router.post("/cb/compare")
async def compare_cb_policies(
    bank_a: str = Query(..., description="First bank code e.g. FED"),
    bank_b: str = Query(..., description="Second bank code e.g. ECB"),
    session: AsyncSession = DB_SESSION,
) -> dict[str, Any]:
    """Compare the latest monetary policy documents from two central banks using AI."""
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured.")

    bank_a = bank_a.upper().strip()
    bank_b = bank_b.upper().strip()

    if bank_a == bank_b:
        raise HTTPException(status_code=400, detail="Select two different banks.")
    if bank_a not in _BANK_CURRENCY or bank_b not in _BANK_CURRENCY:
        raise HTTPException(status_code=400, detail=f"Unknown bank code. Valid: {list(_BANK_CURRENCY)}")

    # Fetch the most-recent analyzed statement/report for each bank
    async def _fetch_latest(bank: str) -> CbPolicyDocument | None:
        row = (await session.execute(
            select(CbPolicyDocument)
            .where(
                CbPolicyDocument.bank == bank,
                CbPolicyDocument.analyzed_at.is_not(None),
                CbPolicyDocument.doc_type.in_(["statement", "report", "upload"]),
            )
            .order_by(CbPolicyDocument.doc_date.desc())
            .limit(1)
        )).scalar_one_or_none()
        return row

    doc_a_row, doc_b_row = await asyncio.gather(_fetch_latest(bank_a), _fetch_latest(bank_b))

    if doc_a_row is None:
        raise HTTPException(status_code=404, detail=f"No analyzed documents found for {bank_a}. Upload and ingest first.")
    if doc_b_row is None:
        raise HTTPException(status_code=404, detail=f"No analyzed documents found for {bank_b}. Upload and ingest first.")

    def _doc_dict(row: CbPolicyDocument, bank: str) -> dict[str, Any]:
        return {
            "bank": bank,
            "currency": _BANK_CURRENCY[bank],
            "doc_date": row.doc_date.isoformat(),
            "doc_type": row.doc_type,
            "tone_score": float(row.tone_score) if row.tone_score is not None else 0.0,
            "tone_label": row.tone_label or "neutral",
            "inflation_outlook": row.inflation_outlook or "unknown",
            "inflation_summary": row.inflation_summary or "",
            "growth_outlook": row.growth_outlook or "unknown",
            "growth_summary": row.growth_summary or "",
            "labor_outlook": row.labor_outlook or "unknown",
            "labor_summary": row.labor_summary or "",
            "key_phrases": row.key_phrases or [],
            "tone_change_vs_prior": row.tone_change_vs_prior or "no change",
        }

    doc_a = _doc_dict(doc_a_row, bank_a)
    doc_b = _doc_dict(doc_b_row, bank_b)

    analysis = await _compare_openai(doc_a, doc_b, settings)

    return {
        "bank_a": doc_a,
        "bank_b": doc_b,
        "analysis": analysis,
        "generated_at": _now().isoformat(),
    }
