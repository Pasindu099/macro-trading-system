"""Manual news-event + price-reaction logging for the event-trading dataset.

Data entry flow: pick a calendar release -> forecast/actual typed by hand,
previous_value looked up from the prior release -> AI interpretation
generated on demand -> price reactions pasted in as raw prices, with pip/pct
change computed server-side against the event's own t0 price.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import and_, desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Country,
    EventPriceReaction,
    EventReactionNote,
    Indicator,
    IndicatorRelease,
)
from app.settings import get_settings

logger = logging.getLogger(__name__)

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

INSTRUMENTS = ["DXY", "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"]
HORIZONS = ["t0", "1m", "5m", "15m", "1h", "4h", "24h"]

_IMPORTANCE_LABELS = {1: "High", 2: "Medium", 3: "Low"}

_SYSTEM_PROMPT = (
    "You are a macro FX analyst. Given one economic data release (actual vs "
    "forecast vs previous), write a short, concrete interpretation covering: "
    "(1) what it implies for the relevant central bank's monetary policy path, "
    "and (2) the likely near-term market reaction for that currency. Two to "
    "four sentences, no hedging filler, no generic disclaimers. Plain text, "
    "not JSON."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def list_candidate_releases(
    session: AsyncSession,
    *,
    release_date: date,
    country_code: str | None = None,
    importance: int | None = None,
) -> list[dict[str, Any]]:
    """Releases on a given day, for the event picker dropdown."""
    filters = [
        IndicatorRelease.released_at >= datetime.combine(release_date, datetime.min.time(), tzinfo=timezone.utc),
        IndicatorRelease.released_at < datetime.combine(release_date, datetime.max.time(), tzinfo=timezone.utc),
        IndicatorRelease.indicator_id.is_not(None),
    ]
    if country_code:
        filters.append(Indicator.country_code == country_code.upper())
    if importance is not None:
        filters.append(Indicator.importance == importance)

    result = await session.execute(
        select(IndicatorRelease, Indicator, Country, EventReactionNote.id)
        .join(Indicator, Indicator.id == IndicatorRelease.indicator_id)
        .join(Country, Country.code == Indicator.country_code)
        .outerjoin(
            EventReactionNote,
            EventReactionNote.indicator_release_id == IndicatorRelease.id,
        )
        .where(*filters)
        .order_by(IndicatorRelease.released_at.asc())
    )

    # indicator_releases can have re-ingestion duplicates (same indicator +
    # timestamp, different row id) — collapse to one candidate per real-world
    # event, preferring whichever duplicate already has a logged note.
    deduped: dict[tuple[int, Any], dict[str, Any]] = {}
    for release, indicator, country, existing_note_id in result.all():
        key = (indicator.id, release.released_at)
        candidate = {
            "release_id": release.id,
            "indicator_name": indicator.display_name,
            "country_code": country.code,
            "country_name": country.name,
            "currency_code": country.currency_code,
            "importance": indicator.importance,
            "importance_label": _IMPORTANCE_LABELS.get(indicator.importance, "—"),
            "released_at": release.released_at,
            "existing_note_id": existing_note_id,
        }
        current = deduped.get(key)
        if current is None or (existing_note_id is not None and current["existing_note_id"] is None):
            deduped[key] = candidate

    return sorted(deduped.values(), key=lambda c: c["released_at"])


async def _lookup_previous_value(
    session: AsyncSession, indicator_id: int, before: datetime
) -> Decimal | None:
    result = await session.execute(
        select(IndicatorRelease.actual)
        .where(
            and_(
                IndicatorRelease.indicator_id == indicator_id,
                IndicatorRelease.released_at < before,
                IndicatorRelease.actual.is_not(None),
            )
        )
        .order_by(desc(IndicatorRelease.released_at))
        .limit(1)
    )
    row = result.first()
    return row[0] if row else None


async def get_or_create_note(
    session: AsyncSession, indicator_release_id: int
) -> EventReactionNote:
    existing = await session.execute(
        select(EventReactionNote).where(
            EventReactionNote.indicator_release_id == indicator_release_id
        )
    )
    note = existing.scalar_one_or_none()
    if note is not None:
        return note

    release = await session.get(IndicatorRelease, indicator_release_id)
    if release is None:
        raise ValueError(f"indicator_release {indicator_release_id} not found")

    previous_value = None
    if release.indicator_id is not None:
        previous_value = await _lookup_previous_value(
            session, release.indicator_id, release.released_at
        )

    note = EventReactionNote(
        indicator_release_id=indicator_release_id,
        previous_value=previous_value,
    )
    session.add(note)
    await session.flush()
    return note


_UPDATABLE_NOTE_FIELDS = {
    "forecast_value", "actual_value", "previous_value", "manual_notes", "ai_interpretation",
}


async def update_note_values(
    session: AsyncSession, note_id: int, updates: dict[str, Any]
) -> EventReactionNote:
    """Apply only the fields present in `updates` — lets a client explicitly clear a field to null."""
    note = await session.get(EventReactionNote, note_id)
    if note is None:
        raise ValueError(f"event note {note_id} not found")
    for field, value in updates.items():
        if field in _UPDATABLE_NOTE_FIELDS:
            setattr(note, field, value)
    note.updated_at = _now()
    await session.flush()
    return note


async def _call_openai_interpretation(prompt: str) -> str | None:
    settings = get_settings()
    if not settings.openai_api_key:
        logger.error("OPENAI_API_KEY not set — skipping AI interpretation")
        return None
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                OPENAI_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.openai_model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 300,
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("OpenAI request failed: %s", exc)
        return None

    return (
        resp.json().get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    ) or None


def _build_interpretation_prompt(
    indicator_name: str,
    country_name: str,
    currency_code: str,
    actual: Decimal | None,
    forecast: Decimal | None,
    previous: Decimal | None,
) -> str:
    return (
        f"Indicator: {indicator_name} ({country_name}, {currency_code})\n"
        f"Actual: {actual if actual is not None else 'pending'}\n"
        f"Forecast: {forecast if forecast is not None else 'n/a'}\n"
        f"Previous: {previous if previous is not None else 'n/a'}"
    )


async def generate_ai_interpretation(session: AsyncSession, note_id: int) -> EventReactionNote:
    note = await session.get(EventReactionNote, note_id)
    if note is None:
        raise ValueError(f"event note {note_id} not found")

    release = await session.get(IndicatorRelease, note.indicator_release_id)
    indicator = await session.get(Indicator, release.indicator_id) if release else None
    country = await session.get(Country, indicator.country_code) if indicator else None

    prompt = _build_interpretation_prompt(
        indicator_name=indicator.display_name if indicator else "Unknown indicator",
        country_name=country.name if country else "Unknown",
        currency_code=country.currency_code if country else "?",
        actual=note.actual_value,
        forecast=note.forecast_value,
        previous=note.previous_value,
    )

    interpretation = await _call_openai_interpretation(prompt)
    if interpretation:
        note.ai_interpretation = interpretation
        note.ai_generated_at = _now()
        note.updated_at = _now()
        await session.flush()
    return note


def _pip_size(instrument: str) -> Decimal | None:
    if instrument == "DXY":
        return None
    return Decimal("0.01") if "JPY" in instrument else Decimal("0.0001")


async def upsert_price_cell(
    session: AsyncSession,
    event_id: int,
    instrument: str,
    horizon: str,
    raw_price: Decimal,
) -> list[EventPriceReaction]:
    """Save one grid cell and (re)compute pip/pct change for the instrument's row.

    Recomputes every filled cell for this (event, instrument) whenever the
    cell that changed is t0, since t0 is the baseline every other horizon is
    diffed against.
    """
    if instrument not in INSTRUMENTS or horizon not in HORIZONS:
        raise ValueError(f"unknown instrument/horizon: {instrument}/{horizon}")

    stmt = (
        pg_insert(EventPriceReaction)
        .values(
            event_id=event_id,
            instrument=instrument,
            horizon=horizon,
            raw_price=raw_price,
            updated_at=_now(),
        )
        .on_conflict_do_update(
            index_elements=["event_id", "instrument", "horizon"],
            set_={"raw_price": raw_price, "updated_at": _now()},
        )
    )
    await session.execute(stmt)
    await session.flush()

    return await _recompute_instrument_row(session, event_id, instrument)


async def _recompute_instrument_row(
    session: AsyncSession, event_id: int, instrument: str
) -> list[EventPriceReaction]:
    result = await session.execute(
        select(EventPriceReaction).where(
            and_(
                EventPriceReaction.event_id == event_id,
                EventPriceReaction.instrument == instrument,
            )
        )
    )
    rows = {row.horizon: row for row in result.scalars().all()}

    t0_row = rows.get("t0")
    pip_size = _pip_size(instrument)

    for row in rows.values():
        if row.raw_price is None:
            continue
        if row.horizon == "t0" or t0_row is None or t0_row.raw_price is None:
            row.pip_change = Decimal("0") if row.horizon == "t0" and row.raw_price else None
            row.pct_change = Decimal("0") if row.horizon == "t0" and row.raw_price else None
            continue
        diff = row.raw_price - t0_row.raw_price
        row.pip_change = (diff / pip_size).quantize(Decimal("0.01")) if pip_size else None
        row.pct_change = (
            (diff / t0_row.raw_price) * Decimal("100")
        ).quantize(Decimal("0.0001"))

    await session.flush()
    return list(rows.values())


async def get_event_detail(session: AsyncSession, note_id: int) -> dict[str, Any] | None:
    note = await session.get(EventReactionNote, note_id)
    if note is None:
        return None

    release = await session.get(IndicatorRelease, note.indicator_release_id)
    indicator = await session.get(Indicator, release.indicator_id) if release else None
    country = await session.get(Country, indicator.country_code) if indicator else None

    reactions_result = await session.execute(
        select(EventPriceReaction).where(EventPriceReaction.event_id == note.id)
    )
    grid: dict[str, dict[str, Any]] = {
        instrument: {horizon: None for horizon in HORIZONS} for instrument in INSTRUMENTS
    }
    for row in reactions_result.scalars().all():
        if row.instrument in grid:
            grid[row.instrument][row.horizon] = {
                "raw_price": row.raw_price,
                "pip_change": row.pip_change,
                "pct_change": row.pct_change,
            }

    return {
        "note": note,
        "release": release,
        "indicator": indicator,
        "country": country,
        "grid": grid,
        "instruments": INSTRUMENTS,
        "horizons": HORIZONS,
    }


async def list_events(
    session: AsyncSession, *, limit: int = 100
) -> list[dict[str, Any]]:
    result = await session.execute(
        select(EventReactionNote, IndicatorRelease, Indicator, Country)
        .join(IndicatorRelease, IndicatorRelease.id == EventReactionNote.indicator_release_id)
        .join(Indicator, Indicator.id == IndicatorRelease.indicator_id)
        .join(Country, Country.code == Indicator.country_code)
        .order_by(desc(IndicatorRelease.released_at))
        .limit(limit)
    )

    rows = result.all()
    if not rows:
        return []

    note_ids = [note.id for note, _, _, _ in rows]
    filled_result = await session.execute(
        select(EventPriceReaction.event_id)
        .where(
            and_(
                EventPriceReaction.event_id.in_(note_ids),
                EventPriceReaction.raw_price.is_not(None),
            )
        )
    )
    filled_counts: dict[int, int] = {}
    for (event_id,) in filled_result.all():
        filled_counts[event_id] = filled_counts.get(event_id, 0) + 1

    total_cells = len(INSTRUMENTS) * len(HORIZONS)
    events = []
    for note, release, indicator, country in rows:
        events.append(
            {
                "note_id": note.id,
                "indicator_name": indicator.display_name,
                "country_code": country.code,
                "currency_code": country.currency_code,
                "importance_label": _IMPORTANCE_LABELS.get(indicator.importance, "—"),
                "released_at": release.released_at,
                "forecast_value": note.forecast_value,
                "actual_value": note.actual_value,
                "previous_value": note.previous_value,
                "has_ai_interpretation": note.ai_interpretation is not None,
                "cells_filled": filled_counts.get(note.id, 0),
                "cells_total": total_cells,
            }
        )
    return events
