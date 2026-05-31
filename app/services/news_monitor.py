import logging
from datetime import UTC, datetime

from sqlalchemy import Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from app.db.session import session_scope
from app.services.news_fetcher import fetch_new_headlines
from app.services.news_scorer import score_headlines

logger = logging.getLogger(__name__)

INSERT_NEWS_ALERT = (
    text(
        """
        INSERT INTO intelligence.news_alerts (
            headline,
            source,
            url,
            published_at,
            detected_at,
            relevance_score,
            severity,
            was_surfaced,
            affected_currencies,
            affected_assets,
            implied_tier,
            risk_tone_implication,
            currency_implications,
            alert_text,
            content_hash,
            triggered_state_change,
            resulting_dominance_state_id
        )
        VALUES (
            :headline,
            :source,
            :url,
            :published_at,
            :detected_at,
            :relevance_score,
            :severity,
            :was_surfaced,
            :affected_currencies,
            :affected_assets,
            :implied_tier,
            :risk_tone_implication,
            :currency_implications,
            :alert_text,
            :content_hash,
            :triggered_state_change,
            :resulting_dominance_state_id
        )
        ON CONFLICT (content_hash) DO NOTHING
        """
    )
    .bindparams(bindparam("affected_currencies", type_=ARRAY(Text)))
    .bindparams(bindparam("affected_assets", type_=ARRAY(Text)))
    .bindparams(bindparam("currency_implications", type_=JSONB))
)


async def run_news_monitor(tier: int) -> dict:
    try:
        if not _is_trading_hours():
            return {
                "tier": tier,
                "skipped": True,
                "reason": "outside trading hours",
            }

        headlines = await fetch_new_headlines(tier)
        if not headlines:
            return {"tier": tier, "fetched": 0, "scored": 0, "saved": 0}

        scored_headlines = await score_headlines(headlines)
        qualifying = [headline for headline in scored_headlines if _qualifies(headline)]

        saved = 0
        detected_at = datetime.now(UTC)
        async with session_scope() as session:
            for headline in qualifying:
                result = await session.execute(
                    INSERT_NEWS_ALERT,
                    _insert_params(headline, detected_at),
                )
                if result.rowcount and result.rowcount > 0:
                    saved += result.rowcount

        return {
            "tier": tier,
            "fetched": len(headlines),
            "filtered_out": _count_severity(scored_headlines, "FILTERED"),
            "scored": sum(1 for headline in scored_headlines if headline.get("severity") != "FILTERED"),
            "saved": saved,
            "critical_count": sum(1 for headline in scored_headlines if _score(headline) >= 9),
            "high_count": sum(1 for headline in scored_headlines if 7 <= _score(headline) < 9),
        }
    except Exception:
        logger.exception("News monitor failed for tier %s.", tier)
        return {
            "tier": tier,
            "fetched": 0,
            "filtered_out": 0,
            "scored": 0,
            "saved": 0,
            "critical_count": 0,
            "high_count": 0,
        }


def _is_trading_hours() -> bool:
    hour = datetime.now(UTC).hour
    return 0 <= hour < 22


def _qualifies(headline: dict) -> bool:
    severity = str(headline.get("severity") or "").upper()
    return _score(headline) >= 5 and severity not in {"FILTERED", "ERROR"}


def _insert_params(headline: dict, detected_at: datetime) -> dict:
    score = _score(headline)
    return {
        "headline": str(headline.get("title") or ""),
        "source": str(headline.get("source_name") or ""),
        "url": str(headline.get("url") or ""),
        "published_at": headline.get("published_at") or detected_at,
        "detected_at": detected_at,
        "relevance_score": score,
        "severity": str(headline.get("severity") or ""),
        "was_surfaced": score >= 7,
        "affected_currencies": _string_list(headline.get("affected_currencies")),
        "affected_assets": [],
        "implied_tier": headline.get("dominant_tier"),
        "risk_tone_implication": headline.get("risk_tone"),
        "currency_implications": (
            headline["currency_implications"]
            if isinstance(headline.get("currency_implications"), dict)
            else {}
        ),
        "alert_text": headline.get("alert_text"),
        "content_hash": str(headline.get("content_hash") or ""),
        "triggered_state_change": False,
        "resulting_dominance_state_id": None,
    }


def _score(headline: dict) -> int:
    try:
        return int(headline.get("score") or 0)
    except (TypeError, ValueError):
        return 0


def _count_severity(headlines: list[dict], severity: str) -> int:
    return sum(1 for headline in headlines if headline.get("severity") == severity)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]
