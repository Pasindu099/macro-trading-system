"""Admin API routes.

Endpoints for operational visibility — NOT for end-user consumption.
Mounted at /api/admin/* in main.py. No auth in Phase 1 (local-only).

Routes:
    GET /api/admin/health            — overall system status
    GET /api/admin/ingestion-runs    — paginated ingestion run history
    GET /api/admin/unmapped-events   — unmapped event types awaiting YAML mapping
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import event_reaction_log
from app.api.schemas import (
    AdminHealthPayload,
    CountryHealth,
    Envelope,
    IngestionRunSummary,
    IngestionRunsPayload,
    ResponseMeta,
    UnmappedEventGroup,
    UnmappedEventsPayload,
)
from app.db.models import (
    Country,
    GovernmentYieldIngestionStatus,
    Indicator,
    IndicatorRelease,
    IngestionRun,
)
from app.db.session import get_session
from app.settings import get_settings

router = APIRouter(prefix="/api/admin", tags=["admin"])


class IndicatorSeriesRefreshRequest(BaseModel):
    """Admin maintenance request for one canonical indicator series."""

    country_code: str = Field(min_length=2, max_length=2)
    canonical_name: str = Field(min_length=1, max_length=200)
    repair_latest_flags: bool = True


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _meta(cache_hint: str | None = None) -> ResponseMeta:
    return ResponseMeta(generated_at=_now(), cache_hint=cache_hint)


def _build_unmapped_where(country: str | None) -> tuple[str, dict[str, str]]:
    """Build a shared WHERE clause for unmapped-event admin queries."""
    where_clauses = ["indicator_id IS NULL"]
    params: dict[str, str] = {}

    normalized_country = country.upper() if country else None
    if normalized_country:
        where_clauses.append("raw_payload->>'country' = :country")
        params["country"] = normalized_country

    return " AND ".join(where_clauses), params


def _series_group_key(release: IndicatorRelease) -> tuple[object, ...]:
    return (
        release.period,
        release.period_start_date,
        release.released_at
        if release.period is None and release.period_start_date is None
        else None,
    )


def _pick_series_latest(releases: list[IndicatorRelease]) -> IndicatorRelease:
    actual_latest_rows = [
        release for release in releases
        if release.actual is not None and release.is_latest
    ]
    actual_rows = [release for release in releases if release.actual is not None]
    eligible = actual_latest_rows or actual_rows or releases
    return sorted(
        eligible,
        key=lambda release: (
            release.retrieved_at or release.released_at,
            release.id,
        ),
        reverse=True,
    )[0]


# ══════════════════════════════════════════════════════════════════════
#  GET /api/admin/health
# ══════════════════════════════════════════════════════════════════════

@router.get("/health", response_model=Envelope[AdminHealthPayload])
async def admin_health(
    session: AsyncSession = Depends(get_session),
) -> Envelope[AdminHealthPayload]:
    """Overall system status snapshot."""
    settings = get_settings()
    cutoff_24h = _now() - timedelta(hours=24)

    # DB connectivity check — if this query runs, DB is OK.
    db_ok = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — we want any DB error to show as unhealthy
        db_ok = False

    # Ingestion run counts (last 24h)
    runs_24h_q = await session.execute(
        select(func.count(IngestionRun.id)).where(
            IngestionRun.started_at >= cutoff_24h
        )
    )
    total_runs_24h = runs_24h_q.scalar_one()

    failed_runs_q = await session.execute(
        select(func.count(IngestionRun.id)).where(
            and_(
                IngestionRun.started_at >= cutoff_24h,
                IngestionRun.status == "failed",
            )
        )
    )
    failed_runs_24h = failed_runs_q.scalar_one()

    # Unmapped event types count (distinct raw types)
    # indicator_id IS NULL + raw_payload->>type is distinct
    unmapped_q = await session.execute(
        text("""
            SELECT COUNT(DISTINCT raw_payload->>'type')
            FROM indicator_releases
            WHERE indicator_id IS NULL
        """)
    )
    unmapped_types_count = unmapped_q.scalar_one() or 0

    # Per-country health
    countries_q = await session.execute(
        select(Country).order_by(Country.code)
    )
    countries = countries_q.scalars().all()

    per_country: list[CountryHealth] = []
    for c in countries:
        # Indicator count
        ind_q = await session.execute(
            select(func.count(Indicator.id)).where(
                Indicator.country_code == c.code
            )
        )
        ind_count = ind_q.scalar_one()

        # Release count for this country's mapped indicators
        rel_q = await session.execute(
            select(func.count(IndicatorRelease.id))
            .select_from(IndicatorRelease)
            .join(Indicator, Indicator.id == IndicatorRelease.indicator_id)
            .where(Indicator.country_code == c.code)
        )
        rel_count = rel_q.scalar_one()

        # Last successful run that included this country
        # countries_fetched is an ARRAY, Postgres: use ANY operator
        last_run_q = await session.execute(
            select(IngestionRun.started_at, IngestionRun.status)
            .where(
                and_(
                    IngestionRun.status.in_(["success", "partial"]),
                    text(":code = ANY(countries_fetched)").bindparams(code=c.code),
                )
            )
            .order_by(desc(IngestionRun.started_at))
            .limit(1)
        )
        last_row = last_run_q.first()
        last_run_time = last_row[0] if last_row else None
        last_run_status = last_row[1] if last_row else None

        per_country.append(CountryHealth(
            country_code=c.code,
            last_successful_run=last_run_time,
            last_run_status=last_run_status,
            indicator_count=ind_count,
            release_count=rel_count,
        ))

    payload = AdminHealthPayload(
        database_ok=db_ok,
        scheduler_enabled=settings.enable_scheduler,
        ingestion_runs_last_24h=total_runs_24h,
        failed_runs_last_24h=failed_runs_24h,
        unmapped_event_types_count=unmapped_types_count,
        countries=per_country,
    )
    return Envelope(data=payload, meta=_meta())


# ══════════════════════════════════════════════════════════════════════
#  GET /api/admin/ingestion-runs
# ══════════════════════════════════════════════════════════════════════

@router.get("/ingestion-runs", response_model=Envelope[IngestionRunsPayload])
async def admin_ingestion_runs(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    run_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> Envelope[IngestionRunsPayload]:
    """Paginated list of recent ingestion runs.

    Query params:
        limit: max rows to return (1-200, default 20)
        offset: pagination offset
        run_type: filter by 'scheduled_asia', 'scheduled_london', ...
        status: filter by 'success', 'failed', 'partial', 'running'
    """
    # Build base filter
    filters = []
    if run_type:
        filters.append(IngestionRun.run_type == run_type)
    if status:
        filters.append(IngestionRun.status == status)

    # Total count (before pagination)
    count_q = select(func.count(IngestionRun.id))
    if filters:
        count_q = count_q.where(and_(*filters))
    total = (await session.execute(count_q)).scalar_one()

    # Page
    data_q = select(IngestionRun).order_by(desc(IngestionRun.started_at))
    if filters:
        data_q = data_q.where(and_(*filters))
    data_q = data_q.limit(limit).offset(offset)

    rows = (await session.execute(data_q)).scalars().all()

    runs = [
        IngestionRunSummary(
            id=r.id,
            started_at=r.started_at,
            finished_at=r.finished_at,
            run_type=r.run_type,
            countries_fetched=list(r.countries_fetched or []),
            events_inserted=r.events_inserted,
            events_updated=r.events_updated,
            api_calls_used=r.api_calls_used,
            status=r.status,
            has_errors=bool(r.errors),
        )
        for r in rows
    ]

    payload = IngestionRunsPayload(
        total=total,
        limit=limit,
        offset=offset,
        runs=runs,
    )
    return Envelope(data=payload, meta=_meta())


@router.get("/government-yields/status")
async def admin_government_yield_status(
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Operational status for durable EODHD government-yield ingestion."""
    rows = (
        await session.execute(
            select(GovernmentYieldIngestionStatus).order_by(
                GovernmentYieldIngestionStatus.job_name,
            )
        )
    ).scalars().all()
    return {
        "jobs": [
            {
                "job_name": row.job_name,
                "status": row.status,
                "last_attempted_at": row.last_attempted_at,
                "last_successful_at": row.last_successful_at,
                "next_scheduled_run": row.next_scheduled_run,
                "observations_inserted": row.observations_inserted,
                "observations_seen": row.observations_seen,
                "symbols_missing": list(row.symbols_missing or []),
                "stale_symbols": list(row.stale_symbols or []),
                "errors": row.errors or {},
                "updated_at": row.updated_at,
            }
            for row in rows
        ],
        "meta": {"generated_at": _now()},
    }


