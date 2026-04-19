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

from datetime import datetime
from typing import Any, Generic, TypeVar

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