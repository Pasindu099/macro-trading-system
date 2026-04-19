"""Admin API routes.

Endpoints for operational visibility — NOT for end-user consumption.
Mounted at /api/admin/* in main.py. No auth in Phase 1 (local-only).

Routes:
    GET /api/admin/health            — overall system status
    GET /api/admin/ingestion-runs    — paginated ingestion run history
    GET /api/admin/unmapped-events   — unmapped event types awaiting YAML mapping
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.db.models import Country, Indicator, IndicatorRelease, IngestionRun
from app.db.session import get_session
from app.settings import get_settings

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _meta(cache_hint: str | None = None) -> ResponseMeta:
    return ResponseMeta(generated_at=_now(), cache_hint=cache_hint)


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
    # Total unmapped releases
    total_rel_q = await session.execute(
        select(func.count(IndicatorRelease.id)).where(
            IndicatorRelease.indicator_id.is_(None)
        )
    )
    total_unmapped = total_rel_q.scalar_one()

    # Total distinct unmapped event types
    distinct_q_sql = """
        SELECT COUNT(*) FROM (
            SELECT DISTINCT
                raw_payload->>'country' AS country,
                raw_payload->>'type' AS event_type,
                raw_payload->>'comparison' AS comparison
            FROM indicator_releases
            WHERE indicator_id IS NULL
        ) t
    """
    total_distinct = (await session.execute(text(distinct_q_sql))).scalar_one()

    # Grouped query
    where_clauses = ["indicator_id IS NULL"]
    params: dict = {}
    if country:
        where_clauses.append("raw_payload->>'country' = :country")
        params["country"] = country

    where_sql = " AND ".join(where_clauses)

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
    params["min_count"] = min_count
    params["limit"] = limit

    groups_rows = await session.execute(text(groups_sql).bindparams(**params))

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