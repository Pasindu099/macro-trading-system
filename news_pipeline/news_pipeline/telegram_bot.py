from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from news_pipeline.collectors import get_sessionmaker

logger = logging.getLogger(__name__)

REDIS_STREAM_NAME = "news:enriched"
REDIS_LAST_ID_KEY = "telegram:news_enriched:last_id"
MACRO_FLASH_STREAM_NAME = "news:macro_flash"
MACRO_FLASH_LAST_ID_KEY = "telegram:macro_flash:last_id"
DISPLAY_TIMEZONE = ZoneInfo("Europe/Berlin")

# Replace these values after creating Telegram topics, or set
# TELEGRAM_TOPIC_THREAD_IDS to a JSON object with the same keys.
TOPIC_THREAD_IDS: dict[str, int | None] = {
    "central_bank": None,
    "geopolitical": None,
    "economic_data": None,
    "energy": None,
    "politics": None,
    "risk_sentiment": None,
}

TIER_EMOJI = {
    "geopolitical": "🌍",
    "risk_sentiment": "⚠️",
    "cb_policy": "🏦",
    "economic_data": "📊",
    "positioning": "📈",
    "technical": "📉",
}

SURPRISE_EMOJI = {"high": "🔴", "medium": "🟡", "low": "⚪"}

DIRECTION_EMOJI = {
    "bullish": "▲", "bearish": "▼", "neutral": "◆", "conflicting": "⚡",
    "stronger": "▲", "weaker": "▼", "higher": "▲", "lower": "▼",
    "hawkish": "▲", "dovish": "▼", "unclear": "◆", "not_applicable": "—",
}


def load_topic_thread_ids() -> dict[str, int | None]:
    raw = os.getenv("TELEGRAM_TOPIC_THREAD_IDS")
    if not raw:
        return TOPIC_THREAD_IDS

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Invalid TELEGRAM_TOPIC_THREAD_IDS JSON; using defaults: %s", exc)
        return TOPIC_THREAD_IDS

    topic_ids = TOPIC_THREAD_IDS.copy()
    for tier, message_thread_id in parsed.items():
        if tier in topic_ids and message_thread_id is not None:
            topic_ids[tier] = int(message_thread_id)
    return topic_ids


def conviction_bar(score: int) -> str:
    filled = round(score / 20)
    return "●" * filled + "○" * (5 - filled)


