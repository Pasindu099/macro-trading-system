"""Step 13 C: rule-based FX relevance filter, cross-source dedupe and tagging (no LLM).

raw_news (every collected row, permanent) -> news_raw_status (kept / dropped / merged + reason)
                                           -> news_items (one per story, tags, "+N sources").
Rules live in config/news_tagging.yaml; officials come from config/cb_officials.yaml.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.types import Text

from app.db.session import session_scope
from app.processing.cb_officials import is_policy_sentence, is_voter, match_officials, official

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "news_tagging.yaml"
TAG_VERSION = "rules_v1"
TYPES = ("Data", "CB speaker", "Politics & fiscal", "Geopolitics", "Flows & market", "Commodities", "Analysis")
BANK_CURRENCY = {"FED": "USD", "ECB": "EUR", "BOE": "GBP", "BOJ": "JPY", "RBA": "AUD", "BOC": "CAD",
                 "RBNZ": "NZD", "SNB": "CHF"}
TECHNICAL_WORDS = ("technical", "chart", "fibonacci", "elliott wave", "support", "resistance", "levels to watch")
CLAUSE_SPLIT = re.compile(r"[,;:.!?()]|\b(?:as|and|while|but|after|amid|with|despite|on)\b", re.I)


def _words_re(words: list[str]) -> re.Pattern | None:
    if not words:
        return None
    alternatives = "|".join(re.escape(w) + r"(?:s|es)?" for w in sorted(set(words), key=len, reverse=True))
    return re.compile(r"(?<![\w&])(?:" + alternatives + r")(?![\w&])", re.I)


def _acronyms_re(words: list[str]) -> re.Pattern | None:
    if not words:
        return None
    alternatives = "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))
    return re.compile(r"(?<![\w.])(?:" + alternatives + r")(?![\w+])")


@dataclass(frozen=True)
class Rules:
    raw: dict[str, Any]
    currency_strong: dict[str, list[re.Pattern]]
    currency_weak: dict[str, re.Pattern]
    pair_re: re.Pattern
    patterns: dict[str, re.Pattern | None] = field(default_factory=dict)

    def hit(self, name: str, value: str) -> bool:
        pattern = self.patterns.get(name)
        return bool(pattern and pattern.search(value))


@lru_cache(maxsize=1)
def rules() -> Rules:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    country = {w.lower() for w in raw["country_words"]}
    strong, weak = {}, {}
    for ccy, spec in raw["currencies"].items():
        words = [w for w in spec.get("words", []) if w.lower() not in country]
        strong[ccy] = [p for p in (_acronyms_re(spec.get("acronyms", [])), _words_re(words)) if p]
        weak_words = [w for w in spec.get("words", []) if w.lower() in country]
        if weak_words:
            weak[ccy] = _words_re(weak_words)
    codes = "|".join(raw["currencies"])
    patterns = {
        "country": _words_re(raw["country_words"]),
        "cb": _words_re(raw["central_banks"]["words"]),
        "cb_acr": _acronyms_re(raw["central_banks"]["acronyms"]),
        "cb_decision": _words_re(raw["cb_decision"]["words"]),
        "data_high": _words_re(raw["data_releases"]["high"]),
        "data_medium": _words_re(raw["data_releases"]["medium"]),
        "print": re.compile(raw["data_releases"]["print_pattern"]),
        "commodity": _words_re(raw["commodities"]["words"]),
        "commodity_acr": _acronyms_re(raw["commodities"]["acronyms"]),
        "precious": _words_re(["gold", "silver", "bullion", "precious metal", "xau", "xag"]),
        "crypto": _words_re(raw["crypto_words"]),
        "equity_single": _words_re(raw["equity_single_words"]),
        "equity_index": _words_re(raw["equity_index_words"]),
        "analysis": _words_re(raw["types"]["analysis_words"]),
        "geopolitics": _words_re(raw["types"]["geopolitics_words"]),
        "politics": _words_re(raw["types"]["politics_fiscal_words"]),
        "positioning": _words_re(raw["positioning_words"]),
        "technical": _words_re(list(TECHNICAL_WORDS)),
        "dovish": _words_re(raw["dovish_words"]),
        "hawkish": _words_re(raw["hawkish_words"]),
    }
    for situation_id, words in raw["situations"].items():
        patterns[f"situation:{situation_id}"] = _words_re(words)
    for driver, words in raw["drivers"].items():
        patterns[f"driver:{driver}"] = _words_re(words)
    return Rules(raw=raw, currency_strong=strong, currency_weak=weak, patterns=patterns,
                 pair_re=re.compile(rf"(?<![A-Z])({codes})/?({codes})(?![A-Z])"))


def clean(value: str | None) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value or ""))).strip()


def detect_currencies(value: str, *, include_weak: bool = True) -> list[str]:
    r = rules()
    found = {ccy for ccy, pats in r.currency_strong.items() if any(p.search(value) for p in pats)}
    for a, b in r.pair_re.findall(value):
        found.update((a, b))
    if include_weak:
        found.update(ccy for ccy, pat in r.currency_weak.items() if pat.search(value))
    return sorted(found)


def _speakers(title: str, on: date) -> list[dict[str, Any]]:
    out = []
    for bank, speaker_id, _ in match_officials(title, on=on):
        person = official(bank, speaker_id) or {}
        out.append({"bank": bank, "speaker_id": speaker_id, "name": person.get("name", speaker_id),
                    "role": person.get("role"), "voter": is_voter(bank, speaker_id, on.year),
                    "profile_url": f"/central-banks?bank={bank}&speaker={speaker_id}"})
    return out


# ---------------------------------------------------------------- drivers

def _direction(window_after: list[str], window_before: list[str]) -> str | None:
    up, down = set(rules().raw["direction_up"]), set(rules().raw["direction_down"])
    for distance in range(4):
        for tokens in (window_after, window_before):
            if distance < len(tokens):
                token = tokens[distance]
                if token in up:
                    return "up"
                if token in down:
                    return "down"
    return None


def detect_drivers(value: str) -> list[dict[str, str | None]]:
    """Named gold/commodity drivers with direction where the text states one."""
    r = rules()
    precious = r.hit("precious", value)
    found: dict[str, str | None] = {}
    for clause in CLAUSE_SPLIT.split(value):
        if not clause or not clause.strip():
            continue
        lower = clause.lower()
        used: list[tuple[int, int]] = []
        for driver in ("real_yields", "yields", "dollar", "oil", "safe_haven", "fed_rates", "inflation",
                       "cb_buying", "etf_flows"):
            if driver == "oil" and not precious:
                continue
            for match in r.patterns[f"driver:{driver}"].finditer(lower):
                if any(s <= match.start() < e for s, e in used):
                    continue
                used.append(match.span())
                before = re.findall(r"[a-z+-]+", lower[:match.start()])[::-1][:4]
                after = re.findall(r"[a-z+-]+", lower[match.end():])[:4]
                term_tokens = re.findall(r"[a-z+-]+", match.group(0))
                direction = _direction(term_tokens[-1:] + after, before) if driver == "etf_flows" else _direction(after, before)
                if driver == "fed_rates":
                    if r.hit("dovish", clause):
                        direction = "dovish"
                    elif r.hit("hawkish", clause):
                        direction = "hawkish"
                if driver not in found or (found[driver] is None and direction):
                    found[driver] = direction
    return [{"driver": d, "direction": found[d]} for d in found]


# ---------------------------------------------------------------- classification

@dataclass
class Classification:
    status: str  # kept | dropped
    reason: str | None = None
    currencies: list[str] = field(default_factory=list)
    news_type: str | None = None
    impact: str | None = None
    cb_decision: bool = False
    data_tier: str | None = None
    speakers: list[dict[str, Any]] = field(default_factory=list)
    situation_ids: list[str] = field(default_factory=list)
    episode_ids: list[int] = field(default_factory=list)
    dominance_tier: str | None = None
    is_commodity: bool = False
    drivers: list[dict[str, Any]] = field(default_factory=list)


def _active_episodes(episodes: list[dict[str, Any]], published_at: datetime) -> list[dict[str, Any]]:
    day = published_at.date()
    return [e for e in episodes if e["started_at"] <= day and (e.get("ended_at") is None or e["ended_at"] >= day)]


def classify(*, title: str, summary: str | None, source: str, source_category: str | None,
             published_at: datetime, episodes: list[dict[str, Any]]) -> Classification:
    r = rules()
    title = clean(title)
    summary = clean(summary)
    body = f"{title} {summary}"
    if source in r.raw.get("excluded_sources", []):
        return Classification("dropped", "excluded_source")

    speakers = _speakers(title, published_at.date())
    currencies = sorted(set(detect_currencies(body)) | {BANK_CURRENCY[s["bank"]] for s in speakers})
    strong_ccy = detect_currencies(body, include_weak=False)
    cb = r.hit("cb", title) or r.hit("cb_acr", title)
    data_tier = "high" if r.hit("data_high", title) else "medium" if r.hit("data_medium", title) else None
    is_print = r.hit("print", title)
    commodity = source == "kitco" or r.hit("commodity", title) or r.hit("commodity_acr", title)
    linked = []
    for episode in _active_episodes(episodes, published_at):
        if not r.hit(f"situation:{episode['situation_id']}", title):
            continue
        scope = str(episode.get("scope_key") or "GLOBAL").replace(":", "/").split("/")
        if not currencies or "GLOBAL" in scope or any(c in scope for c in currencies):
            linked.append(episode)
    situation_ids = sorted({e["situation_id"] for e in linked})
    strong = bool(strong_ccy or cb or r.hit("cb", body) or speakers or data_tier or is_print or commodity or linked)
    weak = r.hit("country", body)
    if r.hit("crypto", body) and not strong:
        return Classification("dropped", "crypto_only")
    if r.hit("equity_single", title) and not r.hit("equity_index", title) and not strong:
        return Classification("dropped", "single_stock")
    if not strong and not weak:
        return Classification("dropped", "unrelated")

    cb_decision = bool(r.hit("cb_decision", title) and (cb or source in {"federal_reserve", "ecb"}))
    analysis = (source in r.raw["types"]["analysis_sources"]
                or (source_category or "") in r.raw["types"]["analysis_source_categories"]
                or r.hit("analysis", title))
    if cb_decision:
        news_type = "CB speaker"
    elif is_print or (data_tier and re.search(r"\d", title) and not analysis):
        news_type = "Data"
    elif speakers:
        news_type = "CB speaker"
    elif analysis:
        news_type = "Analysis"
    elif commodity:
        news_type = "Commodities"
    elif r.hit("geopolitics", title):
        news_type = "Geopolitics"
    elif r.hit("politics", title):
        news_type = "Politics & fiscal"
    elif cb or source in {"federal_reserve", "ecb"}:
        news_type = "CB speaker"
    else:
        news_type = "Flows & market"

    voter_on_policy = any(s["voter"] is True for s in speakers) and is_policy_sentence(title)
    high_situation = any(e.get("severity") == "high" for e in linked)
    if news_type == "Analysis":
        impact = "Low"
    elif cb_decision or voter_on_policy or (news_type == "Data" and data_tier == "high" and currencies) or high_situation:
        impact = "High"
    elif speakers or news_type in {"Data", "Commodities"} or commodity:
        impact = "Medium"
    else:
        impact = "Low"

    tier = r.raw["dominance_tier"].get(news_type)
    if news_type == "Flows & market" and r.hit("positioning", title):
        tier = "Positioning"
    if news_type == "Analysis":
        tier = "Technical" if r.hit("technical", title) else None

    return Classification(
        "kept", None, currencies, news_type, impact, cb_decision, data_tier if news_type == "Data" else None,
        speakers, situation_ids, sorted(e["id"] for e in linked), tier, bool(commodity),
        detect_drivers(body) if commodity else [],
    )


# ---------------------------------------------------------------- dedupe

def title_tokens(title: str) -> list[str]:
    stop = set(rules().raw["dedupe"]["stopwords"])
    return [t for t in re.findall(r"[a-z]+", clean(title).lower().replace("'s", "")) if len(t) > 1 and t not in stop]


def same_story(a: str, b: str) -> bool:
    cfg = rules().raw["dedupe"]
    ta, tb = title_tokens(a), title_tokens(b)
    sa, sb = set(ta), set(tb)
    shared = len(sa & sb)
    if not sa or not sb:
        return False
    if shared >= cfg["min_shared_tokens"] and shared / len(sa | sb) >= cfg["jaccard"]:
        return True
    return shared >= 2 and SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio() >= cfg["sequence_ratio"]


# ---------------------------------------------------------------- runner

PENDING = text("""
    SELECT r.id, r.source, r.source_category, r.title, r.body, r.url, r.published_at
    FROM raw_news r LEFT JOIN news_raw_status s ON s.raw_news_id = r.id
    WHERE s.raw_news_id IS NULL AND r.published_at <= now() + interval '5 minutes'
    ORDER BY r.published_at, r.id LIMIT :limit
