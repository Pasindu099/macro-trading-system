"""Word-list tone scoring, methods lexicon_v1 and lexicon_v2 (Step 12; no LLM). Rules in config/tone_lexicon.yaml."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path("config/tone_lexicon.yaml")
TOKEN = re.compile(r"[a-z0-9]+(?:['’-][a-z0-9]+)*|n't")


def tokens(text: str) -> list[str]:
    return TOKEN.findall(text.lower().replace("’", "'"))


@lru_cache(maxsize=1)
def lexicon() -> dict[str, Any]:
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    phrases = [(tuple(tokens(p)), float(w), "hawkish") for p, w in cfg["hawkish"].items()]
    phrases += [(tuple(tokens(p)), float(w), "dovish") for p, w in cfg["dovish"].items()]
    phrases.sort(key=lambda item: -len(item[0]))           # longest first
    return {**cfg, "method": cfg["method"], "phrases": phrases,
            "negators": [tuple(tokens(n)) for n in cfg["negators"]],
            "hedges": {h.lower() for h in cfg["hedges"]},
            "scope": {" ".join(tokens(s)) for s in cfg.get("scope_negators", [])}}


def _matches_at(words: list[str], i: int, phrase: tuple[str, ...]) -> bool:
    if i + len(phrase) > len(words):
        return False
    *head, last = phrase
    return list(words[i:i + len(head)]) == head and words[i + len(head)].startswith(last)


def _negated(words: list[str], start: int, window: int, negators: list[tuple[str, ...]]) -> bool:
    before = words[max(0, start - window):start]
    for negator in negators:
        n = len(negator)
        if any(tuple(before[j:j + n]) == negator for j in range(len(before) - n + 1)):
            return True
    return any(word.endswith("n't") for word in before)


def score_sentence(sentence: str) -> dict[str, Any]:
    cfg = lexicon()
    words = tokens(sentence)
    used = [False] * len(words)
    hits = []
    for phrase, weight, side in cfg["phrases"]:
        for i in range(len(words)):
            if not _matches_at(words, i, phrase) or any(used[i:i + len(phrase)]):
                continue
            negated = _negated(words, i, int(cfg["negation_window"]), cfg["negators"])
            hedged = any(w in cfg["hedges"] for w in words[max(0, i - int(cfg["hedge_window"])):i])
            applied = weight * (-1 if negated else 1) * (float(cfg["hedge_factor"]) if hedged else 1)
            for k in range(i, i + len(phrase)):
                used[k] = True
            hits.append({"phrase": " ".join(phrase), "side": side, "weight": weight, "negated": negated,
                         "hedged": hedged, "applied": round(applied, 3), "start": i})
    scopes = [h["start"] for h in hits if h["phrase"] in cfg["scope"]]
    if scopes:
        for hit in hits:
            if hit["start"] > min(scopes) and hit["phrase"] not in cfg["scope"]:
                hit["negated"] = not hit["negated"]
                hit["applied"] = -hit["applied"]
    hits.sort(key=lambda h: h["start"])
    raw = sum(hit["applied"] for hit in hits)
    clip = float(cfg["clip"])
    return {"method": cfg["method"], "score": round(max(-clip, min(clip, raw)), 3), "raw": round(raw, 3),
            "hits": hits}


# ── lexicon_v2: v1 hits kept only in forward-looking / normative, non-descriptive context ──

def _contains(words: list[str], phrase: tuple[str, ...]) -> int | None:
    n = len(phrase)
    return next((i for i in range(len(words) - n + 1) if tuple(words[i:i + n]) == phrase), None)


@lru_cache(maxsize=1)
def v2_rules() -> dict[str, Any]:
    cfg = lexicon()["v2"]
    as_tuples = lambda key: [tuple(tokens(item)) for item in cfg[key]]  # noqa: E731
    return {"method": cfg["method"], "forward": as_tuples("forward_markers"), "first_person": set(cfg["first_person"]),
            "view_verbs": set(cfg["view_verbs"]), "self_normative": {" ".join(tokens(p)) for p in cfg["self_normative"]},
            "past": as_tuples("past_markers"), "descriptive_after": set(cfg["descriptive_after"]),
            "market": as_tuples("market_pricing"), "third_party": as_tuples("third_party")}


def sentence_context(sentence: str) -> tuple[str | None, bool]:
    """(reason the whole sentence is ignored or None, forward/normative context present)."""
    rules, words = v2_rules(), tokens(sentence)
    if sentence.rstrip().endswith("?"):
        return "question", False
    if any(_contains(words, p) is not None for p in rules["market"]):
        return "market pricing", False
    if any(_contains(words, p) is not None for p in rules["third_party"]):
        return "third party", False
    forward = any(_contains(words, p) is not None for p in rules["forward"])
    first_person = any(w in rules["first_person"] and any(v in rules["view_verbs"] for v in words[i + 1:i + 4])
                       for i, w in enumerate(words))
    return None, forward or first_person


def _descriptive(words: list[str], start: int, length: int, rules: dict[str, Any]) -> bool:
    before = words[max(0, start - 3):start]
    if any(_contains(before, p) is not None for p in rules["past"]):
        return True
    after = words[start + length] if start + length < len(words) else ""
    return after in rules["descriptive_after"] or (after == "of" and start > 0 and words[start - 1] == "the")


def score_sentence_v2(sentence: str) -> dict[str, Any]:
    rules, cfg = v2_rules(), lexicon()
    base = score_sentence(sentence)
    skip, forward = sentence_context(sentence)
    words = tokens(sentence)
    kept, dropped = [], []
    for hit in base["hits"]:
        length = len(hit["phrase"].split())
        if skip:
            reason = skip
        elif _descriptive(words, hit["start"], length, rules):
            reason = "descriptive/past"
        elif not (forward or hit["phrase"] in rules["self_normative"]):
            reason = "no forward/normative context"
        else:
            kept.append(hit)
            continue
        dropped.append({**hit, "dropped": reason})
    raw = sum(hit["applied"] for hit in kept)
    clip = float(cfg["clip"])
    return {"method": rules["method"], "score": round(max(-clip, min(clip, raw)), 3), "raw": round(raw, 3),
            "hits": kept, "dropped": dropped}


SCORERS = {"lexicon_v1": score_sentence, "lexicon_v2": score_sentence_v2}