def _legacy_mojibake_format_telegram_message(a: dict[str, Any]) -> str:
    ci = a["currency_impact"]
    ii = a["inflation_impact"]
    eg = a["employment_growth_impact"]
    g = a["gold_analysis"]

    tier_icon = TIER_EMOJI.get(a["tier"], "📰")
    surprise_icon = SURPRISE_EMOJI.get(a["surprise_factor"], "⚪")

    lines = []
    lines.append(
        f"{tier_icon} *{_md(a['tier'].upper().replace('_', ' '))}* "
        f"{surprise_icon} surprise: {_md(a['surprise_factor'])}"
    )
    lines.append(
        f"ðŸŒ {_md(str(a.get('country') or 'Global'))} | "
        f"ðŸ• {_md(_format_released_at(a.get('released_at')))} | "
        f"ðŸ“¡ {_md(str(a.get('source') or 'unknown'))}"
    )
    lines.append(f"*{_md(a['headline_summary'])}*")
    lines.append("")

    usd_icon = DIRECTION_EMOJI.get(ci["usd"], "◆")
    lines.append(f"💵 *Currency* {usd_icon} USD: {_md(ci['usd'])}")
    lines.append(f"{_md(ci['mechanism'])}")
    if ci.get("affected_pairs"):
        lines.append(f"Pairs: {_md(', '.join(ci['affected_pairs']))}")
    lines.append("")

    if ii.get("direction") != "not_applicable":
        inf_icon = DIRECTION_EMOJI.get(ii["direction"], "◆")
        lines.append(
            f"📈 *Inflation* {inf_icon} {_md(ii['direction'])} "
            f"\\(vs\\. expected: {_md(ii['expected_vs_actual'])}\\)"
        )
        lines.append(f"{_md(ii['mechanism'])}")
        lines.append("")

    if eg.get("direction") != "not_applicable":
        eg_icon = DIRECTION_EMOJI.get(eg["direction"], "◆")
        fed_icon = DIRECTION_EMOJI.get(eg["fed_reaction_function"], "◆")
        lines.append(
            f"👷 *Employment / growth* {eg_icon} {_md(eg['direction'])} "
            f"\\(vs\\. expected: {_md(eg['expected_vs_actual'])}\\)"
        )
        lines.append(f"Fed reaction: {fed_icon} {_md(eg['fed_reaction_function'])}")
        lines.append(f"{_md(eg['mechanism'])}")
        lines.append("")

    gold_icon = DIRECTION_EMOJI.get(g["net_direction"], "◆")
    lines.append(f"🥇 *Gold XAU/USD* {gold_icon} {_md(g['net_direction'].upper())}")
    lines.append(
        f"Conviction: {conviction_bar(g['conviction'])} {g['conviction']}/100 "
        f"| ⏱ {_md(g['time_horizon'])}"
    )
    lines.append(
        f"Channel: {_md(g['dominant_channel'])} "
        f"\\(yields {DIRECTION_EMOJI.get(g['real_yield_direction'], '◆')} "
        f"· USD {_md(g['usd_channel'])} · safe\\-haven {_md(g['safe_haven_channel'])}\\)"
    )
    lines.append(f"{_md(g['reasoning'])}")
    lines.append("")

    if a.get("historical_analog"):
        lines.append(f"📚 *{_md(a['historical_analog'])}*")
    lines.append(f"❌ *Invalidation:* {_md(a['invalidation'])}")

    return re.sub(r"(?<!\\)\|", r"\\|", "\n".join(lines))


async def consume_enriched_stream(bot: Any) -> None:
    from redis.asyncio import Redis

    redis_url = _required_env("REDIS_URL")
    chat_id = _required_env("TELEGRAM_CHAT_ID")
    topic_thread_ids = load_topic_thread_ids()
    redis = Redis.from_url(redis_url, decode_responses=True)

    try:
        last_id = await redis.get(REDIS_LAST_ID_KEY) or "0-0"
        logger.info("Starting Telegram Redis stream consumer from id=%s", last_id)

        while True:
            messages = await redis.xread(
                streams={REDIS_STREAM_NAME: last_id},
                count=10,
                block=60_000,
            )
            if not messages:
                continue

            for _, entries in messages:
                for message_id, fields in entries:
                    payload = _parse_stream_payload(fields)
                    tier = _payload_tier(payload)
                    message_thread_id = topic_thread_ids.get(tier)
                    try:
                        await _send_stream_message(
                            bot,
                            chat_id=chat_id,
                            text=format_telegram_message(await _telegram_analysis_payload(payload)),
                            message_thread_id=message_thread_id,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to send enriched stream message_id=%s tier=%s thread_id=%s: %s",
                            message_id,
                            tier,
                            message_thread_id,
                            exc,
                            exc_info=True,
                        )

                    last_id = message_id
                    await redis.set(REDIS_LAST_ID_KEY, last_id)
                    await asyncio.sleep(1.0)
    finally:
        await redis.aclose()


async def consume_macro_flash_stream(bot: Any) -> None:
    from redis.asyncio import Redis

    redis_url = _required_env("REDIS_URL")
    chat_id = _required_env("TELEGRAM_CHAT_ID")
    topic_thread_ids = load_topic_thread_ids()
    redis = Redis.from_url(redis_url, decode_responses=True)

    try:
        last_id = await redis.get(MACRO_FLASH_LAST_ID_KEY) or "0-0"
        logger.info("Starting Telegram macro flash consumer from id=%s", last_id)

        while True:
            messages = await redis.xread(
                streams={MACRO_FLASH_STREAM_NAME: last_id},
                count=10,
                block=60_000,
            )
            if not messages:
                continue

            for _, entries in messages:
                for message_id, fields in entries:
                    try:
                        await _send_stream_message(
                            bot,
                            chat_id=chat_id,
                            text=format_macro_flash_message(fields),
                            message_thread_id=topic_thread_ids.get("economic_data"),
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to send macro flash message_id=%s: %s",
                            message_id,
                            exc,
                            exc_info=True,
                        )

                    last_id = message_id
                    await redis.set(MACRO_FLASH_LAST_ID_KEY, last_id)
                    await asyncio.sleep(1.0)
    finally:
        await redis.aclose()


