from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, MetaData, Numeric, Table, Text, text
from sqlalchemy.dialects.postgresql import ENUM, JSONB, insert as pg_insert

from news_pipeline.collectors import get_sessionmaker, raw_news

logger = logging.getLogger(__name__)

MARKET_CACHE_TTL_SECONDS = 5 * 60
RECENT_ENRICHED_LOOKBACK = timedelta(hours=72)
RECENT_ENRICHED_LIMIT = 5

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
REDIS_STREAM_NAME = "news:enriched"
MARKET_CLOSE_TZ = ZoneInfo("America/New_York")

PAIR_TO_YAHOO_TICKER = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "USDCHF": "CHF=X",
    "USDCAD": "CAD=X",
    "AUDUSD": "AUDUSD=X",
    "NZDUSD": "NZDUSD=X",
    "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
    "EURGBP": "EURGBP=X",
    "XAUUSD": "GC=F",
}

ENRICHMENT_SYSTEM_PROMPT = """
You are a macro analyst for a retail trader who trades gold (XAU/USD) primarily, 
using G10 FX and macro data as context. For every news item, reason through this 
chain explicitly before concluding:

1. RATE EXPECTATIONS: Does this news shift expected central bank policy path 
   (hawkish = higher expected rates, dovish = lower)? Which bank, which meeting.
2. ENERGY / INFLATION SHOCK: For wars, Middle East escalation, sanctions,
   shipping chokepoints, oil supply risk, or Houthi/Iran/US conflict headlines,
   explicitly assess whether higher oil/energy prices raise inflation
   expectations and therefore keep central banks hawkish. This channel can be
   bearish for gold if nominal yields or real yields rise faster than safe-haven
   demand. Do not assume geopolitical escalation is automatically gold bullish.
3. REAL YIELDS: Rate expectations minus inflation expectations = real yield 
   direction. Gold has a strong inverse relationship with real yields.
4. USD STRENGTH: Does this news move DXY? Note gold and DXY are usually 
   inversely correlated, but can decouple during strong risk-off events.
5. RISK SENTIMENT / SAFE HAVEN: Is this risk-on or risk-off? Risk-off tends 
   to support gold via safe-haven flows, independent of the rate/USD channel.
6. NET GOLD CALL: Weigh 1-5 against each other. State which channel dominates. 
   If channels conflict, say so explicitly rather than picking one arbitrarily.
   For oil-driven geopolitical shocks, compare safe-haven demand against
   inflation-expectation/rate-expectation pressure. If the headline implies
   higher oil prices and sticky inflation, consider net_direction "conflicting"
   or "bearish" unless market_context shows gold is actually responding bullishly.

Apply this signal hierarchy: geopolitical shock > risk sentiment > cb policy > 
economic data surprise > positioning > technicals.

Identify the primary country or region this news relates to (e.g. United States,
Eurozone, China, Middle East, Global). If multiple countries are involved, name
the most market-relevant one first.

CRITICAL: You must reason ONLY from the headline, body, and market_context
object provided in this exact request. Do not reference any other news event,
political figure, approval rating, geopolitical situation, or data point unless
it is explicitly present in the input text or market_context. If you are
uncertain whether something was mentioned, treat it as NOT mentioned.
Fabricating unstated context is a critical error. If the input is thin or
ambiguous, say so explicitly in the reasoning field (e.g. 'this headline alone
does not indicate a clear gold direction') and set conviction below 20, rather
than inventing supporting narrative.

Do not force a gold trade idea. If the item has no clear or material gold impact,
set gold_analysis.net_direction to "neutral", conviction below 20, dominant_channel
to "conflicting" when channels are unclear, and explain that the headline has no
direct XAU/USD signal. Do not create historical analogs for low/no-impact items;
use an empty string instead.

Output ONLY valid JSON matching the provided schema, no preamble.
"""

