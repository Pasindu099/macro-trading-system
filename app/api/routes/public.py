"""Public API routes for the frontend-facing dashboard experience."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    BiggestSurpriseItem,
    BiggestSurprisesPayload,
    CountriesPayload,
    CountryDetailPayload,
    CountrySummary,
    EconomicCalendarEvent,
    EconomicCalendarPayload,
    Envelope,
    IndicatorDetailPayload,
    IndicatorExplorerPayload,
    IndicatorLatestRelease,
    IndicatorMetadata,
    IndicatorSeriesPoint,
    IndicatorSnapshot,
    ResponseMeta,
)
from app.db.models import Country, Indicator, IndicatorRelease, IngestionRun
from app.db.session import get_session

router = APIRouter(prefix="/api", tags=["public"])
RANGE_LOOKBACK_DAYS = {
    "1y": 365,
    "3y": 365 * 3,
    "5y": 365 * 5,
    "all": None,
}
CALENDAR_CATEGORIES = (
    "Inflation",
    "Growth",
    "Labor",
    "Monetary Policy",
    "Trade",
    "Sentiment",
    "Housing",
    "Other",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _meta(session: AsyncSession) -> ResponseMeta:
    latest_run_q = await session.execute(
        select(IngestionRun.finished_at)
        .where(
            IngestionRun.status.in_(["success", "partial"]),
            IngestionRun.finished_at.is_not(None),
        )
        .order_by(desc(IngestionRun.finished_at))
        .limit(1)
    )
    latest_finished_at = latest_run_q.scalar_one_or_none()

    cache_hint = None
    if latest_finished_at is not None:
        cache_hint = f"last_ingestion: {latest_finished_at.isoformat()}"
    return ResponseMeta(generated_at=_now(), cache_hint=cache_hint)


def _release_to_schema(
    release: IndicatorRelease | None,
) -> IndicatorLatestRelease | None:
    if release is None:
        return None

    return IndicatorLatestRelease(
        release_id=release.id,
        period=release.period,
        period_start_date=release.period_start_date,
        released_at=release.released_at,
        actual=float(release.actual) if release.actual is not None else None,
        previous=float(release.previous) if release.previous is not None else None,
        estimate=float(release.estimate) if release.estimate is not None else None,
        change=float(release.change) if release.change is not None else None,
        change_percentage=(
            float(release.change_percentage)
            if release.change_percentage is not None else None
        ),
        surprise=float(release.surprise) if release.surprise is not None else None,
        is_latest=release.is_latest,
    )


def _calendar_event_rank(event: EconomicCalendarEvent) -> tuple[int, int, int, int]:
    period_quality = 0
    if event.period:
        normalized_period = event.period.strip()
        if re.fullmatch(r"[A-Z][a-z]{2}", normalized_period):
            period_quality = 2
        elif re.fullmatch(r"Q[1-4](?:[ /-]\d{2,4})?", normalized_period):
            period_quality = 2
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized_period):
            period_quality = 2
        elif len(normalized_period) <= 8 and "(" not in normalized_period and ")" not in normalized_period:
            period_quality = 1

    return (
        1 if event.canonical_name else 0,
        sum(
            1 for value in (event.actual, event.estimate, event.previous, event.surprise)
            if value is not None
        ),
        period_quality,
        1 if event.status == "released" else 0,
        event.release_id,
    )


def _normalize_calendar_event_name(name: str) -> str:
    normalized = name.casefold().strip()
    for prefix in (
        "s&p global ",
        "westpac ",
        "markit ",
        "commbank ",
        "judo bank ",
    ):
        if normalized.startswith(prefix):
            normalized = normalized.removeprefix(prefix)
            break

    normalized = normalized.replace("(mom)", "(mom)")
    normalized = normalized.replace("(yoy)", "(yoy)")
    normalized = normalized.replace("(qoq)", "(qoq)")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _dedupe_calendar_events(
    events: list[EconomicCalendarEvent],
) -> list[EconomicCalendarEvent]:
    deduped: dict[tuple[object, ...], EconomicCalendarEvent] = {}

    for event in events:
        dedupe_name = _normalize_calendar_event_name(event.display_name)
        key = (
            event.country_code,
            dedupe_name,
            event.released_at,
            event.primary_category,
        )
        existing = deduped.get(key)
        if existing is None or _calendar_event_rank(event) > _calendar_event_rank(existing):
            deduped[key] = event

    return sorted(
        deduped.values(),
        key=lambda event: (event.released_at, event.importance, event.display_name),
    )


def _normalize_category_filter(category: str | None) -> str | None:
    if not category:
        return None

    normalized = category.strip().casefold()
    for allowed_category in CALENDAR_CATEGORIES:
        if allowed_category.casefold() == normalized:
            return allowed_category
    return category.strip().title()


def _latest_release_from_rows(
    releases: list[IndicatorRelease],
    *,
    actual_only: bool = False,
) -> IndicatorRelease | None:
    eligible = [
        release for release in releases
        if not actual_only or release.actual is not None
    ]
    if not eligible:
        return None

    eligible.sort(
        key=lambda release: (
            release.period_start_date or date.min,
            release.released_at,
            release.retrieved_at,
            release.id,
        )
    )
    return eligible[-1]


async def get_latest_indicator_release(
    session: AsyncSession,
    indicator_id: int,
    *,
    actual_only: bool = False,
) -> IndicatorRelease | None:
    releases_q = await session.execute(
        select(IndicatorRelease)
        .where(IndicatorRelease.indicator_id == indicator_id)
        .order_by(
            IndicatorRelease.period_start_date.desc().nullslast(),
            desc(IndicatorRelease.released_at),
            desc(IndicatorRelease.retrieved_at),
            desc(IndicatorRelease.id),
        )
        .limit(24)
    )
    return _latest_release_from_rows(
        list(releases_q.scalars().all()),
        actual_only=actual_only,
    )


async def _country_summary(
    session: AsyncSession,
    country: Country,
) -> CountrySummary:
    now = _now()
    indicator_count_q = await session.execute(
        select(func.count(Indicator.id)).where(Indicator.country_code == country.code)
    )
    indicator_count = indicator_count_q.scalar_one()

    latest_release_q = await session.execute(
        select(func.max(IndicatorRelease.released_at))
        .select_from(IndicatorRelease)
        .join(Indicator, Indicator.id == IndicatorRelease.indicator_id)
        .where(
            Indicator.country_code == country.code,
            IndicatorRelease.actual.is_not(None),
            IndicatorRelease.released_at <= now,
        )
    )
    latest_release_at = latest_release_q.scalar_one()

    return CountrySummary(
        code=country.code,
        name=country.name,
        currency_code=country.currency_code,
        central_bank=country.central_bank,
        cb_mandate_type=country.cb_mandate_type,
        cb_inflation_target=(
            float(country.cb_inflation_target)
            if country.cb_inflation_target is not None else None
        ),
        timezone=country.timezone,
        indicator_count=indicator_count,
        latest_release_at=latest_release_at,
    )


async def list_country_summaries(session: AsyncSession) -> list[CountrySummary]:
    """Shared helper for country-card summaries."""
    countries_q = await session.execute(select(Country).order_by(Country.code))
    countries = countries_q.scalars().all()
    return [await _country_summary(session, country) for country in countries]


async def list_biggest_surprises(
    session: AsyncSession,
    *,
    days: int = 7,
    limit: int = 5,
) -> list[BiggestSurpriseItem]:
    """Return the biggest recent mapped surprises for the landing strip."""
    cutoff = _now() - timedelta(days=days)
    surprises_q = await session.execute(
        select(Indicator, IndicatorRelease, Country)
        .join(
            IndicatorRelease,
            and_(
                IndicatorRelease.indicator_id == Indicator.id,
                IndicatorRelease.is_latest.is_(True),
            ),
        )
        .join(Country, Country.code == Indicator.country_code)
        .where(
            IndicatorRelease.surprise.is_not(None),
            IndicatorRelease.released_at >= cutoff,
        )
        .order_by(
            func.abs(IndicatorRelease.surprise).desc(),
            desc(IndicatorRelease.released_at),
        )
        .limit(limit)
    )

    items: list[BiggestSurpriseItem] = []
    for indicator, release, country in surprises_q.all():
        if release.surprise is None:
            continue
        items.append(BiggestSurpriseItem(
            country_code=country.code,
            country_name=country.name,
            currency_code=country.currency_code,
            indicator_id=indicator.id,
            canonical_name=indicator.canonical_name,
            display_name=indicator.display_name,
            surprise=float(release.surprise),
            actual=float(release.actual) if release.actual is not None else None,
            estimate=float(release.estimate) if release.estimate is not None else None,
            released_at=release.released_at,
        ))
    return items


async def list_calendar_events(
    session: AsyncSession,
    *,
    days_back: int = 1,
    days_forward: int = 7,
    country_code: str | None = None,
    category: str | None = None,
    importance: int | None = None,
    limit: int = 200,
) -> list[EconomicCalendarEvent]:
    """Return calendar-ready releases from the ingested EODHD event stream."""
    start_at = _now() - timedelta(days=days_back)
    end_at = _now() + timedelta(days=days_forward)
    normalized_category = _normalize_category_filter(category)

    mapped_filters = [
        IndicatorRelease.released_at >= start_at,
        IndicatorRelease.released_at <= end_at,
        IndicatorRelease.indicator_id.is_not(None),
    ]
    if country_code:
        mapped_filters.append(Indicator.country_code == country_code.upper())
    if normalized_category:
        mapped_filters.append(Indicator.primary_category == normalized_category)
    if importance is not None:
        mapped_filters.append(Indicator.importance == importance)

    calendar_q = await session.execute(
        select(IndicatorRelease, Indicator, Country)
        .join(Indicator, Indicator.id == IndicatorRelease.indicator_id)
        .join(Country, Country.code == Indicator.country_code)
        .where(*mapped_filters)
        .order_by(
            asc(IndicatorRelease.released_at),
            asc(Indicator.importance),
            asc(Indicator.display_name),
        )
        .limit(limit)
    )

    events: list[EconomicCalendarEvent] = []
    now = _now()
    for release, indicator, country in calendar_q.all():
        events.append(EconomicCalendarEvent(
            release_id=release.id,
            indicator_id=indicator.id,
            country_code=country.code,
            country_name=country.name,
            currency_code=country.currency_code,
            canonical_name=indicator.canonical_name,
            display_name=indicator.display_name,
            primary_category=indicator.primary_category,
            importance=indicator.importance,
            period=release.period,
            released_at=release.released_at,
            status=(
                "released"
                if release.actual is not None
                else "upcoming" if release.released_at >= now else "pending"
            ),
            actual=float(release.actual) if release.actual is not None else None,
            estimate=float(release.estimate) if release.estimate is not None else None,
            previous=float(release.previous) if release.previous is not None else None,
            surprise=float(release.surprise) if release.surprise is not None else None,
            unit=indicator.unit,
            is_positive_when_higher=indicator.is_higher_better_for_currency,
        ))

    country_rows = await session.execute(select(Country))
    countries = {country.code: country for country in country_rows.scalars().all()}

    unmapped_filters = [
        IndicatorRelease.indicator_id.is_(None),
        IndicatorRelease.released_at >= start_at,
        IndicatorRelease.released_at <= end_at,
    ]
    if country_code:
        unmapped_filters.append(
            IndicatorRelease.raw_payload["country"].astext == country_code.upper()
        )

    unmapped_q = await session.execute(
        select(IndicatorRelease)
        .where(*unmapped_filters)
        .order_by(asc(IndicatorRelease.released_at), asc(IndicatorRelease.id))
        .limit(limit)
    )

    for release in unmapped_q.scalars().all():
        raw_country = (release.raw_payload or {}).get("country")
        country = countries.get(raw_country)
        if country is None:
            continue

        raw_type = (release.raw_payload or {}).get("type") or "Unmapped event"
        raw_comparison = (release.raw_payload or {}).get("comparison")
        display_name = raw_type if raw_comparison in (None, "") else f"{raw_type} ({str(raw_comparison).upper()})"
        inferred_category = _infer_calendar_category(raw_type)
        inferred_importance = _infer_calendar_importance(raw_type)
        inferred_direction = _infer_calendar_positive_when_higher(raw_type)

        if normalized_category and inferred_category != normalized_category:
            continue
        if importance is not None and inferred_importance != importance:
            continue

        events.append(EconomicCalendarEvent(
            release_id=release.id,
            indicator_id=None,
            country_code=country.code,
            country_name=country.name,
            currency_code=country.currency_code,
            canonical_name=None,
            display_name=display_name,
            primary_category=inferred_category,
            importance=inferred_importance,
            period=release.period,
            released_at=release.released_at,
            status=(
                "released"
                if release.actual is not None
                else "upcoming" if release.released_at >= now else "pending"
            ),
            actual=float(release.actual) if release.actual is not None else None,
            estimate=float(release.estimate) if release.estimate is not None else None,
            previous=float(release.previous) if release.previous is not None else None,
            surprise=float(release.surprise) if release.surprise is not None else None,
            unit=None,
            is_positive_when_higher=inferred_direction,
        ))

    deduped_events = _dedupe_calendar_events(events)
    return deduped_events[:limit]


def _infer_calendar_category(raw_type: str) -> str:
    normalized = raw_type.lower()
    if any(token in normalized for token in ["interest rate", "central bank", "fed", "ecb", "boj", "rba", "rbnz", "boc", "snb"]):
        return "Monetary Policy"
    if any(token in normalized for token in ["sentiment", "confidence", "expectations", "survey", "zew"]):
        return "Sentiment"
    if any(token in normalized for token in ["house", "housing", "property", "rightmove"]):
        return "Housing"
    if any(token in normalized for token in ["cpi", "inflation", "ppi", "price"]):
        return "Inflation"
    if any(token in normalized for token in ["employment", "earnings", "payroll", "jobless", "claims", "unemployment"]):
        return "Labor"
    if any(token in normalized for token in ["trade", "import", "export", "balance"]):
        return "Trade"
    return "Growth"


def _infer_calendar_importance(raw_type: str) -> int:
    normalized = raw_type.lower()
    if any(token in normalized for token in ["inflation", "cpi", "payroll", "unemployment", "interest rate"]):
        return 1
    if any(token in normalized for token in ["trade", "export", "import", "house", "housing", "price index"]):
        return 2
    return 3


def _infer_calendar_positive_when_higher(raw_type: str) -> bool | None:
    normalized = raw_type.lower()
    if any(token in normalized for token in ["unemployment", "jobless", "claimant", "claims"]):
        return False
    if any(token in normalized for token in [
        "cpi", "inflation", "ppi", "price", "payroll", "earnings",
        "employment", "pmi", "confidence", "sales", "gdp", "trade",
        "export", "import", "credit", "production",
    ]):
        return True
    return None


async def get_country(
    session: AsyncSession,
    country_code: str,
) -> Country | None:
    """Return a single tracked country by code."""
    country_q = await session.execute(
        select(Country).where(Country.code == country_code.upper())
    )
    return country_q.scalar_one_or_none()


async def get_indicator_by_country_and_name(
    session: AsyncSession,
    country_code: str,
    canonical_name: str,
) -> Indicator | None:
    """Look up an indicator by country + canonical name."""
    indicator_q = await session.execute(
        select(Indicator).where(
            Indicator.country_code == country_code.upper(),
            Indicator.canonical_name == canonical_name,
        )
    )
    return indicator_q.scalar_one_or_none()


def _release_sort_key(release: IndicatorRelease) -> tuple[date | None, datetime, int]:
    return (
        release.period_start_date or date.min,
        release.released_at,
        release.id,
    )


def _pick_revision_row(
    releases: list[IndicatorRelease],
    revision_mode: str,
) -> IndicatorRelease:
    if revision_mode == "latest":
        latest = next((release for release in releases if release.is_latest), None)
        if latest is not None:
            return latest
        return sorted(releases, key=lambda release: release.id, reverse=True)[0]

    # "as_reported" means earliest retrieved version for the same period.
    return sorted(
        releases,
        key=lambda release: (
            release.retrieved_at or release.released_at,
            release.id,
        ),
    )[0]


def _release_to_series_point(release: IndicatorRelease) -> IndicatorSeriesPoint:
    return IndicatorSeriesPoint(
        release_id=release.id,
        period=release.period,
        period_start_date=release.period_start_date,
        released_at=release.released_at,
        retrieved_at=release.retrieved_at,
        actual=float(release.actual) if release.actual is not None else None,
        previous=float(release.previous) if release.previous is not None else None,
        estimate=float(release.estimate) if release.estimate is not None else None,
        surprise=float(release.surprise) if release.surprise is not None else None,
        change=float(release.change) if release.change is not None else None,
        change_percentage=(
            float(release.change_percentage)
            if release.change_percentage is not None else None
        ),
        is_latest=release.is_latest,
    )


async def get_indicator_explorer_payload(
    session: AsyncSession,
    country_code: str,
    canonical_name: str,
    *,
    revision_mode: str = "latest",
    range_key: str = "all",
) -> IndicatorExplorerPayload | None:
    """Build the full indicator-detail payload for the Step 6 page."""
    country = await get_country(session, country_code)
    if country is None:
        return None

    indicator = await get_indicator_by_country_and_name(
        session, country.code, canonical_name
    )
    if indicator is None:
        return None

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
    release_rows = releases_q.scalars().all()

    grouped: dict[tuple[object, ...], list[IndicatorRelease]] = {}
    for release in release_rows:
        key = (
            release.period,
            release.period_start_date,
            release.released_at if release.period is None and release.period_start_date is None else None,
        )
        grouped.setdefault(key, []).append(release)

    picked_releases = [
        _pick_revision_row(group_releases, revision_mode)
        for group_releases in grouped.values()
    ]
    picked_releases.sort(key=_release_sort_key)

    lookback_days = RANGE_LOOKBACK_DAYS[range_key]
    if lookback_days is not None:
        cutoff = _now() - timedelta(days=lookback_days)
        picked_releases = [
            release for release in picked_releases
            if release.released_at >= cutoff
        ]

    now = _now()
    actual_releases = [
        release for release in picked_releases
        if release.actual is not None and release.released_at <= now
    ]

    series = [_release_to_series_point(release) for release in actual_releases]
    recent_prints = list(reversed(series[-12:]))

    return IndicatorExplorerPayload(
        indicator=IndicatorMetadata(
            id=indicator.id,
            canonical_name=indicator.canonical_name,
            display_name=indicator.display_name,
            country_code=indicator.country_code,
            primary_category=indicator.primary_category,
            secondary_categories=list(indicator.secondary_categories or []),
            comparison=indicator.comparison,
            frequency=indicator.frequency,
            unit=indicator.unit,
            importance=indicator.importance,
            is_higher_better_for_currency=indicator.is_higher_better_for_currency,
            notes=indicator.notes,
        ),
        country=await _country_summary(session, country),
        revision_mode=revision_mode,
        range_key=range_key,
        total_points=len(series),
        cb_target=(
            float(country.cb_inflation_target)
            if country.cb_inflation_target is not None else None
        ),
        series=series,
        recent_prints=recent_prints,
    )


async def get_country_detail_payload(
    session: AsyncSession,
    country_code: str,
) -> CountryDetailPayload | None:
    """Shared helper for country detail data."""
    normalized_country_code = country_code.upper()
    country = await get_country(session, normalized_country_code)
    if country is None:
        return None

    release_count_q = await session.execute(
        select(func.count(IndicatorRelease.id))
        .select_from(IndicatorRelease)
        .join(Indicator, Indicator.id == IndicatorRelease.indicator_id)
        .where(Indicator.country_code == normalized_country_code)
    )
    release_count = release_count_q.scalar_one()

    indicators_q = await session.execute(
        select(Indicator)
        .where(Indicator.country_code == normalized_country_code)
        .order_by(
            Indicator.importance.asc(),
            Indicator.primary_category.asc(),
            Indicator.display_name.asc(),
        )
    )

    indicators = []
    for indicator in indicators_q.scalars().all():
        release = await get_latest_indicator_release(
            session,
            indicator.id,
            actual_only=True,
        )
        indicators.append(IndicatorSnapshot(
            id=indicator.id,
            canonical_name=indicator.canonical_name,
            display_name=indicator.display_name,
            primary_category=indicator.primary_category,
            secondary_categories=list(indicator.secondary_categories or []),
            comparison=indicator.comparison,
            frequency=indicator.frequency,
            unit=indicator.unit,
            importance=indicator.importance,
            is_higher_better_for_currency=indicator.is_higher_better_for_currency,
            latest_release=_release_to_schema(release),
        ))

    return CountryDetailPayload(
        country=await _country_summary(session, country),
        release_count=release_count,
        indicators=indicators,
    )


@router.get("/countries", response_model=Envelope[CountriesPayload])
async def list_countries(
    session: AsyncSession = Depends(get_session),
) -> Envelope[CountriesPayload]:
    """List all tracked countries with lightweight landing-page summaries."""
    payload = CountriesPayload(countries=await list_country_summaries(session))
    return Envelope(data=payload, meta=await _meta(session))


@router.get("/surprises", response_model=Envelope[BiggestSurprisesPayload])
async def get_biggest_surprises(
    days: int = Query(default=7, ge=1, le=30),
    limit: int = Query(default=5, ge=1, le=10),
    session: AsyncSession = Depends(get_session),
) -> Envelope[BiggestSurprisesPayload]:
    """Return the most significant recent surprises for landing-page use."""
    payload = BiggestSurprisesPayload(
        days=days,
        items=await list_biggest_surprises(session, days=days, limit=limit),
    )
    return Envelope(data=payload, meta=await _meta(session))


@router.get("/calendar", response_model=Envelope[EconomicCalendarPayload])
async def get_economic_calendar(
    days_back: int = Query(default=1, ge=0, le=14),
    days_forward: int = Query(default=7, ge=1, le=30),
    country: str | None = Query(default=None, min_length=2, max_length=2),
    category: str | None = Query(
        default=None,
        pattern=(
            "^(Inflation|Growth|Labor|Monetary Policy|Trade|"
            "Sentiment|Housing|Other)$"
        ),
    ),
    importance: int | None = Query(default=None, ge=1, le=3),
    limit: int = Query(default=200, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> Envelope[EconomicCalendarPayload]:
    """Return upcoming and recent calendar events from ingested EODHD data."""
    events = await list_calendar_events(
        session,
        days_back=days_back,
        days_forward=days_forward,
        country_code=country,
        category=category,
        importance=importance,
        limit=limit,
    )
    payload = EconomicCalendarPayload(
        days_back=days_back,
        days_forward=days_forward,
        total_events=len(events),
        events=events,
    )
    return Envelope(data=payload, meta=await _meta(session))


@router.get("/countries/{country_code}", response_model=Envelope[CountryDetailPayload])
async def get_country_detail(
    country_code: str,
    session: AsyncSession = Depends(get_session),
) -> Envelope[CountryDetailPayload]:
    """Return one country plus its indicators and current values."""
    payload = await get_country_detail_payload(session, country_code)
    if payload is None:
        raise HTTPException(status_code=404, detail="Country not found")
    return Envelope(data=payload, meta=await _meta(session))


@router.get("/indicators/{indicator_id}", response_model=Envelope[IndicatorDetailPayload])
async def get_indicator_detail(
    indicator_id: int,
    history_limit: int = Query(default=24, ge=1, le=240),
    session: AsyncSession = Depends(get_session),
) -> Envelope[IndicatorDetailPayload]:
    """Return indicator metadata plus the most recent release history."""
    indicator_q = await session.execute(
        select(Indicator).where(Indicator.id == indicator_id)
    )
    indicator = indicator_q.scalar_one_or_none()
    if indicator is None:
        raise HTTPException(status_code=404, detail="Indicator not found")

    total_releases_q = await session.execute(
        select(func.count(IndicatorRelease.id)).where(
            IndicatorRelease.indicator_id == indicator_id
        )
    )
    total_releases = total_releases_q.scalar_one()

    history_q = await session.execute(
        select(IndicatorRelease)
        .where(IndicatorRelease.indicator_id == indicator_id)
        .order_by(
            IndicatorRelease.period_start_date.desc().nullslast(),
            desc(IndicatorRelease.released_at),
            desc(IndicatorRelease.id),
        )
        .limit(history_limit)
    )
    history_rows = history_q.scalars().all()
    latest_release = next((row for row in history_rows if row.is_latest), None)
    if latest_release is None and history_rows:
        latest_release = history_rows[0]
    history = [
        release_schema
        for row in history_rows
        if (release_schema := _release_to_schema(row)) is not None
    ]

    payload = IndicatorDetailPayload(
        indicator=IndicatorMetadata(
            id=indicator.id,
            canonical_name=indicator.canonical_name,
            display_name=indicator.display_name,
            country_code=indicator.country_code,
            primary_category=indicator.primary_category,
            secondary_categories=list(indicator.secondary_categories or []),
            comparison=indicator.comparison,
            frequency=indicator.frequency,
            unit=indicator.unit,
            importance=indicator.importance,
            is_higher_better_for_currency=indicator.is_higher_better_for_currency,
            notes=indicator.notes,
        ),
        total_releases=total_releases,
        latest_release=_release_to_schema(latest_release),
        history=history,
    )
    return Envelope(data=payload, meta=await _meta(session))


@router.get(
    "/country/{country_code}/indicator/{canonical_name}",
    response_model=Envelope[IndicatorExplorerPayload],
)
async def get_indicator_explorer(
    country_code: str,
    canonical_name: str,
    revision_mode: str = Query(default="latest", pattern="^(latest|as_reported)$"),
    range_key: str = Query(default="all", pattern="^(1y|3y|5y|all)$"),
    session: AsyncSession = Depends(get_session),
) -> Envelope[IndicatorExplorerPayload]:
    """Return chart-ready indicator detail data for the Step 6 page."""
    payload = await get_indicator_explorer_payload(
        session,
        country_code,
        canonical_name,
        revision_mode=revision_mode,
        range_key=range_key,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="Indicator not found")
    return Envelope(data=payload, meta=await _meta(session))