async def _send_stream_message(
    bot: Any,
    *,
    chat_id: str,
    text: str,
    message_thread_id: int | None,
) -> None:
    from telegram.error import RetryAfter, TimedOut

    for attempt in range(2):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="MarkdownV2",
                message_thread_id=message_thread_id,
                disable_web_page_preview=True,
            )
            return
        except RetryAfter as exc:
            if attempt == 1:
                raise
            await asyncio.sleep(float(exc.retry_after) + 1.0)
        except TimedOut:
            if attempt == 1:
                raise
            await asyncio.sleep(2.0)


async def _telegram_analysis_payload(payload: dict[str, Any]) -> dict[str, Any]:
    analysis = dict(payload.get("analysis") or {})
    analysis["released_at"] = payload.get("released_at") or payload.get("published_at")
    analysis["source"] = _display_source(payload.get("source"))

    raw_news_id = payload.get("raw_news_id")
    if raw_news_id and (not analysis.get("source") or not analysis.get("released_at")):
        raw_metadata = await _load_raw_news_metadata(int(raw_news_id))
        analysis["source"] = analysis.get("source") or _display_source(raw_metadata.get("source"))
        analysis["released_at"] = analysis.get("released_at") or raw_metadata.get("published_at")
    return analysis


async def _load_raw_news_metadata(raw_news_id: int) -> dict[str, Any]:
    rows = await _query_rows(
        """
        SELECT source, published_at
        FROM raw_news
        WHERE id = :raw_news_id
        """,
        {"raw_news_id": raw_news_id},
    )
    return rows[0] if rows else {}


def _display_source(source: Any) -> str | None:
    if not source:
        return None
    value = str(source)
    return SOURCE_DISPLAY_NAMES.get(value, value)


SOURCE_DISPLAY_NAMES = {
    "ap_news": "AP",
    "al_jazeera": "Al Jazeera",
    "federal_reserve": "Federal Reserve",
    "ecb": "ECB",
    "fred": "FRED",
    "investinglive": "InvestingLive",
}

_TIER_EMOJI = {
    "geopolitical": "\U0001f30d",
    "risk_sentiment": "\u26a0\ufe0f",
    "cb_policy": "\U0001f3e6",
    "economic_data": "\U0001f4ca",
    "positioning": "\U0001f4c8",
    "technical": "\U0001f4c9",
}
_SURPRISE_EMOJI = {"high": "\U0001f534", "medium": "\U0001f7e1", "low": "\u26aa"}
_NEWS_EMOJI = "\U0001f4f0"
_LOW_SURPRISE_EMOJI = "\u26aa"
_NEUTRAL_DIRECTION_EMOJI = "\u25c6"
_DIRECTION_EMOJI = {
    "bullish": "\u25b2",
    "bearish": "\u25bc",
    "neutral": "\u25c6",
    "conflicting": "\u26a1",
    "stronger": "\u25b2",
    "weaker": "\u25bc",
    "higher": "\u25b2",
    "lower": "\u25bc",
    "hawkish": "\u25b2",
    "dovish": "\u25bc",
    "unclear": "\u25c6",
    "not_applicable": "\u2014",
}


def conviction_bar(score: int) -> str:
    filled = round(score / 20)
    return "\u25cf" * filled + "\u25cb" * (5 - filled)