""")
CANDIDATES = text("""
    SELECT id, title FROM news_items
    WHERE published_at BETWEEN :start AND :end AND source <> :source
      AND NOT (:source = ANY(merged_sources))
    ORDER BY published_at
""")
INSERT_ITEM = text("""
    INSERT INTO news_items (raw_news_id, source, published_at, title, summary, url, currencies, news_type, impact,
        cb_decision, data_tier, speakers, situation_ids, dominance_tier, is_commodity, drivers, tag_version)
    VALUES (:raw_news_id, :source, :published_at, :title, :summary, :url, :currencies, :news_type, :impact,
        :cb_decision, :data_tier, :speakers, :situation_ids, :dominance_tier, :is_commodity, :drivers, :tag_version)
    RETURNING id
""").bindparams(bindparam("currencies", type_=ARRAY(Text)), bindparam("situation_ids", type_=ARRAY(Text)),
                bindparam("speakers", type_=JSONB), bindparam("drivers", type_=JSONB))
MERGE_ITEM = text("""
    UPDATE news_items SET merged_sources = array_append(merged_sources, :source), merged_count = merged_count + 1
    WHERE id = :id
""")
INSERT_STATUS = text("""
    INSERT INTO news_raw_status (raw_news_id, status, reason, news_item_id)
    VALUES (:raw_news_id, :status, :reason, :news_item_id) ON CONFLICT (raw_news_id) DO NOTHING