ENRICHMENT_JSON_SCHEMA = {
    "name": "gold_macro_analysis",
    "schema": {
        "type": "object",
        "properties": {
            "headline_summary": {"type": "string"},
            "country": {"type": "string", "minLength": 1},
            "tier": {"type": "string", "enum": ["geopolitical", "risk_sentiment", "cb_policy", "economic_data", "positioning", "technical"]},
            "surprise_factor": {"type": "string", "enum": ["high", "medium", "low"]},
            "currency_impact": {
                "type": "object",
                "properties": {
                    "usd": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
                    "affected_pairs": {"type": "array", "items": {"type": "string"}},
                    "mechanism": {"type": "string"}
                },
                "required": ["usd", "affected_pairs", "mechanism"]
            },
            "inflation_impact": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["higher", "lower", "unclear", "not_applicable"]},
                    "expected_vs_actual": {"type": "string", "enum": ["above", "below", "inline", "not_applicable"]},
                    "mechanism": {"type": "string"}
                },
                "required": ["direction", "expected_vs_actual", "mechanism"]
            },
            "employment_growth_impact": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["stronger", "weaker", "unclear", "not_applicable"]},
                    "expected_vs_actual": {"type": "string", "enum": ["above", "below", "inline", "not_applicable"]},
                    "fed_reaction_function": {"type": "string", "enum": ["hawkish", "dovish", "neutral"]},
                    "mechanism": {"type": "string"}
                },
                "required": ["direction", "expected_vs_actual", "fed_reaction_function", "mechanism"]
            },
            "gold_analysis": {
                "type": "object",
                "properties": {
                    "real_yield_direction": {"type": "string", "enum": ["up", "down", "unclear"]},
                    "usd_channel": {"type": "string", "enum": ["supportive", "unsupportive", "neutral"]},
                    "safe_haven_channel": {"type": "string", "enum": ["supportive", "unsupportive", "neutral"]},
                    "dominant_channel": {"type": "string", "enum": ["real_yields", "usd", "safe_haven", "conflicting"]},
                    "net_direction": {"type": "string", "enum": ["bullish", "bearish", "neutral", "conflicting"]},
                    "conviction": {"type": "integer"},
                    "reasoning": {"type": "string"},
                    "time_horizon": {"type": "string", "enum": ["immediate", "session", "multi-day"]}
                },
                "required": ["real_yield_direction", "usd_channel", "safe_haven_channel", "dominant_channel", "net_direction", "conviction", "reasoning", "time_horizon"]
            },
            "historical_analog": {"type": "string"},
            "invalidation": {"type": "string"},
            "confidence": {"type": "integer"}
        },
        "required": ["headline_summary", "country", "tier", "surprise_factor", "currency_impact", "inflation_impact", "employment_growth_impact", "gold_analysis", "historical_analog", "invalidation", "confidence"]
    }
}

metadata = MetaData()