def format_telegram_message(a: dict[str, Any]) -> str:
    ci = a["currency_impact"]
    ii = a["inflation_impact"]
    eg = a["employment_growth_impact"]
    g = a["gold_analysis"]

    lines = [
        (
            f"{_TIER_EMOJI.get(a['tier'], _NEWS_EMOJI)} "
            f"*{_md(a['tier'].upper().replace('_', ' '))}* "
            f"{_SURPRISE_EMOJI.get(a['surprise_factor'], _LOW_SURPRISE_EMOJI)} "
            f"surprise: {_md(a['surprise_factor'])}"
        ),
        (
            f"\U0001f310 {_md(str(a.get('country') or 'Global'))} | "
            f"\U0001f550 {_md(_format_released_at(a.get('released_at')))} | "
            f"\U0001f4e1 {_md(str(a.get('source') or 'unknown'))}"
        ),
        f"*{_md(a['headline_summary'])}*",
        "",
    ]

    usd_icon = _DIRECTION_EMOJI.get(ci["usd"], _NEUTRAL_DIRECTION_EMOJI)
    lines.append(f"\U0001f4b5 *Currency* {usd_icon} USD: {_md(ci['usd'])}")
    lines.append(_md(ci["mechanism"]))
    if ci.get("affected_pairs"):
        lines.append(f"Pairs: {_md(', '.join(ci['affected_pairs']))}")
    lines.append("")

    if ii.get("direction") != "not_applicable":
        inf_icon = _DIRECTION_EMOJI.get(ii["direction"], _NEUTRAL_DIRECTION_EMOJI)
        lines.append(
            f"\U0001f4c8 *Inflation* {inf_icon} {_md(ii['direction'])} "
            f"\\(vs\\. expected: {_md(ii['expected_vs_actual'])}\\)"
        )
        lines.append(_md(ii["mechanism"]))
        lines.append("")

    if eg.get("direction") != "not_applicable":
        eg_icon = _DIRECTION_EMOJI.get(eg["direction"], _NEUTRAL_DIRECTION_EMOJI)
        fed_icon = _DIRECTION_EMOJI.get(eg["fed_reaction_function"], _NEUTRAL_DIRECTION_EMOJI)
        lines.append(
            f"\U0001f477 *Employment / growth* {eg_icon} {_md(eg['direction'])} "
            f"\\(vs\\. expected: {_md(eg['expected_vs_actual'])}\\)"
        )
        lines.append(f"Fed reaction: {fed_icon} {_md(eg['fed_reaction_function'])}")
        lines.append(_md(eg["mechanism"]))
        lines.append("")

    if _has_meaningful_gold_impact(g):
        net_direction = str(g.get("net_direction") or "neutral")
        gold_icon = _DIRECTION_EMOJI.get(net_direction, _NEUTRAL_DIRECTION_EMOJI)
        conviction = int(g.get("conviction") or 0)
        time_horizon = str(g.get("time_horizon") or "unspecified")
        lines.append(f"\U0001f947 *Gold XAU/USD* {gold_icon} {_md(net_direction.upper())}")
        lines.append(
            f"Conviction: {conviction_bar(conviction)} {conviction}/100 "
            f"| \u23f1 {_md(time_horizon)}"
        )
        lines.append(
            f"Channel: {_md(str(g.get('dominant_channel') or 'unknown'))} "
            f"\\(yields {_DIRECTION_EMOJI.get(g.get('real_yield_direction'), _NEUTRAL_DIRECTION_EMOJI)} "
            f"\u00b7 USD {_md(str(g.get('usd_channel') or 'neutral'))} "
            f"\u00b7 safe\\-haven {_md(str(g.get('safe_haven_channel') or 'neutral'))}\\)"
        )
        lines.append(_md(str(g.get("reasoning") or "")))
        lines.append("")

        if a.get("historical_analog"):
            lines.append(f"\U0001f4da *{_md(a['historical_analog'])}*")
        if a.get("invalidation"):
            lines.append(f"\u274c *Invalidation:* {_md(a['invalidation'])}")

    return re.sub(r"(?<!\\)\|", r"\\|", "\n".join(lines))


