"""Word-list tone scoring, method lexicon_v1 (Step 12 Part F; no LLM). Rules in config/tone_lexicon.yaml."""

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
