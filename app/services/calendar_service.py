"""Day-bounded calendar history with one row per release bundle."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


_OVERRIDES = yaml.safe_load(Path("config/calendar_overrides.yaml").read_text(encoding="utf-8"))
_HIGH = tuple(re.compile(pattern, re.I) for pattern in _OVERRIDES["high_impact_patterns"])

_DAY_SQL = text("""
SELECT r.id, r.indicator_id, r.released_at, r.period, r.actual, r.estimate,
       r.previous, r.raw_payload, r.retrieved_at, i.display_name,
       i.canonical_name, i.importance, i.unit, i.country_code,
       c.currency_code, c.name AS country_name, s.surprise_normalized,
       s.bundle_id, b.bundle_key,
       (SELECT old.previous FROM indicator_releases old
        WHERE old.indicator_id = r.indicator_id AND old.released_at = r.released_at
          AND old.period IS NOT DISTINCT FROM r.period AND old.id < r.id
        ORDER BY old.id ASC LIMIT 1) AS first_previous
FROM indicator_releases r
LEFT JOIN indicators i ON i.id = r.indicator_id
LEFT JOIN countries c ON c.code = COALESCE(i.country_code, r.raw_payload->>'country')
LEFT JOIN event_innovation_scores s ON s.release_id = r.id
LEFT JOIN release_bundles b ON b.id = s.bundle_id
WHERE r.released_at >= :start_at AND r.released_at < :end_at
  AND r.is_latest = true
ORDER BY r.released_at, r.id
""")


def impact_for(name: str, raw_importance: int | None) -> int:
    """1 = High, 2 = Medium, 3 = Low."""
    try:
        value = int(raw_importance) if raw_importance is not None else 3
    except (TypeError, ValueError):
        value = 3
    return 1 if any(pattern.search(name) for pattern in _HIGH) else max(1, min(3, value))


def _shape(rows: list[dict[str, Any]], currencies: set[str] | None,
           min_impact: int, surprise_only: bool) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        raw = row["raw_payload"] or {}
        country = row["country_code"] or raw.get("country")
        currency = row["currency_code"] or ("CNY" if country == "CN" else None)
        if not country or not currency or (currencies and currency not in currencies):
            continue
        name = row["display_name"] or raw.get("type") or "Unmapped event"
        impact = impact_for(name, raw.get("importance") or row["importance"])
        if impact > min_impact:
            continue
        score = float(row["surprise_normalized"]) if row["surprise_normalized"] is not None else None
        if surprise_only and (score is None or abs(score) <= 1):
            continue
        stamp = row["released_at"]
        key = ("bundle", row["bundle_id"]) if row["bundle_id"] else ("time", country, stamp)
        member = {
            "release_id": row["id"], "indicator_id": row["indicator_id"],
            "name": name, "actual": float(row["actual"]) if row["actual"] is not None else None,
            "consensus": float(row["estimate"]) if row["estimate"] is not None else None,
            "previous": float(row["previous"]) if row["previous"] is not None else None,
            "revised_from": float(row["first_previous"]) if row["first_previous"] is not None
                            and row["first_previous"] != row["previous"] else None,
            "surprise_sigma": score, "unit": row["unit"],
            "source_status": "confirmed" if row["actual"] is not None else "scheduled",
            "mapped": row["indicator_id"] is not None,
        }
        group = groups.setdefault(key, {
            "id": str(row["bundle_id"] or row["id"]), "released_at": stamp,
            "country_code": country, "currency_code": currency,
            "country_name": row["country_name"] or country,
            "name": row["bundle_key"] or name, "impact": impact, "components": [],
        })
        group["impact"] = min(group["impact"], impact)
        group["components"].append(member)
    result = list(groups.values())
    for group in result:
        group["components"].sort(key=lambda item: (item["surprise_sigma"] is None,
                                                   -(abs(item["surprise_sigma"] or 0))))
        group.update({key: group["components"][0][key] for key in
                      ("actual", "consensus", "previous", "revised_from",
                       "surprise_sigma", "source_status")})
    return result


async def list_events(session: AsyncSession, start: date, end: date,
                      currencies: set[str] | None = None, min_impact: int = 3,
                      *, day: date | None = None, surprise_only: bool = False
                      ) -> list[dict[str, Any]]:
    """Read one day at a time; ``day`` serves one progressive page."""
    if end < start or (day is not None and not start <= day <= end):
        raise ValueError("Invalid calendar range")
    days = [day] if day else (start + timedelta(days=n) for n in range((end - start).days + 1))
    events: list[dict[str, Any]] = []
    for current in days:
        start_at = datetime.combine(current, time.min, tzinfo=timezone.utc)
        rows = (await session.execute(_DAY_SQL, {
            "start_at": start_at, "end_at": start_at + timedelta(days=1),
        })).mappings().all()
        events.extend(_shape([dict(row) for row in rows], currencies, min_impact, surprise_only))
    return events