def _has_meaningful_gold_impact(gold_analysis: dict[str, Any]) -> bool:
    try:
        conviction = int(gold_analysis.get("conviction") or 0)
    except (TypeError, ValueError):
        conviction = 0

    net_direction = str(gold_analysis.get("net_direction") or "neutral")
    dominant_channel = str(gold_analysis.get("dominant_channel") or "")
    return conviction >= 40 or (
        conviction >= 20
        and net_direction not in {"neutral", "conflicting"}
        and dominant_channel not in {"", "conflicting"}
    )


def format_macro_flash_message(fields: dict[str, str]) -> str:
    headline = fields.get("headline") or "(untitled)"
    source = _display_source(fields.get("source")) or "unknown"
    released_at = _format_released_at(fields.get("released_at"))
    categories = _parse_categories(fields.get("matched_categories"))
    country = _infer_country_from_headline(headline)
    url = fields.get("url") or ""

    lines = [
        "\U0001f4ca *MACRO DATA FLASH*",
        (
            f"\U0001f310 {_md(country)} | "
            f"\U0001f550 {_md(released_at)} | "
            f"\U0001f4e1 {_md(source)}"
        ),
        f"*{_md(headline)}*",
    ]
    if categories:
        lines.append(f"Tags: {_md(', '.join(categories))}")
    if url:
        lines.append(f"[Source]({_md_url(url)})")

    return re.sub(r"(?<!\\)\|", r"\\|", "\n".join(lines))


def _parse_categories(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _infer_country_from_headline(headline: str) -> str:
    text_value = headline.lower()
    country_terms = (
        ("United Kingdom", ("uk", "u.k.", "britain", "british", "boe")),
        ("United States", ("us ", "u.s.", "united states", "fed", "fomc", "nonfarm", "nfp")),
        ("Eurozone", ("eurozone", "ecb", "euro area")),
        ("China", ("china", "chinese", "pboc", "cny")),
        ("Japan", ("japan", "boj", "yen", "jpy")),
        ("Canada", ("canada", "boc", "cad")),
        ("Australia", ("australia", "rba", "aud")),
        ("New Zealand", ("new zealand", "rbnz", "nzd")),
    )
    for country, terms in country_terms:
        if any(term in text_value for term in terms):
            return country
    return "Global"


async def latest_command(update: Any, context: Any) -> None:
    rows = await _query_rows(
        """
        SELECT e.id, e.tier, e.confidence, e.created_at, r.title, r.url
        FROM enriched_news e
        JOIN raw_news r ON r.id = e.raw_news_id
        ORDER BY e.created_at DESC
        LIMIT 5
        """,
    )
    await _reply(update, _format_rows("Latest enriched items", rows))


async def gold_command(update: Any, context: Any) -> None:
    rows = await _query_rows(
        """
        SELECT e.id, e.tier, e.confidence, e.created_at, r.title, r.url,
               e.gold_analysis ->> 'conviction' AS conviction
        FROM enriched_news e
        JOIN raw_news r ON r.id = e.raw_news_id
        WHERE e.gold_analysis ->> 'conviction' ~ '^[0-9]+(\\.[0-9]+)?$'
          AND (e.gold_analysis ->> 'conviction')::numeric >= 60
        ORDER BY e.created_at DESC
        LIMIT 10
        """,
    )
    await _reply(update, _format_rows("Gold conviction >= 60", rows))


async def pair_command(update: Any, context: Any) -> None:
    if not context.args:
        await _reply(update, "Usage: `/pair SYMBOL`")
        return

    symbol = context.args[0].upper()
    rows = await _query_rows(
        """
        SELECT e.id, e.tier, e.confidence, e.created_at, r.title, r.url
        FROM enriched_news e
        JOIN raw_news r ON r.id = e.raw_news_id
        WHERE EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(
                COALESCE(e.currency_impact -> 'affected_pairs', '[]'::jsonb)
            ) AS pair(value)
            WHERE upper(pair.value) = :symbol
        )
        ORDER BY e.created_at DESC
        LIMIT 10
        """,
        {"symbol": symbol},
    )
    await _reply(update, _format_rows(f"Pair {symbol}", rows))


async def status_command(update: Any, context: Any) -> None:
    rows = await _query_rows(
        """
        SELECT source_name, last_item_at, last_checked_at, is_healthy
        FROM source_health
        ORDER BY source_name
        """,
    )
    now = datetime.now(UTC)
    lines = ["*Source Health*"]
    for row in rows:
        last_item_at = row.get("last_item_at")
        minutes = _minutes_since(now, last_item_at)
        status = "STALE" if minutes is None or minutes > 180 else "ok"
        minutes_text = "never" if minutes is None else f"{minutes:.0f}m"
        lines.append(f"`{_md(str(row['source_name']))}`: {_md(minutes_text)} since item - *{_md(status)}*")
    await _reply(update, "\n".join(lines))


async def run_bot() -> None:
    from telegram.ext import Application, CommandHandler

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    token = _required_env("TELEGRAM_BOT_TOKEN")

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("latest", latest_command))
    application.add_handler(CommandHandler("gold", gold_command))
    application.add_handler(CommandHandler("pair", pair_command))
    application.add_handler(CommandHandler("status", status_command))

    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    stream_tasks = [
        asyncio.create_task(consume_enriched_stream(application.bot)),
        asyncio.create_task(consume_macro_flash_stream(application.bot)),
    ]

    try:
        await asyncio.Event().wait()
    finally:
        for task in stream_tasks:
            task.cancel()
        await asyncio.gather(*stream_tasks, return_exceptions=True)
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