# ══════════════════════════════════════════════════════════════════════
#  GET /api/admin/unmapped-events
# ══════════════════════════════════════════════════════════════════════

@router.get("/unmapped-events", response_model=Envelope[UnmappedEventsPayload])
async def admin_unmapped_events(
    limit: int = Query(default=100, ge=1, le=500),
    country: str | None = Query(default=None),
    min_count: int = Query(
        default=1, ge=1,
        description="Only return event types with at least this many releases",
    ),
    session: AsyncSession = Depends(get_session),
) -> Envelope[UnmappedEventsPayload]:
    """Distinct EODHD event types that don't yet have a canonical mapping.

    Use this to drive YAML mapping work. Events with high release_count
    appearing in your feed often are the most valuable to map.
    """
    where_sql, params = _build_unmapped_where(country)

    # Total unmapped releases (respecting optional country filter)
    total_rel_q = await session.execute(
        text(f"""
            SELECT COUNT(*)
            FROM indicator_releases
            WHERE {where_sql}
        """).bindparams(**params)
    )
    total_unmapped = total_rel_q.scalar_one()

    # Total distinct unmapped event types (respecting optional country filter)
    distinct_q_sql = """
        SELECT COUNT(*) FROM (
            SELECT DISTINCT
                raw_payload->>'country' AS country,
                raw_payload->>'type' AS event_type,
                raw_payload->>'comparison' AS comparison
            FROM indicator_releases
            WHERE {where_sql}
        ) t
    """.format(where_sql=where_sql)
    total_distinct = (
        await session.execute(text(distinct_q_sql).bindparams(**params))
    ).scalar_one()

    groups_sql = f"""
        SELECT
            raw_payload->>'country' AS country,
            raw_payload->>'type' AS event_type,
            raw_payload->>'comparison' AS comparison,
            COUNT(*) AS release_count,
            MAX(released_at) AS latest_release
        FROM indicator_releases
        WHERE {where_sql}
        GROUP BY
            raw_payload->>'country',
            raw_payload->>'type',
            raw_payload->>'comparison'
        HAVING COUNT(*) >= :min_count
        ORDER BY COUNT(*) DESC
        LIMIT :limit
    """
    query_params: dict[str, object] = {
        **params,
        "min_count": min_count,
        "limit": limit,
    }

    groups_rows = await session.execute(text(groups_sql).bindparams(**query_params))

    groups: list[UnmappedEventGroup] = []
    for row in groups_rows:
        groups.append(UnmappedEventGroup(
            country=row.country or "??",
            event_type=row.event_type or "??",
            comparison=row.comparison,
            release_count=row.release_count,
            latest_release=row.latest_release,
        ))

    payload = UnmappedEventsPayload(
        total_distinct_types=total_distinct,
        total_unmapped_releases=total_unmapped,
        groups=groups,
    )
    return Envelope(data=payload, meta=_meta())


