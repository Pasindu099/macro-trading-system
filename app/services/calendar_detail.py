"""Calendar event detail panel: header, history, components and release-day reactions."""

from __future__ import annotations

from urllib.parse import quote

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.calendar_service import list_events
from app.services.desks import get_desk
from app.services.event_reactions import MIN_RELEASES, pairs_for, release_moves, stats_for
from app.services.overview import _event_tracking, _event_why, event_family

HISTORY_LENGTH = 24
CALENDAR_ONLY_WHY = {"CNY": "Affects AUD, NZD via China demand."}

_HISTORY_SQL = text("""
SELECT r.id, r.released_at, r.period, r.actual::float AS actual, r.estimate::float AS consensus,
       s.surprise_normalized::float AS sigma,
       (SELECT first.actual::float FROM indicator_releases first
        WHERE first.indicator_id = r.indicator_id AND first.actual IS NOT NULL
          AND first.period IS NOT DISTINCT FROM r.period
          AND (r.period IS NOT NULL OR first.released_at = r.released_at)
        ORDER BY first.id ASC LIMIT 1) AS first_actual
FROM indicator_releases r
LEFT JOIN event_innovation_scores s ON s.release_id = r.id
WHERE r.indicator_id = :indicator_id AND r.is_latest AND r.released_at <= :at
ORDER BY r.released_at DESC
LIMIT :limit
""")

_NOTES_SQL = text("""
SELECT n.id, n.manual_notes, n.outcome_after, n.ai_note_final, n.created_at
FROM event_reaction_notes n
WHERE n.indicator_release_id = ANY(:ids)
ORDER BY n.created_at DESC
""")


def shape_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Oldest first; ``first_print`` is the first stored actual, ``revised`` when it differs."""
    history = []
    for row in reversed(rows):
        first = row["first_actual"]
        history.append({
            "release_id": row["id"], "released_at": row["released_at"], "period": row["period"],
            "actual": row["actual"], "first_print": first if row["actual"] is not None else None,
            "consensus": row["consensus"], "sigma": row["sigma"],
            "revised": first is not None and row["actual"] is not None and first != row["actual"],
        })
    return history


def chart_data(history: list[dict[str, Any]]) -> dict[str, list[Any]]:
    """ECharts series; an upcoming release keeps its slot with empty actuals."""
    return {
        "labels": [row["period"] or row["released_at"].strftime("%Y-%m-%d") for row in history],
        "actual": [row["actual"] for row in history],
        "first_print": [row["first_print"] for row in history],
        "consensus": [row["consensus"] for row in history],
        "sigma": [row["sigma"] for row in history],
    }


def shape_reactions(moves: list[dict[str, Any]], stats: dict[str, dict[str, Any]],
                    pairs: list[str], release_id: int) -> dict[str, Any]:
    by_release: dict[int, dict[str, Any]] = {}
    for move in moves:
        entry = by_release.setdefault(move["release_id"], {
            "release_id": move["release_id"], "released_at": move["released_at"],
            "period": move["period"], "sigma": move["sigma"], "moves": {}})
        entry["moves"][move["pair"]] = move["move_pips"]
    releases = sorted(by_release.values(), key=lambda item: item["released_at"], reverse=True)
    summary = []
    for pair in pairs:
        stat = stats.get(pair)
        enough = bool(stat and stat["sample_n"] >= MIN_RELEASES)
        summary.append({"pair": pair, "stats": stat if enough else None,
                        "sample_n": stat["sample_n"] if stat else 0,
                        "this_move": by_release.get(release_id, {}).get("moves", {}).get(pair)})
    return {"pairs": pairs, "summary": summary, "releases": releases[:HISTORY_LENGTH],
            "min_releases": MIN_RELEASES}


def why_it_matters(currency: str, components: list[dict[str, Any]], tracking: dict[str, str]) -> str:
    if currency in CALENDAR_ONLY_WHY:
        return CALENDAR_ONLY_WHY[currency]
    items = [{"canonical_name": c.get("canonical_name") or "", "display_name": c["name"]} for c in components]
    return _event_why({"currency_code": currency, "family": event_family(items)},
                      tracking.get(currency, "tracking pending"))


async def find_event(session: AsyncSession, release_id: int) -> dict[str, Any] | None:
    released_at = (await session.execute(text(
        "SELECT released_at FROM indicator_releases WHERE id = :id"), {"id": release_id})).scalar_one_or_none()
    if released_at is None:
        return None
    day = released_at.astimezone(timezone.utc).date()
    for event in await list_events(session, day, day, None, 3):
        if any(c["release_id"] == release_id for c in event["components"]):
            return event
    return None


async def event_detail(session: AsyncSession, release_id: int,
                       tracking: dict[str, str] | None = None) -> dict[str, Any] | None:
    event = await find_event(session, release_id)
    if event is None:
        return None
    headline = next(c for c in event["components"] if c["release_id"] == release_id)
    event = event | {key: headline[key] for key in
                     ("name", "actual", "consensus", "previous", "revised_from",
                      "surprise_sigma", "source_status")}
    currency = event["currency_code"]
    indicator_id = headline["indicator_id"]
    upcoming = event["actual"] is None and event["released_at"] > datetime.now(timezone.utc)
    history: list[dict[str, Any]] = []
    reactions = {"pairs": pairs_for(currency), "summary": [], "releases": [], "min_releases": MIN_RELEASES}
    if indicator_id is not None:
        rows = (await session.execute(_HISTORY_SQL, {
            "indicator_id": indicator_id, "at": event["released_at"], "limit": HISTORY_LENGTH,
        })).mappings().all()
        history = shape_history([dict(row) for row in rows])
        pairs = pairs_for(currency)
        moves = await release_moves(session, [indicator_id], currency, pairs)
        reactions = shape_reactions(moves, await stats_for(session, indicator_id), pairs,
                                    headline["release_id"])
    member_ids = [c["release_id"] for c in event["components"]]
    notes = [dict(row) for row in (await session.execute(_NOTES_SQL, {"ids": member_ids})).mappings().all()]
    if tracking is None and currency not in CALENDAR_ONLY_WHY:
        tracking = await _event_tracking()
    return {
        "event": event, "headline": headline, "upcoming": upcoming,
        "why": why_it_matters(currency, event["components"], tracking or {}),
        "history": history, "chart": chart_data(history), "reactions": reactions, "notes": notes,
        "single_indicator": len(event["components"]) == 1,
        "links": {
            "desk": f"/desks/{currency}" if get_desk(currency) else None,
            "pairs": [{"pair": pair, "href": "/pairs/" + pair.replace("/", "")}
                      for pair in reactions["pairs"]],
            "data": (f"/data/indicator/{event['country_code']}/{quote(headline['canonical_name'], safe='')}"
                     if indicator_id and headline.get("canonical_name") else None),
            "event_log": f"/calendar/event-log/new?release_id={headline['release_id']}",
        },
    }
