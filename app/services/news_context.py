"""Short, evidence-backed news context; return no line when evidence is absent."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.services.overview import MANDATES, _situation_evidence


def why_line(item: dict[str, Any], *, tracking: dict[str, str] | None = None,
             episodes: dict[str, dict[str, Any]] | None = None,
             speaker_focus: dict[tuple[str, str], str] | None = None) -> str | None:
    tracking, episodes, speaker_focus = tracking or {}, episodes or {}, speaker_focus or {}
    currencies = item.get("currencies") or []
    currency = currencies[0] if currencies else None
    if item.get("news_type") == "Data" and item.get("actual") is not None and item.get("consensus") is not None:
        actual, consensus = Decimal(str(item["actual"])), Decimal(str(item["consensus"]))
        relation = "above" if actual > consensus else "below" if actual < consensus else "in line with"
        first = f"Actual {actual:g} vs {consensus:g} expected ({relation} consensus)."
        status = tracking.get(currency)
        return f"{first} {MANDATES[currency]}: {status}." if currency in MANDATES and status else first
    speakers = item.get("speakers") or []
    if item.get("news_type") == "CB speaker" and speakers:
        speaker = speakers[0]
        name = speaker.get("name")
        if not name:
            return None
        parts = [name]
        if speaker.get("role"):
            parts.append(speaker["role"])
        if speaker.get("voter") is True:
            parts.append("current voter")
        elif speaker.get("voter") is False:
            parts.append("non-voter")
        focus = speaker_focus.get((speaker.get("bank"), speaker.get("speaker_id")))
        if focus:
            parts.append(f"recent focus: {focus}")
        return " · ".join(parts) + "."
    for situation_id in item.get("situation_ids") or []:
        episode = episodes.get(situation_id)
        if episode:
            evidence = _situation_evidence(situation_id, episode.get("evidence") or {})
            if evidence != "Evidence pending" and not evidence.endswith("pending"):
                return f"{episode.get('name', situation_id)}: {evidence}."
    if item.get("news_type") == "Commodities":
        drivers = item.get("drivers") or []
        if drivers:
            named = [f"{d['driver'].replace('_', ' ')} {('↑' if d['direction'] == 'up' else '↓') if d.get('direction') in ('up', 'down') else d.get('direction') or ''}".strip()
                     for d in drivers if d.get("driver")]
            if named:
                return "Drivers named: " + ", ".join(named) + "."
    return None