async def _query_rows(statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    maker = get_sessionmaker()
    async with maker() as session:
        result = await session.execute(text(statement), params or {})
        return [dict(row) for row in result.mappings().all()]


async def _reply(update: Any, text_value: str) -> None:
    message = update.effective_message
    await message.get_bot().send_message(
        chat_id=message.chat_id,
        message_thread_id=getattr(message, "message_thread_id", None),
        text=text_value,
        parse_mode="MarkdownV2",
        disable_web_page_preview=True,
    )


def _format_rows(title: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return f"*{_md(title)}*\nNo matching items\\."

    lines = [f"*{_md(title)}*"]
    for row in rows:
        created_at = row.get("created_at")
        timestamp = created_at.isoformat(timespec="minutes") if hasattr(created_at, "isoformat") else str(created_at)
        confidence = row.get("confidence", "n/a")
        tier = row.get("tier", "n/a")
        headline = str(row.get("title") or "(untitled)")
        lines.append(
            f"\n*{_md(headline)}*\n"
            f"`{_md(str(tier))}` conf `{_md(str(confidence))}` at `{_md(timestamp)}`"
        )
        if row.get("url"):
            lines.append(f"[Source]({_md_url(str(row['url']))})")
    return "\n".join(lines)


def _parse_stream_payload(fields: dict[str, str]) -> dict[str, Any]:
    payload = fields.get("payload")
    if payload:
        return json.loads(payload)
    return {
        "raw_news_id": fields.get("raw_news_id"),
        "enriched_news_id": fields.get("enriched_news_id"),
        "headline": fields.get("headline") or "(untitled)",
        "analysis": {},
        "market_context": {},
    }


def _payload_tier(payload: dict[str, Any]) -> str:
    analysis = payload.get("analysis") or {}
    return str(analysis.get("tier") or payload.get("tier") or "risk_sentiment")


def _format_released_at(value: Any) -> str:
    if value in (None, ""):
        return "unknown"

    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return str(value)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    display_time = parsed.astimezone(DISPLAY_TIMEZONE)
    return f"{display_time:%H:%M CET/CEST, %b} {display_time.day:02d}, {display_time.year}"


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _minutes_since(now: datetime, value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return (now - value.astimezone(UTC)).total_seconds() / 60


def _compact_json(value: Any) -> str:
    if value in (None, {}, []):
        return "n/a"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:700]


def _round(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{value:.2f}"


def _md(value: str) -> str:
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!])", r"\\\1", value)


def _md_url(value: str) -> str:
    return value.replace("\\", "\\\\").replace(")", r"\)")


if __name__ == "__main__":
    asyncio.run(run_bot())
