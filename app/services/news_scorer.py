import json
import logging

from openai import AsyncOpenAI

from app.settings import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are an FX market analyst. Score each headline
for FX market relevance.

Return a JSON object only. No other text. No markdown.
The object must have a "results" array with one object per headline in the same order received.

Each results object:
{
  "score": integer 1-10,
  "severity": "CRITICAL|HIGH|MEDIUM|LOW",
  "affected_currencies": ["USD", "EUR"],
  "risk_tone": "RISK_ON|RISK_OFF|NEUTRAL" or null,
  "dominant_tier": one of "GEOPOLITICAL_SHOCK|
    RISK_SENTIMENT|CB_POLICY_DIVERGENCE|
    ECONOMIC_DATA|POSITIONING|TECHNICAL" or null,
  "currency_implications": {"USD": "bullish"},
  "alert_text": "One sentence for dashboard.
                 Null if score below 7.",
  "reason": "One sentence why this score."
}

Scoring:
9-10: War escalation, emergency CB meeting, pandemic,
      major sanctions, OPEC emergency action
7-8:  CB official on rates, geopolitical development,
      tariff announcement, political shock,
      data preview from credible source
5-6:  Routine commentary, minor development
1-4:  Not FX relevant, already known, opinion only
"""


async def score_headlines(headlines: list[dict]) -> list[dict]:
    if not headlines:
        return []

    FX_KEYWORDS = [  # noqa: N806 - requested local constant name.
        "fed", "federal reserve", "ecb", "bank of england",
        "boe", "rba", "boj", "bank of japan", "rbnz", "snb",
        "bank of canada", "boc", "central bank",
        "interest rate", "monetary policy", "inflation",
        "powell", "lagarde", "bailey", "ueda", "macklem",
        "war", "ceasefire", "sanctions", "tariff", "trade war",
        "invasion", "attack", "missile", "nuclear", "opec",
        "oil", "crude", "energy", "supply", "embargo",
        "china", "russia", "ukraine", "iran", "north korea",
        "middle east", "taiwan", "nato",
        "recession", "gdp", "unemployment", "jobs", "cpi",
        "crisis", "collapse", "default", "pandemic",
        "emergency", "shock", "surge", "plunge",
        "dollar", "euro", "sterling", "yen", "yuan",
        "aussie", "kiwi", "loonie", "franc",
        "usd", "eur", "gbp", "jpy", "aud", "nzd", "cad", "chf",
        "stock market", "equity", "bond yield", "treasury",
        "safe haven", "risk off", "risk on", "vix",
    ]

    scored = [dict(headline) for headline in headlines]
    passed: list[tuple[int, dict]] = []

    for index, headline in enumerate(scored):
        title = str(headline.get("title", ""))
        if any(keyword in title.lower() for keyword in FX_KEYWORDS):
            passed.append((index, headline))
        else:
            headline.update(_filtered_score())

    if not passed:
        return scored

    settings = get_settings()
    if not settings.openai_api_key:
        logger.error("OPENAI_API_KEY is not configured for headline scoring.")
        return [_error_score(headline) for headline in scored]

    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_message([headline for _, headline in passed])},
            ],
            max_tokens=4000,
            temperature=0,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "headline_scores",
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "results": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": True,
                                },
                            },
                        },
                        "required": ["results"],
                    },
                },
            },
        )
        content = response.choices[0].message.content or ""
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            parsed = parsed.get("results")
        if not isinstance(parsed, list):
            raise ValueError("OpenAI response was not a JSON array.")
    except Exception:
        logger.exception("OpenAI headline scoring failed.")
        return [_error_score(headline) for headline in scored]

    for (index, headline), result in zip(passed, parsed, strict=False):
        if not isinstance(result, dict):
            result = {}
        scored[index].update(_normalise_score(result))
        headline.update(scored[index])

    for index, _headline in passed[len(parsed):]:
        scored[index].update(_normalise_score({}))

    return scored


def _build_user_message(headlines: list[dict]) -> str:
    lines = [
        'Score each headline below. Return {"results": [...]} with one object per headline in the same order.',
        "",
    ]
    lines.extend(
        f"{index}. {headline.get('title', '')} (source: {headline.get('source_name', '')})"
        for index, headline in enumerate(headlines, start=1)
    )
    return "\n".join(lines)


def _filtered_score() -> dict:
    return {
        "score": 0,
        "severity": "FILTERED",
        "filtered": True,
        "affected_currencies": [],
        "risk_tone": None,
        "dominant_tier": None,
        "currency_implications": {},
        "alert_text": None,
        "reason": "Keyword filter: no FX relevance detected",
    }


def _error_score(headline: dict) -> dict:
    updated = dict(headline)
    updated.update(
        {
            "score": 0,
            "severity": "ERROR",
            "filtered": False,
            "affected_currencies": [],
            "risk_tone": None,
            "dominant_tier": None,
            "currency_implications": {},
            "alert_text": None,
            "reason": "OpenAI scoring failed",
        }
    )
    return updated


def _normalise_score(result: dict) -> dict:
    score = _score_int(result.get("score"))
    return {
        "score": score,
        "severity": _severity(score, result.get("severity")),
        "filtered": False,
        "affected_currencies": _string_list(result.get("affected_currencies")),
        "risk_tone": _nullable_string(result.get("risk_tone")),
        "dominant_tier": _nullable_string(result.get("dominant_tier")),
        "currency_implications": (
            result["currency_implications"]
            if isinstance(result.get("currency_implications"), dict)
            else {}
        ),
        "alert_text": _nullable_string(result.get("alert_text")) if score >= 7 else None,
        "reason": str(result.get("reason") or ""),
    }


def _score_int(value: object) -> int:
    try:
        return min(10, max(1, int(value)))
    except (TypeError, ValueError):
        return 1


def _severity(score: int, value: object) -> str:
    severity = str(value or "").upper()
    if severity in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
        return severity
    if score >= 9:
        return "CRITICAL"
    if score >= 7:
        return "HIGH"
    if score >= 5:
        return "MEDIUM"
    return "LOW"


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


def _nullable_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