@router.post("/maintenance/indicator-series-refresh")
async def admin_refresh_indicator_series(
    payload: IndicatorSeriesRefreshRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Repair/report duplicate latest rows for one canonical indicator series.

    EODHD can return duplicate same-period rows where a null-actual row and an
    actual row are both marked latest. This action clears those null latest
    flags while preserving actual latest rows for the chart API.
    """
    country_code = payload.country_code.upper()
    canonical_name = payload.canonical_name.strip()

    indicator_q = await session.execute(
        select(Indicator).where(
            Indicator.country_code == country_code,
            Indicator.canonical_name == canonical_name,
        )
    )
    indicator = indicator_q.scalar_one_or_none()
    if indicator is None:
        return {
            "ok": False,
            "message": "Indicator not found.",
            "country_code": country_code,
            "canonical_name": canonical_name,
        }

    releases_q = await session.execute(
        select(IndicatorRelease)
        .where(IndicatorRelease.indicator_id == indicator.id)
        .order_by(
            IndicatorRelease.period_start_date.desc().nullslast(),
            desc(IndicatorRelease.released_at),
            desc(IndicatorRelease.retrieved_at),
            desc(IndicatorRelease.id),
        )
    )
    releases = list(releases_q.scalars().all())

    grouped: dict[tuple[object, ...], list[IndicatorRelease]] = {}
    for release in releases:
        grouped.setdefault(_series_group_key(release), []).append(release)

    selected_ids: set[int] = set()
    duplicate_groups = 0
    rows_updated = 0
    for group_releases in grouped.values():
        if len(group_releases) > 1:
            duplicate_groups += 1
        selected = _pick_series_latest(group_releases)
        selected_ids.add(selected.id)
        if payload.repair_latest_flags:
            has_actual_latest = any(
                release.is_latest and release.actual is not None
                for release in group_releases
            )
            if has_actual_latest:
                repair_rows = [
                    release for release in group_releases
                    if release.is_latest and release.actual is None
                ]
            else:
                repair_rows = [
                    release for release in group_releases
                    if release.is_latest != (release.id == selected.id)
                ]

            for release in repair_rows:
                should_be_latest = not has_actual_latest and release.id == selected.id
                if release.is_latest != should_be_latest:
                    release.is_latest = should_be_latest
                    rows_updated += 1

    if payload.repair_latest_flags and rows_updated:
        await session.commit()

    now = _now()
    chartable_prints = [
        release for release in releases
        if release.id in selected_ids
        and release.actual is not None
        and release.released_at <= now
    ]
    chartable_prints.sort(
        key=lambda release: (
            release.period_start_date or datetime.min.date(),
            release.released_at,
            release.id,
        )
    )

    latest_print = chartable_prints[-1] if chartable_prints else None
    return {
        "ok": True,
        "message": "Series refreshed." if payload.repair_latest_flags else "Series checked.",
        "indicator_id": indicator.id,
        "country_code": country_code,
        "canonical_name": canonical_name,
        "display_name": indicator.display_name,
        "stored_rows": len(releases),
        "period_groups": len(grouped),
        "duplicate_groups": duplicate_groups,
        "rows_updated": rows_updated,
        "chartable_prints": len(chartable_prints),
        "latest_period": latest_print.period if latest_print else None,
        "latest_actual": float(latest_print.actual) if latest_print else None,
    }


# ── Event reaction log ──────────────────────────────────────────────────────

class UpdateNoteRequest(BaseModel):
    forecast_value: Decimal | None = None
    actual_value: Decimal | None = None
    previous_value: Decimal | None = None
    manual_notes: str | None = None
    ai_interpretation: str | None = None


class SavePriceCellRequest(BaseModel):
    instrument: str = Field(min_length=1, max_length=10)
    horizon: str = Field(min_length=1, max_length=4)
    raw_price: Decimal


class CreateNoteRequest(BaseModel):
    indicator_release_id: int


def _note_to_dict(note) -> dict:
    return {
        "id": note.id,
        "indicator_release_id": note.indicator_release_id,
        "forecast_value": note.forecast_value,
        "actual_value": note.actual_value,
        "previous_value": note.previous_value,
        "manual_notes": note.manual_notes,
        "ai_interpretation": note.ai_interpretation,
        "ai_generated_at": note.ai_generated_at,
    }


@router.get("/event-log/candidates")
async def get_event_log_candidates(
    date: date = Query(...),
    country: str | None = Query(default=None, min_length=2, max_length=2),
    importance: int | None = Query(default=None, ge=1, le=3),
    session: AsyncSession = Depends(get_session),
) -> dict:
    candidates = await event_reaction_log.list_candidate_releases(
        session, release_date=date, country_code=country, importance=importance
    )
    return {"candidates": candidates}


@router.post("/event-log/notes")
async def create_event_log_note(
    payload: CreateNoteRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    note = await event_reaction_log.get_or_create_note(
        session, payload.indicator_release_id
    )
    await session.commit()
    return _note_to_dict(note)


@router.get("/event-log/notes/{note_id}")
async def get_event_log_note(
    note_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    detail = await event_reaction_log.get_event_detail(session, note_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Event note not found.")
    note = detail["note"]
    return {
        **_note_to_dict(note),
        "indicator_name": detail["indicator"].display_name if detail["indicator"] else None,
        "country_code": detail["country"].code if detail["country"] else None,
        "currency_code": detail["country"].currency_code if detail["country"] else None,
        "released_at": detail["release"].released_at if detail["release"] else None,
        "grid": detail["grid"],
        "instruments": detail["instruments"],
        "horizons": detail["horizons"],
    }


@router.patch("/event-log/notes/{note_id}")
async def update_event_log_note(
    note_id: int,
    payload: UpdateNoteRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        note = await event_reaction_log.update_note_values(
            session, note_id, payload.model_dump(exclude_unset=True)
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return _note_to_dict(note)


@router.post("/event-log/notes/{note_id}/ai")
async def generate_event_log_ai(
    note_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        note = await event_reaction_log.generate_ai_interpretation(session, note_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    if note.ai_interpretation is None:
        raise HTTPException(
            status_code=502, detail="AI interpretation generation failed."
        )
    return _note_to_dict(note)


@router.put("/event-log/notes/{note_id}/price")
async def save_event_log_price_cell(
    note_id: int,
    payload: SavePriceCellRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        rows = await event_reaction_log.upsert_price_cell(
            session, note_id, payload.instrument, payload.horizon, payload.raw_price
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return {
        "instrument": payload.instrument,
        "cells": [
            {
                "horizon": row.horizon,
                "raw_price": row.raw_price,
                "pip_change": row.pip_change,
                "pct_change": row.pct_change,
            }
            for row in rows
        ],
    }


@router.get("/event-log")
async def list_event_log(
    session: AsyncSession = Depends(get_session),
) -> dict:
    events = await event_reaction_log.list_events(session)
    return {"events": events}