""")


async def _episodes() -> list[dict[str, Any]]:
    from app.services.situations import get_situation_episodes

    return await get_situation_episodes()


async def process_pending(*, limit: int = 1000) -> dict[str, int]:
    """Classify, dedupe and tag raw rows that have no status yet (oldest first)."""
    counts = {"kept": 0, "dropped": 0, "merged": 0}
    episodes = await _episodes()
    window = timedelta(hours=rules().raw["dedupe"]["window_hours"])
    async with session_scope() as session:
        if not (await session.execute(text("SELECT pg_try_advisory_xact_lock(hashtext('news_tagging'))"))).scalar():
            return counts  # another run holds the lock
        rows = (await session.execute(PENDING, {"limit": limit})).mappings().all()
        for row in rows:
            published_at = row["published_at"].astimezone(UTC)
            result = classify(title=row["title"], summary=row["body"], source=row["source"],
                              source_category=row["source_category"], published_at=published_at, episodes=episodes)
            status = {"raw_news_id": row["id"], "status": result.status, "reason": result.reason, "news_item_id": None}
            if result.status == "kept":
                candidates = (await session.execute(CANDIDATES, {
                    "start": published_at - window, "end": published_at + window, "source": row["source"],
                })).mappings().all()
                match = next((c for c in candidates if same_story(c["title"], row["title"])), None)
                if match:
                    await session.execute(MERGE_ITEM, {"id": match["id"], "source": row["source"]})
                    status.update(status="merged", reason="same_story", news_item_id=match["id"])
                else:
                    item_id = (await session.execute(INSERT_ITEM, {
                        "raw_news_id": row["id"], "source": row["source"], "published_at": published_at,
                        "title": clean(row["title"]), "summary": clean(row["body"])[:2000] or None, "url": row["url"],
                        "currencies": result.currencies, "news_type": result.news_type, "impact": result.impact,
                        "cb_decision": result.cb_decision, "data_tier": result.data_tier, "speakers": result.speakers,
                        "situation_ids": result.situation_ids, "dominance_tier": result.dominance_tier,
                        "is_commodity": result.is_commodity, "drivers": result.drivers, "tag_version": TAG_VERSION,
                    })).scalar_one()
                    status["news_item_id"] = item_id
            await session.execute(INSERT_STATUS, status)
            counts[status["status"]] += 1
    if any(counts.values()):
        logger.info("news tagging: %s", counts)
    return counts