enriched_news = Table(
    "enriched_news",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("raw_news_id", BigInteger, nullable=False),
    Column("tier", Text, nullable=False),
    Column("country", Text, nullable=True),
    Column("surprise_factor", Text, nullable=True),
    Column("currency_impact", JSONB, nullable=False),
    Column("inflation_impact", JSONB, nullable=False),
    Column("employment_growth_impact", JSONB, nullable=False),
    Column("gold_analysis", JSONB, nullable=False),
    Column("historical_analog", Text, nullable=True),
    Column("invalidation", Text, nullable=True),
    Column("confidence", Integer, nullable=True),
    Column("market_context", JSONB, nullable=False),
    Column("contains_unverified_entity", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

price_snapshots = Table(
    "price_snapshots",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("enriched_news_id", BigInteger, nullable=False),
    Column("instrument", Text, nullable=False),
    Column(
        "snapshot_type",
        ENUM(
            "immediate",
            "15m",
            "1h",
            "4h",
            "eod",
            name="price_snapshot_type",
            create_type=False,
        ),
        nullable=False,
    ),
    Column("scheduled_for", DateTime(timezone=True), nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column("price", Numeric(20, 6), nullable=False),
    Column("price_change_from_immediate_pct", Numeric(10, 4), nullable=True),
)

# Extend the lightweight raw_news table imported from collectors with columns
# needed by enrichment queries. SQLAlchemy keeps this metadata additive.
if "id" not in raw_news.c:
    raw_news.append_column(Column("id", BigInteger, primary_key=True))
if "is_gated_relevant" not in raw_news.c:
    raw_news.append_column(Column("is_gated_relevant", Boolean, nullable=False))


@dataclass
class CacheEntry:
    value: dict[str, Any]
    expires_at: datetime


_market_cache: dict[str, CacheEntry] = {}
_price_snapshot_scheduler: AsyncIOScheduler | None = None
_poll_and_enrich_lock = asyncio.Lock()


async def enrich_raw_news(raw_news_id: int) -> dict[str, Any]:
    row = await _load_raw_news(raw_news_id)
    if row is None:
        raise ValueError(f"raw_news id={raw_news_id} not found")
    if not row["is_gated_relevant"]:
        raise ValueError(f"raw_news id={raw_news_id} did not pass the filter gate")

    matched_categories = _matched_categories(row["raw_payload"])
    market_context = await build_market_context(matched_categories)
    analysis = await _call_llm_with_retry(row, matched_categories, market_context)
    enriched_id = await _insert_enriched_news(row, analysis, market_context)
    await _schedule_price_snapshots_for_analysis(enriched_id, analysis)
    payload = _build_stream_payload(enriched_id, row, matched_categories, analysis, market_context)
    await _publish_enriched(payload)
    return payload


async def poll_and_enrich() -> None:
    if _poll_and_enrich_lock.locked():
        logger.info("Skipping enrichment poll because the previous run is still active")
        return

    async with _poll_and_enrich_lock:
        raw_news_ids = await _load_pending_raw_news_ids(limit=5)
        if not raw_news_ids:
            logger.debug("No gated raw_news rows pending enrichment")
            return

        logger.info("Enrichment poll found %s pending raw_news rows", len(raw_news_ids))
        for raw_news_id in raw_news_ids:
            try:
                payload = await enrich_raw_news(raw_news_id)
                logger.info(
                    "Enriched raw_news_id=%s into enriched_news_id=%s",
                    raw_news_id,
                    payload.get("enriched_news_id"),
                )
            except Exception as exc:
                logger.warning(
                    "Failed to enrich raw_news_id=%s; continuing with next item: %s",
                    raw_news_id,
                    exc,
                    exc_info=True,
                )


def start_price_snapshot_scheduler() -> None:
    global _price_snapshot_scheduler

    if _price_snapshot_scheduler and _price_snapshot_scheduler.running:
        return

    _price_snapshot_scheduler = AsyncIOScheduler(
        jobstores={
            "default": SQLAlchemyJobStore(
                url=_sync_database_url(),
                tablename="apscheduler_jobs",
            )
        },
        timezone="UTC",
    )
    _price_snapshot_scheduler.start()
    logger.info("Price snapshot scheduler started with PostgreSQL job store")


def shutdown_price_snapshot_scheduler() -> None:
    global _price_snapshot_scheduler

    if _price_snapshot_scheduler and _price_snapshot_scheduler.running:
        _price_snapshot_scheduler.shutdown(wait=False)
        logger.info("Price snapshot scheduler stopped")
    _price_snapshot_scheduler = None


async def schedule_price_snapshots(enriched_news_id: int, instrument: str = "GC=F") -> None:
    scheduler = _get_price_snapshot_scheduler()
    now = datetime.now(UTC)
    await capture_price_snapshot(enriched_news_id, instrument, "immediate", now)

    for snapshot_type, run_date in _future_snapshot_schedule(now):
        scheduler.add_job(
            capture_price_snapshot,
            trigger="date",
            run_date=run_date,
            id=_price_snapshot_job_id(enriched_news_id, instrument, snapshot_type),
            replace_existing=True,
            kwargs={
                "enriched_news_id": enriched_news_id,
                "instrument": instrument,
                "snapshot_type": snapshot_type,
                "scheduled_for": run_date,
            },
        )
        logger.info(
            "Scheduled %s price snapshot for enriched_news_id=%s instrument=%s at %s",
            snapshot_type,
            enriched_news_id,
            instrument,
            run_date.isoformat(),
        )


async def capture_price_snapshot(
    enriched_news_id: int,
    instrument: str,
    snapshot_type: str,
    scheduled_for: datetime,
) -> None:
    captured_at = datetime.now(UTC)
    price = await _fetch_current_price(instrument)
    immediate_price = None
    if snapshot_type != "immediate":
        immediate_price = await _load_immediate_price(enriched_news_id, instrument)
    change_pct = _pct_change(price, immediate_price) if immediate_price else None

    maker = get_sessionmaker()
    async with maker() as session:
        async with session.begin():
            await session.execute(
                pg_insert(price_snapshots).values(
                    enriched_news_id=enriched_news_id,
                    instrument=instrument,
                    snapshot_type=snapshot_type,
                    scheduled_for=scheduled_for,
                    captured_at=captured_at,
                    price=price,
                    price_change_from_immediate_pct=change_pct,
                )
            )


async def build_market_context(matched_categories: list[str]) -> dict[str, Any]:
    dxy_task = _cached_market_value("dxy", _fetch_dxy)
    real_yield_task = _cached_market_value("real_yield_10y", _fetch_real_yield_10y)
    gold_task = _safe_market_value("gold", _fetch_gold)
    vix_task = _cached_market_value("vix", _fetch_vix)
    recent_task = _fetch_recent_enriched(matched_categories)

    dxy, real_yield_10y, gold, vix, recent_items = await asyncio.gather(
        dxy_task,
        real_yield_task,
        gold_task,
        vix_task,
        recent_task,
    )
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "dxy": dxy,
        "real_yield_10y": real_yield_10y,
        "gold": gold,
        "vix": vix,
        "recent_related_enriched_news": recent_items,
    }


async def _cached_market_value(key: str, fetcher: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    cached = _market_cache.get(key)
    if cached and cached.expires_at > now:
        return cached.value

    value = await _safe_market_value(key, fetcher)
    _market_cache[key] = CacheEntry(
        value=value,
        expires_at=now + timedelta(seconds=MARKET_CACHE_TTL_SECONDS),
    )
    return value


async def _safe_market_value(key: str, fetcher: Any) -> dict[str, Any]:
    try:
        return await fetcher()
    except Exception as exc:
        logger.warning("Market context fetch failed for %s: %s", key, exc)
        return {
            "source": key,
            "status": "unavailable",
            "error": str(exc),
            "as_of": datetime.now(UTC).isoformat(),
        }


async def _fetch_dxy() -> dict[str, Any]:
    return await _fetch_yahoo_summary("DX-Y.NYB")


async def _fetch_gold() -> dict[str, Any]:
    summary = await _fetch_yahoo_summary("GC=F", range_="1mo", interval="1d")
    closes = summary.pop("closes", [])
    recent_closes = closes[-20:]
    if recent_closes:
        high_20d = max(recent_closes)
        low_20d = min(recent_closes)
        level = summary.get("level")
        summary["distance_from_20d_high_pct"] = _pct_change(level, high_20d)
        summary["distance_from_20d_low_pct"] = _pct_change(level, low_20d)
        summary["high_20d"] = high_20d
        summary["low_20d"] = low_20d
    return summary


async def _fetch_vix() -> dict[str, Any]:
    return await _fetch_yahoo_summary("^VIX")


async def _fetch_current_price(symbol: str) -> float:
    summary = await _fetch_yahoo_summary(symbol, range_="1d", interval="1m")
    level = summary.get("level")
    if not isinstance(level, (int, float)):
        raise ValueError(f"No current price returned for {symbol}")
    return float(level)


async def _fetch_yahoo_summary(
    symbol: str,
    *,
    range_: str = "7d",
    interval: str = "1d",
) -> dict[str, Any]:
    encoded_symbol = quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}"
    params = {"range": range_, "interval": interval}
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
    result = response.json()["chart"]["result"][0]
    quote_data = result["indicators"]["quote"][0]
    closes = [float(value) for value in quote_data.get("close", []) if value is not None]
    if not closes:
        raise ValueError(f"No close data returned for {symbol}")

    level = closes[-1]
    previous = closes[-2] if len(closes) >= 2 else None
    five_day_prior = closes[-6] if len(closes) >= 6 else None
    return {
        "source": "yahoo_chart",
        "symbol": symbol,
        "level": level,
        "change_1d_pct": _pct_change(level, previous),
        "change_5d_pct": _pct_change(level, five_day_prior),
        "as_of_epoch": result.get("timestamp", [None])[-1] if result.get("timestamp") else None,
        "closes": closes,
    }


async def _fetch_real_yield_10y() -> dict[str, Any]:
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv"
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        response = await client.get(url, params={"id": "DFII10"})
        response.raise_for_status()

    rows = list(csv.DictReader(StringIO(response.text)))
    observations = [
        (row["observation_date"], float(row["DFII10"]))
        for row in rows
        if row.get("DFII10") not in (None, "", ".")
    ]
    if not observations:
        raise ValueError("No DFII10 observations returned from FRED")

    latest_date, level = observations[-1]
    previous = observations[-2][1] if len(observations) >= 2 else None
    five_day_prior = observations[-6][1] if len(observations) >= 6 else None
    return {
        "source": "fred_public_csv",
        "series": "DFII10",
        "date": latest_date,
        "level": level,
        "change_1d": _difference(level, previous),
        "change_5d": _difference(level, five_day_prior),
    }


async def _fetch_recent_enriched(matched_categories: list[str]) -> list[dict[str, Any]]:
    if not matched_categories:
        return []

    maker = get_sessionmaker()
    since = datetime.now(UTC) - RECENT_ENRICHED_LOOKBACK
    async with maker() as session:
        result = await session.execute(
            text(
                """
                SELECT
                    e.id,
                    e.raw_news_id,
                    e.tier,
                    e.confidence,
                    e.gold_analysis,
                    e.created_at,
                    r.title,
                    r.url,
                    r.raw_payload -> 'filter_matched_categories' AS matched_categories
                FROM enriched_news e
                JOIN raw_news r ON r.id = e.raw_news_id
                WHERE e.created_at >= :since
                  AND EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(
                        COALESCE(r.raw_payload -> 'filter_matched_categories', '[]'::jsonb)
                    ) AS category(value)
                    WHERE category.value = ANY(:matched_categories)
                  )
                ORDER BY e.created_at DESC
                LIMIT :limit
                """
            ),
            {
                "since": since,
                "matched_categories": matched_categories,
                "limit": RECENT_ENRICHED_LIMIT,
            },
        )
        rows = result.mappings().all()

    return [_json_safe(dict(row)) for row in rows]


async def _load_raw_news(raw_news_id: int) -> dict[str, Any] | None:
    maker = get_sessionmaker()
    async with maker() as session:
        result = await session.execute(
            text(
                """
                SELECT id, source, title, body, url, published_at, raw_payload, is_gated_relevant
                FROM raw_news
                WHERE id = :raw_news_id
                """
            ),
            {"raw_news_id": raw_news_id},
        )
        row = result.mappings().first()
    return dict(row) if row else None


async def _load_pending_raw_news_ids(limit: int) -> list[int]:
    maker = get_sessionmaker()
    async with maker() as session:
        result = await session.execute(
            text(
                """
                SELECT r.id
                FROM raw_news r
                WHERE r.is_gated_relevant = true
                  AND NOT EXISTS (
                    SELECT 1
                    FROM enriched_news e
                    WHERE e.raw_news_id = r.id
                  )
                ORDER BY r.published_at ASC, r.id ASC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )
        return [int(value) for value in result.scalars().all()]


async def _call_llm_with_retry(
    row: dict[str, Any],
    matched_categories: list[str],
    market_context: dict[str, Any],
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            analysis = await _call_llm(row, matched_categories, market_context, attempt=attempt)
            _validate_analysis(analysis)
            return analysis
        except Exception as exc:
            last_error = exc
            logger.warning("LLM enrichment attempt %s failed for raw_news id=%s: %s", attempt + 1, row["id"], exc)

    raise ValueError(f"LLM enrichment failed after retry: {last_error}")


async def _call_llm(
    row: dict[str, Any],
    matched_categories: list[str],
    market_context: dict[str, Any],
    *,
    attempt: int,
) -> dict[str, Any]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    user_payload = {
        "headline": row["title"],
        "body": row.get("body") or "",
        "matched_categories": matched_categories,
        "market_context": market_context,
    }
    prompt = ENRICHMENT_SYSTEM_PROMPT
    if attempt:
        prompt += "\nThe previous response was invalid. Return only valid JSON with every required field."

    response = await client.responses.create(
        model=OPENAI_MODEL,
        instructions=prompt,
        input=json.dumps(user_payload, ensure_ascii=False),
        text={
            "format": {
                "type": "json_schema",
                "name": ENRICHMENT_JSON_SCHEMA["name"],
                "schema": ENRICHMENT_JSON_SCHEMA["schema"],
                "strict": False,
            }
        },
    )
    output_text = response.output_text
    return json.loads(output_text)


def _validate_analysis(analysis: dict[str, Any]) -> None:
    required = ENRICHMENT_JSON_SCHEMA["schema"]["required"]
    missing = [field for field in required if field not in analysis]
    if missing:
        raise ValueError(f"Missing required enrichment fields: {', '.join(missing)}")
    if not str(analysis.get("country") or "").strip():
        raise ValueError("Missing required enrichment field: country")


def _contains_unverified_entity(
    analysis: dict[str, Any],
    row: dict[str, Any],
    market_context: dict[str, Any],
) -> bool:
    input_text = " ".join(
        [
            str(row.get("title") or ""),
            str(row.get("body") or ""),
            json.dumps(market_context, ensure_ascii=False, default=str),
        ]
    ).lower()
    output_text = " ".join(_analysis_audit_texts(analysis))

    for entity in _extract_proper_noun_candidates(output_text):
        if entity.lower() not in input_text:
            logger.warning(
                "Enrichment output contains unverified entity for raw_news_id=%s: %s",
                row.get("id"),
                entity,
            )
            return True
    return False


def _analysis_audit_texts(analysis: dict[str, Any]) -> list[str]:
    texts = [
        str((analysis.get("currency_impact") or {}).get("mechanism") or ""),
        str((analysis.get("inflation_impact") or {}).get("mechanism") or ""),
        str((analysis.get("employment_growth_impact") or {}).get("mechanism") or ""),
        str((analysis.get("gold_analysis") or {}).get("reasoning") or ""),
        str(analysis.get("historical_analog") or ""),
    ]
    return [text for text in texts if text]


def _extract_proper_noun_candidates(text_value: str) -> list[str]:
    candidates = re.findall(
        r"\b(?:[A-Z][a-z]+|[A-Z]{2,})(?:[\s\-]+(?:[A-Z][a-z]+|[A-Z]{2,}))*\b",
        text_value,
    )
    ignored = {
        "A",
        "An",
        "As",
        "Current",
        "Fed",
        "Gold",
        "If",
        "In",
        "Investors",
        "Market",
        "Markets",
        "No",
        "Similar",
        "The",
        "This",
        "USD",
        "XAU",
        "XAU USD",
    }
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = " ".join(candidate.replace("-", " ").split())
        if normalized in ignored or len(normalized) < 3:
            continue
        key = normalized.lower()
        if key not in seen:
            seen.add(key)
            unique.append(normalized)
    return unique


async def _insert_enriched_news(
    row: dict[str, Any],
    analysis: dict[str, Any],
    market_context: dict[str, Any],
) -> int:
    maker = get_sessionmaker()
    contains_unverified_entity = _contains_unverified_entity(analysis, row, market_context)
    values = {
        "raw_news_id": row["id"],
        "tier": analysis["tier"],
        "country": analysis.get("country"),
        "surprise_factor": analysis.get("surprise_factor"),
        "currency_impact": analysis.get("currency_impact") or {},
        "inflation_impact": analysis.get("inflation_impact") or {},
        "employment_growth_impact": analysis.get("employment_growth_impact") or {},
        "gold_analysis": analysis.get("gold_analysis") or {},
        "historical_analog": analysis.get("historical_analog"),
        "invalidation": analysis.get("invalidation"),
        "confidence": analysis.get("confidence"),
        "market_context": market_context,
        "contains_unverified_entity": contains_unverified_entity,
        "created_at": datetime.now(UTC),
    }
    async with maker() as session:
        async with session.begin():
            result = await session.execute(
                pg_insert(enriched_news).values(**values).returning(enriched_news.c.id)
            )
            enriched_id = result.scalar_one()
    return int(enriched_id)


async def _schedule_price_snapshots_for_analysis(
    enriched_news_id: int,
    analysis: dict[str, Any],
) -> None:
    instruments = _snapshot_instruments(analysis)
    for instrument in instruments:
        try:
            await schedule_price_snapshots(enriched_news_id, instrument)
        except Exception as exc:
            logger.warning(
                "Failed to schedule price snapshots for enriched_news_id=%s instrument=%s: %s",
                enriched_news_id,
                instrument,
                exc,
            )


def _snapshot_instruments(analysis: dict[str, Any]) -> list[str]:
    instruments = ["GC=F"]
    affected_pairs = (analysis.get("currency_impact") or {}).get("affected_pairs") or []
    for pair in affected_pairs:
        ticker = _pair_to_yahoo_ticker(str(pair))
        if ticker and ticker not in instruments:
            instruments.append(ticker)
    return instruments


def _pair_to_yahoo_ticker(pair: str) -> str | None:
    normalized = pair.upper().replace("/", "").replace("-", "").replace("_", "").strip()
    if normalized.endswith("=X") or normalized.endswith("=F"):
        return pair.upper()
    return PAIR_TO_YAHOO_TICKER.get(normalized)


def _future_snapshot_schedule(now: datetime) -> list[tuple[str, datetime]]:
    return [
        ("15m", now + timedelta(minutes=15)),
        ("1h", now + timedelta(hours=1)),
        ("4h", now + timedelta(hours=4)),
        ("eod", _next_market_close(now)),
    ]


def _next_market_close(now: datetime) -> datetime:
    eastern_now = now.astimezone(MARKET_CLOSE_TZ)
    close = eastern_now.replace(hour=17, minute=0, second=0, microsecond=0)
    if eastern_now >= close:
        close += timedelta(days=1)
    return close.astimezone(UTC)


def _get_price_snapshot_scheduler() -> AsyncIOScheduler:
    if _price_snapshot_scheduler is None or not _price_snapshot_scheduler.running:
        start_price_snapshot_scheduler()
    if _price_snapshot_scheduler is None:
        raise RuntimeError("Price snapshot scheduler failed to start")
    return _price_snapshot_scheduler


def _price_snapshot_job_id(
    enriched_news_id: int,
    instrument: str,
    snapshot_type: str,
) -> str:
    safe_instrument = instrument.replace("=", "").replace("/", "").replace(":", "")
    return f"price_snapshot:{enriched_news_id}:{safe_instrument}:{snapshot_type}"


async def _load_immediate_price(enriched_news_id: int, instrument: str) -> float | None:
    maker = get_sessionmaker()
    async with maker() as session:
        result = await session.execute(
            text(
                """
                SELECT price
                FROM price_snapshots
                WHERE enriched_news_id = :enriched_news_id
                  AND instrument = :instrument
                  AND snapshot_type = 'immediate'
                ORDER BY captured_at ASC
                LIMIT 1
                """
            ),
            {"enriched_news_id": enriched_news_id, "instrument": instrument},
        )
        value = result.scalar_one_or_none()
    return float(value) if value is not None else None


def _sync_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for price snapshot job store")
    return (
        database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
        .replace("postgresql://", "postgresql+psycopg2://", 1)
    )


async def _publish_enriched(payload: dict[str, Any]) -> None:
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise RuntimeError("REDIS_URL is required to publish enriched news")

    from redis.asyncio import Redis

    redis = Redis.from_url(redis_url, decode_responses=True)
    try:
        await redis.xadd(
            REDIS_STREAM_NAME,
            {
                "raw_news_id": str(payload["raw_news_id"]),
                "enriched_news_id": str(payload["enriched_news_id"]),
                "headline": payload["headline"],
                "payload": json.dumps(payload, ensure_ascii=False, default=str),
            },
        )
    finally:
        await redis.aclose()


def _build_stream_payload(
    enriched_id: int,
    row: dict[str, Any],
    matched_categories: list[str],
    analysis: dict[str, Any],
    market_context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "enriched_news_id": enriched_id,
        "raw_news_id": row["id"],
        "headline": row["title"],
        "body": row.get("body") or "",
        "url": row.get("url"),
        "source": row.get("source"),
        "released_at": _json_safe(row.get("published_at")),
        "matched_categories": matched_categories,
        "market_context": market_context,
        "analysis": analysis,
        "published_at": _json_safe(row.get("published_at")),
    }


def _matched_categories(raw_payload: dict[str, Any] | None) -> list[str]:
    if not raw_payload:
        return []
    value = raw_payload.get("filter_matched_categories", [])
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return ((current - previous) / previous) * 100


def _difference(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return current - previous


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
