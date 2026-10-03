"""Country directory, detail and surprise data shared by APIs and desks."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    BiggestSurpriseItem, CountryDetailPayload, CountrySummary,
    IndicatorLatestRelease, IndicatorSnapshot,
)
from app.db.models import Country, Indicator, IndicatorRelease


def _now() -> datetime:
    return datetime.now(UTC)


def _release_to_schema(release: IndicatorRelease | None) -> IndicatorLatestRelease | None:
    if release is None:
        return None
    return IndicatorLatestRelease(
        release_id=release.id, period=release.period,
        period_start_date=release.period_start_date, released_at=release.released_at,
        actual=float(release.actual) if release.actual is not None else None,
        previous=float(release.previous) if release.previous is not None else None,
        estimate=float(release.estimate) if release.estimate is not None else None,
        change=float(release.change) if release.change is not None else None,
        change_percentage=(float(release.change_percentage)
                           if release.change_percentage is not None else None),
        surprise=float(release.surprise) if release.surprise is not None else None,
        is_latest=release.is_latest,
    )


def _latest_release_from_rows(
    releases: list[IndicatorRelease], *, actual_only: bool = False,
) -> IndicatorRelease | None:
    eligible = [release for release in releases if not actual_only or release.actual is not None]
    if not eligible:
        return None
    eligible.sort(key=lambda release: (
        release.period_start_date or date.min, release.released_at,
        release.retrieved_at, release.id,
    ))
    return eligible[-1]


async def get_latest_indicator_release(
    session: AsyncSession, indicator_id: int, *, actual_only: bool = False,
) -> IndicatorRelease | None:
    releases_q = await session.execute(
        select(IndicatorRelease)
        .where(IndicatorRelease.indicator_id == indicator_id)
        .order_by(
            IndicatorRelease.period_start_date.desc().nullslast(),
            desc(IndicatorRelease.released_at), desc(IndicatorRelease.retrieved_at),
            desc(IndicatorRelease.id),
        ).limit(24)
    )
    return _latest_release_from_rows(list(releases_q.scalars().all()), actual_only=actual_only)


async def get_country(session: AsyncSession, country_code: str) -> Country | None:
    country_q = await session.execute(select(Country).where(Country.code == country_code.upper()))
    return country_q.scalar_one_or_none()


async def _country_summary(session: AsyncSession, country: Country) -> CountrySummary:
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
        code=country.code, name=country.name, currency_code=country.currency_code,
        central_bank=country.central_bank, cb_mandate_type=country.cb_mandate_type,
        cb_inflation_target=(float(country.cb_inflation_target)
                             if country.cb_inflation_target is not None else None),
        timezone=country.timezone, indicator_count=indicator_count,
        latest_release_at=latest_release_at,
    )


async def list_country_summaries(session: AsyncSession) -> list[CountrySummary]:
    countries_q = await session.execute(select(Country).order_by(Country.code))
    return [await _country_summary(session, country) for country in countries_q.scalars().all()]


async def list_biggest_surprises(
    session: AsyncSession, *, days: int = 7, limit: int = 5,
) -> list[BiggestSurpriseItem]:
    cutoff = _now() - timedelta(days=days)
    surprises_q = await session.execute(
        select(Indicator, IndicatorRelease, Country)
        .join(IndicatorRelease, and_(
            IndicatorRelease.indicator_id == Indicator.id,
            IndicatorRelease.is_latest.is_(True),
        ))
        .join(Country, Country.code == Indicator.country_code)
        .where(
            IndicatorRelease.surprise.is_not(None),
            IndicatorRelease.released_at >= cutoff,
        )
        .order_by(func.abs(IndicatorRelease.surprise).desc(), desc(IndicatorRelease.released_at))
        .limit(limit)
    )
    items: list[BiggestSurpriseItem] = []
    for indicator, release, country in surprises_q.all():
        if release.surprise is None:
            continue
        items.append(BiggestSurpriseItem(
            country_code=country.code, country_name=country.name,
            currency_code=country.currency_code, indicator_id=indicator.id,
            canonical_name=indicator.canonical_name, display_name=indicator.display_name,
            surprise=float(release.surprise),
            actual=float(release.actual) if release.actual is not None else None,
            estimate=float(release.estimate) if release.estimate is not None else None,
            released_at=release.released_at,
        ))
    return items


async def get_country_detail_payload(
    session: AsyncSession, country_code: str,
) -> CountryDetailPayload | None:
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
            Indicator.importance.asc(), Indicator.primary_category.asc(),
            Indicator.display_name.asc(),
        )
    )
    indicators = []
    for indicator in indicators_q.scalars().all():
        release = await get_latest_indicator_release(session, indicator.id, actual_only=True)
        indicators.append(IndicatorSnapshot(
            id=indicator.id, canonical_name=indicator.canonical_name,
            display_name=indicator.display_name,
            primary_category=indicator.primary_category,
            secondary_categories=list(indicator.secondary_categories or []),
            comparison=indicator.comparison, frequency=indicator.frequency,
            unit=indicator.unit, importance=indicator.importance,
            is_higher_better_for_currency=indicator.is_higher_better_for_currency,
            latest_release=_release_to_schema(release),
        ))
    return CountryDetailPayload(
        country=await _country_summary(session, country),
        release_count=release_count, indicators=indicators,
    )
