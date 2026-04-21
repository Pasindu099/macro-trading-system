"""Pydantic response models for the API.

Shared across all route modules. Keeping them here (not per-route-file)
makes it easy to see the whole API shape in one place.

Every response follows the standard envelope from spec §7.3:

    {
      "data": <endpoint-specific payload>,
      "meta": {"generated_at": "...", "cache_hint": "..."}
    }
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

# ══════════════════════════════════════════════════════════════════════
#  Envelope
# ══════════════════════════════════════════════════════════════════════

T = TypeVar("T")


class ResponseMeta(BaseModel):
    """Metadata attached to every response."""
    generated_at: datetime = Field(
        description="UTC timestamp when this response was generated",
    )
    cache_hint: str | None = Field(
        default=None,
        description="Optional info about staleness, e.g. 'last_ingestion: 2026-04-18T14:00:00Z'",
    )


class Envelope(BaseModel, Generic[T]):
    """Standard response wrapper: { data: T, meta: ResponseMeta }."""
    data: T
    meta: ResponseMeta


# ══════════════════════════════════════════════════════════════════════
#  Admin — Health
# ══════════════════════════════════════════════════════════════════════

class CountryHealth(BaseModel):
    """Health snapshot for one country."""
    country_code: str
    last_successful_run: datetime | None = None
    last_run_status: str | None = None
    indicator_count: int
    release_count: int


class AdminHealthPayload(BaseModel):
    """Payload for GET /api/admin/health."""
    database_ok: bool
    scheduler_enabled: bool
    ingestion_runs_last_24h: int
    failed_runs_last_24h: int
    unmapped_event_types_count: int
    countries: list[CountryHealth]


# ══════════════════════════════════════════════════════════════════════
#  Admin — Ingestion runs
# ══════════════════════════════════════════════════════════════════════

class IngestionRunSummary(BaseModel):
    """One row in the ingestion runs list."""
    id: int
    started_at: datetime
    finished_at: datetime | None
    run_type: str
    countries_fetched: list[str]
    events_inserted: int
    events_updated: int
    api_calls_used: int
    status: str
    has_errors: bool

    # We expose a flag rather than the full errors blob by default, to keep
    # the list compact. A dedicated endpoint can fetch full error details.


class IngestionRunsPayload(BaseModel):
    """Paginated list of ingestion runs."""
    total: int
    limit: int
    offset: int
    runs: list[IngestionRunSummary]


# ══════════════════════════════════════════════════════════════════════
#  Admin — Unmapped events
# ══════════════════════════════════════════════════════════════════════

class UnmappedEventGroup(BaseModel):
    """One distinct (country, type, comparison) with a count."""
    country: str
    event_type: str
    comparison: str | None
    release_count: int
    latest_release: datetime | None


class UnmappedEventsPayload(BaseModel):
    """Payload for GET /api/admin/unmapped-events."""
    total_distinct_types: int
    total_unmapped_releases: int
    groups: list[UnmappedEventGroup]


# ============================================================================
# Public API - Countries
# ============================================================================

class CountrySummary(BaseModel):
    """Landing-page summary for one tracked country."""
    code: str
    name: str
    currency_code: str
    central_bank: str
    cb_mandate_type: str
    cb_inflation_target: float | None = None
    timezone: str
    indicator_count: int
    latest_release_at: datetime | None = None


class CountriesPayload(BaseModel):
    """Payload for GET /api/countries."""
    countries: list[CountrySummary]


class BiggestSurpriseItem(BaseModel):
    """One release ranked in the weekly surprises strip."""
    country_code: str
    country_name: str
    currency_code: str
    indicator_id: int
    canonical_name: str
    display_name: str
    surprise: float
    actual: float | None = None
    estimate: float | None = None
    released_at: datetime


class BiggestSurprisesPayload(BaseModel):
    """Payload for GET /api/surprises."""
    days: int
    items: list[BiggestSurpriseItem]


class EconomicCalendarEvent(BaseModel):
    """One upcoming or recently released economic event."""
    release_id: int
    indicator_id: int | None = None
    country_code: str
    country_name: str
    currency_code: str
    canonical_name: str | None = None
    display_name: str
    primary_category: str
    importance: int
    period: str | None = None
    released_at: datetime
    status: str
    actual: float | None = None
    estimate: float | None = None
    previous: float | None = None
    surprise: float | None = None
    unit: str | None = None
    is_positive_when_higher: bool | None = None


class EconomicCalendarPayload(BaseModel):
    """Payload for GET /api/calendar."""
    days_back: int
    days_forward: int
    total_events: int
    events: list[EconomicCalendarEvent]


class IndicatorLatestRelease(BaseModel):
    """Most recent value for an indicator."""
    release_id: int
    period: str | None = None
    period_start_date: date | None = None
    released_at: datetime
    actual: float | None = None
    previous: float | None = None
    estimate: float | None = None
    change: float | None = None
    change_percentage: float | None = None
    surprise: float | None = None
    is_latest: bool


class IndicatorSnapshot(BaseModel):
    """Indicator metadata plus its latest release for country pages."""
    id: int
    canonical_name: str
    display_name: str
    primary_category: str
    secondary_categories: list[str]
    comparison: str | None = None
    frequency: str
    unit: str | None = None
    importance: int
    is_higher_better_for_currency: bool
    latest_release: IndicatorLatestRelease | None = None


class CountryDetailPayload(BaseModel):
    """Payload for GET /api/countries/{country_code}."""
    country: CountrySummary
    release_count: int
    indicators: list[IndicatorSnapshot]


# ============================================================================
# Public API - Indicator detail
# ============================================================================

class IndicatorMetadata(BaseModel):
    """Metadata for a canonical indicator."""
    id: int
    canonical_name: str
    display_name: str
    country_code: str
    primary_category: str
    secondary_categories: list[str]
    comparison: str | None = None
    frequency: str
    unit: str | None = None
    importance: int
    is_higher_better_for_currency: bool
    notes: str | None = None


class IndicatorDetailPayload(BaseModel):
    """Payload for GET /api/indicators/{indicator_id}."""
    indicator: IndicatorMetadata
    total_releases: int
    latest_release: IndicatorLatestRelease | None = None
    history: list[IndicatorLatestRelease]


class IndicatorSeriesPoint(BaseModel):
    """One chart-ready point for indicator detail views."""
    release_id: int
    period: str | None = None
    period_start_date: date | None = None
    released_at: datetime
    retrieved_at: datetime | None = None
    actual: float | None = None
    previous: float | None = None
    estimate: float | None = None
    surprise: float | None = None
    change: float | None = None
    change_percentage: float | None = None
    is_latest: bool


class IndicatorExplorerPayload(BaseModel):
    """Payload for GET /api/country/{code}/indicator/{canonical_name}."""
    indicator: IndicatorMetadata
    country: CountrySummary
    revision_mode: str
    range_key: str
    total_points: int
    cb_target: float | None = None
    series: list[IndicatorSeriesPoint]
    recent_prints: list[IndicatorSeriesPoint]
