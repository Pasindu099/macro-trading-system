"""Meeting-priced scenarios with deterministic catalyst and situation text."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from app.db.session import get_sessionmaker
from app.services import cb_tracking
from app.services.desk_panels import upcoming_events
from app.services.desks import get_desk
from app.services.rate_probability import get_rate_probability_view
from app.services.verdict import get_verdict

CONFIG_PATH = Path("config/scenarios.yaml")


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _direction(value: int, config: dict[str, Any]) -> str:
    return config["direction_labels"][max(-1, min(1, value))]


def _evidence_text(row: dict[str, Any]) -> str:
    evidence = row.get("evidence") or {}
    values = evidence.get("current", evidence)
    return ", ".join(f"{name.replace('_', ' ')} {value:.1f}" for name, value in values.items()
                     if isinstance(value, (int, float))) or "source conditions remain active"


def build_scenarios(currency: str, verdict: dict[str, Any], market: dict[str, Any],
                    catalysts: list[str], tracking: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    if verdict.get("status") != "available":
        return {"currency": currency, "status": "unavailable", "reason": "Verdict unavailable", "scenarios": []}
    meetings = market.get("meetings") or []
    meeting = meetings[0] if meetings else {}
    live = bool(meeting.get("market_data_available")) and all(
        meeting.get(key) is not None for key in ("hold_prob", "hike_prob", "cut_prob"))
    if live:
        raw = [max(0.0, float(meeting[key])) for key in ("hold_prob", "hike_prob", "cut_prob")]
        total = sum(raw)
        live = total > 0
    probabilities = ([round(raw[0] / total * 100, 1), round(raw[1] / total * 100, 1)] if live else [])
    if live:
        probabilities.append(round(100 - sum(probabilities), 1))
    meeting_date = meeting.get("meeting_at")
    if isinstance(meeting_date, (datetime,)):
        meeting_date = meeting_date.date().isoformat()
    trigger = cfg["templates"]["meeting_trigger"].format(bank=market.get("bank", "central bank"),
                                                          meeting_date=meeting_date or "date unavailable")
    trigger += " " + (cfg["templates"]["catalyst_trigger"].format(catalyst=", ".join(catalysts[:3]))
                       if catalysts else cfg["templates"]["no_catalyst"])
    trigger += " " + cfg["templates"]["tracking_trigger"].format(tracking=tracking)
    bias_direction = cfg["directions"][verdict["bias"]]
    rows = []
    for index, key in enumerate(("base", "hawkish", "dovish")):
        rows.append({"id": key, "label": cfg["labels"][key],
                     "probability_pct": probabilities[index] if live else None,
                     "pricing_status": "priced" if live else "unavailable",
                     "direction": _direction(bias_direction + cfg["outcome_shift"][key], cfg),
                     "trigger": trigger, "horizon": verdict["horizon"]})
    for situation in verdict["active_situations"]:
        if situation["severity"] != "high":
            continue
        effect = situation.get("effect", 0)
        shift = 1 if effect > 0 else -1 if effect < 0 else 0
        rows.append({"id": f"situation:{situation['situation_id']}:{situation['scope_key']}",
                     "label": cfg["situation_labels"].get(situation["situation_id"], "Situation tail"),
                     "probability_pct": None, "pricing_status": "not priced",
                     "direction": _direction(bias_direction + shift, cfg),
                     "trigger": cfg["templates"]["situation_trigger"].format(
                         situation=situation["situation_id"].replace("_", " "),
                         evidence=_evidence_text(situation)), "horizon": verdict["horizon"]})
    return {"currency": currency, "status": "available", "meeting_at": meeting_date,
            "market_probabilities_available": live,
            "market_probability_reason": None if live else market.get("probability_reason", "No live next-meeting pricing"),
            "scenarios": rows}


async def get_scenarios(currency: str) -> dict[str, Any]:
    currency = currency.upper()
    desk = get_desk(currency)
    if desk is None or currency not in {"USD", "EUR"}:
        return {"currency": currency, "status": "unavailable", "reason": "No scenarios for this desk", "scenarios": []}
    verdict = await get_verdict(currency)
    async with get_sessionmaker()() as session:
        market = await get_rate_probability_view(desk["cb"], session)
    if currency == "EUR":
        # No verified ECB €STR OIS/futures curve exists; horizon approximation has no meeting odds.
        market = {**market, "meetings": [{**row, "market_data_available": False}
                                          for row in market.get("meetings", [])],
                  "probability_reason": "not priced: no verified €STR OIS or futures source"}
    countries = [member["country"] for member in desk.get("members", [])] or [desk["country"]]
    now = datetime.now(UTC)
    events = []
    for country in countries:
        events.extend(await upcoming_events(country))
    catalysts = [event.display_name for event in sorted(events, key=lambda event: event.released_at or now)
                 if event.importance == 1 and event.released_at and event.released_at > now]
    if currency == "USD":
        tracking = await cb_tracking.get_tracking()
        status = tracking.get("variables", {}).get("core_pce_inflation", {}).get("status", "unavailable")
    else:
        status = "unavailable pending ECB tracking"
    return build_scenarios(currency, verdict, market, catalysts, status)
