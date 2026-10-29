"""Shared deterministic takeaway contract and live Positioning readings."""
import re
from pathlib import Path

import yaml
from app.services.takeaways.positioning import NOW

PANEL_TEXT = {
    "summary": ("This brings together the main readings from the weekly COT report.", "Weekly reports describe past holdings rather than the timing of a trade."),
    "crowding": ("This compares hedge funds and asset managers with their own past bets.", "A crowded bet leaves less room for traders to add on the same side."),
    "flows": ("This shows which bets hedge funds added to or cut over the selected period.", "Adding to a bet on a currency and cutting a bet against it both shift the overall bet towards that currency."),
    "squeeze": ("This compares a crowded bet with price moves and changes in that bet.", "A squeeze puts pressure on traders to close positions when prices move against them."),
    "price": ("This compares the selected futures positions with the matching market price.", "Price and positioning describe different things and can move in different directions."),
    "extremes": ("This compares separate past episodes when hedge funds held a similar extreme bet.", "Past episodes show a range of outcomes rather than a promise about the next move."),
    "retail": ("This compares futures positions held by institutions with the side taken by retail traders.", "These groups cover different markets and their readings are not directly interchangeable."),
}


def glossary() -> dict[str, str]:
    return yaml.safe_load(Path("config/glossary.yaml").read_text(encoding="utf-8"))


def segments(sentence: str) -> list[dict]:
    definitions = glossary()
    terms = {term.casefold(): meaning for term, meaning in definitions.items()}
    pattern = re.compile(r"(?<!\w)(" + "|".join(re.escape(term) for term in sorted(definitions, key=len, reverse=True)) + r")(?!\w)", re.I)
    result, start = [], 0
    for match in pattern.finditer(sentence):
        if match.start() > start:
            result.append({"text": sentence[start:match.start()]})
        result.append({"text": match.group(), "definition": terms[match.group().casefold()]})
        start = match.end()
    if start < len(sentence):
        result.append({"text": sentence[start:]})
    return result


def _takeaway(panel: str, data: dict | None = None) -> dict:
    data = data or {}
    shows, watch = PANEL_TEXT[panel]
    state = data.get("state", "unavailable")
    if state == "stale" or data.get("stale"):
        now = "These inputs are stale, so a current reading is unavailable."
    elif state not in {"ok", "available", "partial", "not_extreme", "too_few"}:
        now = data.get("message") or "The inputs needed for a current reading are unavailable."
    else:
        now = NOW[panel](data)
    if panel == "extremes" and data.get("outcome_range_pct") and state == "available":
        bounds = data["outcome_range_pct"]
        watch = f"Past eight-week moves ranged from {bounds['min']:+.1f}% to {bounds['max']:+.1f}%; these are observations rather than a promise about the next move."
    plain = {"shows": shows, "now": now, "watch": watch}
    return {**plain, "state": state, "parts": {key: segments(value) for key, value in plain.items()}}


def summary(data=None): return _takeaway("summary", data)
def crowding(data=None): return _takeaway("crowding", data)
def flows(data=None): return _takeaway("flows", data)
def squeeze(data=None): return _takeaway("squeeze", data)
def price(data=None): return _takeaway("price", data)
def extremes(data=None): return _takeaway("extremes", data)
def retail(data=None): return _takeaway("retail", data)

REGISTRY = {"summary": summary, "crowding": crowding, "flows": flows, "squeeze": squeeze,
            "price": price, "extremes": extremes, "retail": retail}
COVERAGE = {"positioning": tuple(REGISTRY)}
