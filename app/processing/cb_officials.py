"""Official name/alias matching and rule-based point extraction (Step 12 Part E; no LLM)."""

from __future__ import annotations

import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.services.cb_statement_diff import split_sentences

CONFIG_PATH = Path("config/cb_officials.yaml")
# Keep a sentence when it mentions policy or the economy (word-start match).
POLICY_WORDS = ["inflation", "price", "rate", "policy", "monetary", "economy", "economic", "growth", "gdp", "labor",
                "labour", "employment", "unemployment", "wage", "demand", "tariff", "currenc", "exchange rate", "dollar",
                "appreciat", "depreciat",
                "financial conditions", "yield", "balance sheet", "tighten", "easing", "hike", "cut", "restrictive",
                "accommodative", "neutral", "outlook", "recession", "spending", "consumption", "credit"]
# Theme per sentence: most word-start hits; ties by order; none -> other.
THEMES = {
    "inflation": ["inflation", "price", "cpi", "pce", "hicp", "disinflation", "cost"],
    "labor": ["labor", "labour", "employment", "unemployment", "job", "wage", "payroll", "workers", "hiring"],
    "growth": ["growth", "gdp", "activity", "demand", "output", "consumption", "investment", "spending",
               "recession", "economy", "economic", "tariff", "trade"],
    "financial conditions": ["financial conditions", "financial stability", "credit", "yield", "bond", "spread",
                             "equity", "asset price", "lending", "bank", "market", "liquidity", "balance sheet"],
    "FX": ["exchange rate", "currenc", "dollar", "euro", "yen", "franc", "sterling", "appreciat", "depreciat"],
}


@lru_cache(maxsize=1)
def officials() -> dict[str, Any]:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["banks"]


def _hits(text: str, words: list[str]) -> int:
    lower = f" {text.lower()} "
    return sum(len(re.findall(r"\b" + re.escape(word), lower)) for word in words)


def theme(sentence: str) -> str:
    scores = {name: _hits(sentence, words) for name, words in THEMES.items()}
    best = max(scores.values())
    return next(name for name, score in scores.items() if score == best) if best else "other"


def is_policy_sentence(sentence: str) -> bool:
    return _hits(sentence, POLICY_WORDS) > 0


def extract_points(text: str) -> list[tuple[int, str, str]]:
    """(position, sentence, theme) for sentences with policy/economy keywords."""
    return [(i, s, theme(s)) for i, s in enumerate(split_sentences(text)) if is_policy_sentence(s) and len(s) <= 1200]


def _alias_pattern(alias: str) -> re.Pattern:
    return re.compile(r"(?<![\w-])" + re.escape(alias) + r"(?![\w-])")


def _active(official: dict[str, Any], on: date | None) -> bool:
    if on is None:
        return True
    start, end = official.get("start"), official.get("end")
    return (start is None or date.fromisoformat(str(start)) <= on) and (end is None or on <= date.fromisoformat(str(end)))


def match_officials(text: str, *, bank: str | None = None, on: date | None = None) -> list[tuple[str, str, int]]:
    """(bank, speaker_id, position) of officials named in `text`, earliest first.

    Aliases are case-sensitive at word boundaries. An ambiguous alias counts only when the
    text also names its bank (context words) or the caller already fixed the bank.
    """
    found: dict[tuple[str, str], int] = {}
    for code, cfg in officials().items():
        if bank and code != bank:
            continue
        has_context = bank == code or any(_alias_pattern(word).search(text) for word in cfg.get("context", []))
        for official in cfg["officials"]:
            if not _active(official, on):
                continue
            aliases = [official["name"], *official.get("aliases", [])]
            # BoJ pages write "UEDA Kazuo": also match single-word aliases in upper case.
            aliases += [a.upper() for a in aliases if " " not in a and a.upper() != a]
            for alias in aliases:
                match = _alias_pattern(alias).search(text)
                if not match:
                    continue
                if official.get("ambiguous") and alias != official["name"] and not has_context:
                    continue
                key = (code, official["id"])
                found[key] = min(found.get(key, match.start()), match.start())
    return sorted(((b, s, p) for (b, s), p in found.items()), key=lambda item: item[2])


def official(bank: str, speaker_id: str) -> dict[str, Any] | None:
    return next((o for o in officials()[bank]["officials"] if o["id"] == speaker_id), None)


def is_voter(bank: str, speaker_id: str, year: int) -> bool | None:
    """True/False for a year; None when rotating (ECB) or unknown."""
    person = official(bank, speaker_id)
    if person is None:
        return None
    voter = person.get("voter")
    if voter == "always":
        return True
    if voter == "never":
        return False
    if isinstance(voter, dict):
        return voter.get(year)
    return None
