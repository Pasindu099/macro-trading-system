"""Day-bounded calendar history with one row per release bundle."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.processing.event_innovation import load_config


_OVERRIDES = yaml.safe_load(Path("config/calendar_overrides.yaml").read_text(encoding="utf-8"))
_HIGH = tuple(re.compile(pattern, re.I) for pattern in _OVERRIDES["high_impact_patterns"])
_CAL_BUNDLES = {(member, spec["country"]): spec
                for spec in _OVERRIDES.get("calendar_bundles", []) for member in spec["members"]}
# Event Innovation bundles: the highest-weighted member is the headline (e.g. payrolls on NFP day).
_BUNDLES = load_config().bundles
_BUNDLE_HEADLINES = {spec.bundle_key: max(spec.members, key=lambda m: m.weight).canonical_name
                     for spec in _BUNDLES}
_CONFIG_BUNDLES = {(member.canonical_name, spec.country): spec
                   for spec in _BUNDLES for member in spec.members}

_DAY_SQL = text("""
SELECT r.id, r.indicator_id, r.released_at, r.period, r.actual, r.estimate,
       r.previous, r.raw_payload, r.retrieved_at, i.display_name,
       i.canonical_name, i.importance, i.unit, i.country_code,
       i.is_higher_better_for_currency,
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
        source_name = raw.get("type")
        comparison = (raw.get("comparison") or "").lower()
        name = (f"{source_name} {'MoM' if comparison == 'mom' else 'YoY'}"
                if source_name and comparison in {"mom", "yoy"}
                else source_name or row["display_name"] or "Unmapped event")
        cal_bundle = _CAL_BUNDLES.get((row["canonical_name"], country))
        configured_bundle = _CONFIG_BUNDLES.get((row["canonical_name"], country))
        # Bundle members share the bundle's impact so components are never filtered apart.
        impact = impact_for(cal_bundle["name"] if cal_bundle else name,
                            raw.get("importance") or row["importance"])
        score = float(row["surprise_normalized"]) if row["surprise_normalized"] is not None else None
        stamp = row["released_at"]
        if row["bundle_id"]:
            key = ("bundle", row["bundle_id"])
        elif cal_bundle:
            key = ("calendar", cal_bundle["name"], country, stamp.date())
        elif configured_bundle:
            key = ("configured", configured_bundle.bundle_key, country, stamp.date())
        else:
            # Coincident releases can be unrelated (CPI and real earnings, for example).
            key = ("release", country, stamp, row["canonical_name"] or name.casefold(), row["period"])
        member = {
            "release_id": row["id"], "indicator_id": row["indicator_id"],
            "canonical_name": row["canonical_name"],
            "name": name, "actual": float(row["actual"]) if row["actual"] is not None else None,
            "consensus": float(row["estimate"]) if row["estimate"] is not None else None,
            "previous": float(row["previous"]) if row["previous"] is not None else None,
            "revised_from": float(row["first_previous"]) if row["first_previous"] is not None
                            and row["first_previous"] != row["previous"] else None,
            "surprise_sigma": score, "unit": row["unit"],
            "result": ("beat" if score > 0 else "miss" if score < 0 else "inline") if score is not None else
                      ("beat" if (row["actual"] - row["estimate"]) *
                       (1 if row["is_higher_better_for_currency"] else -1) > 0 else
                       "miss" if (row["actual"] - row["estimate"]) *
                       (1 if row["is_higher_better_for_currency"] else -1) < 0 else "inline")
                      if row["actual"] is not None and row["estimate"] is not None
                      and row["is_higher_better_for_currency"] is not None else None,
            "source_status": "confirmed" if row["actual"] is not None else "scheduled",
            "mapped": row["indicator_id"] is not None,
            "headline": bool((cal_bundle and cal_bundle["headline"] == row["canonical_name"])
                             or (configured_bundle and _BUNDLE_HEADLINES.get(configured_bundle.bundle_key)
                                 == row["canonical_name"])
                             or (row["bundle_key"] and _BUNDLE_HEADLINES.get(row["bundle_key"])
                                 == row["canonical_name"])),
            "impact": impact,
        }
        group = groups.setdefault(key, {
            "id": str(row["bundle_id"] or row["id"]), "released_at": stamp,
            "country_code": country, "currency_code": currency,
            "country_name": row["country_name"] or country,
            "name": ("Inflation Rate" if configured_bundle and configured_bundle.bundle_key == "US_CPI_DAY" else
                     (configured_bundle.bundle_key.split("_", 1)[1].removesuffix("_DAY")
                      .replace("_", " ") + " release") if configured_bundle else
                     (cal_bundle and cal_bundle["name"]) or name),
            "impact": impact, "components": [],
        })
        group["released_at"] = min(group["released_at"], stamp)
        group["impact"] = min(group["impact"], impact)
        group["components"].append(member)
    result = list(groups.values())
    for group in result:
        group["components"].sort(key=lambda item: (not item["headline"], not item["mapped"], item["impact"],
                                                   item["surprise_sigma"] is None,
                                                   -(abs(item["surprise_sigma"] or 0))))
        group.update({key: group["components"][0][key] for key in
                      ("actual", "consensus", "previous", "revised_from",
                       "surprise_sigma", "source_status", "result")})
    return [group for group in result if group["impact"] <= min_impact
            and (not surprise_only or any(c["surprise_sigma"] is not None
                                          and abs(c["surprise_sigma"]) > 1
                                          for c in group["components"]))]


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
