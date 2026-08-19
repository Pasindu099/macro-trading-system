"""Release ledger — deduplicated release history with consensus-normalized surprises.

This module exists to solve two problems that make `indicator_releases` hard to
read directly:

1. **Row inflation.** The table stores one row per *retrieval*, so a single print
   reappears every time it is revised or re-listed by EODHD. Australia's April
   2026 window holds 1,236 rows for 41 actual prints. Every read here collapses
   to one row per (indicator, period), keeping the newest retrieval.

2. **Incomparable surprises.** Raw ``actual - estimate`` cannot be compared across
   indicators: a 0.1pp CPI miss and a 50k payrolls miss are not the same event.
   Surprises are standardized against each indicator's own trailing surprise
   distribution, then sign-flipped by ``is_higher_better_for_currency`` so a
   positive score always means currency-positive, whether the indicator is
   unemployment (down is good) or GDP (up is good).

Indicator importance is 1 = highest, 3 = lowest.

.. deprecated::
    The scoring in this module is superseded by
    :mod:`app.processing.event_innovation`, which is the canonical surprise
    layer: it bundles co-released indicators, uses a point-in-time EWMA scale,
    and persists results to ``event_innovation_scores``. This module now borrows
    that module's scale maths so the two cannot drift, but it remains read-time
    and per-indicator — it does **not** bundle, decay, or persist. New consumers
    should read ``event_innovation_scores`` instead. The remaining job here is to
    repoint the country-page ledger at that table and delete the local scoring.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.processing.event_innovation import ewma_scale, winsorize

# Trailing window used to build each indicator's surprise distribution.
SURPRISE_HISTORY_YEARS = 3
# An indicator needs at least this many past surprises before a z-score is
# trustworthy. Below it we show the raw surprise and no score.
MIN_SURPRISE_SAMPLES = 6
# |z| below this lands close enough to consensus to call it in line.
INLINE_Z_THRESHOLD = 0.5
# Scores are clamped before display; a 12-sigma print is a data error, not a
# signal, and letting it through would wreck the colour scale and the average.
MAX_ABS_Z = 4.0
# Windows up to this long bucket weekly on the chart; longer ones bucket monthly.
WEEKLY_BUCKET_MAX_DAYS = 120
# Scale parameters, mirroring config/bundle_config.yaml defaults. Duplicated as
# constants rather than read from YAML because this is a per-request read path;
# if they drift apart, bundle_config.yaml is the authority.
EWMA_HALFLIFE_OBSERVATIONS = 24
WINSOR_LOWER_PERCENTILE = 5.0
WINSOR_UPPER_PERCENTILE = 95.0

IMPORTANCE_LABELS = {1: "High", 2: "Medium", 3: "Low"}

QUICK_RANGES = (
    ("30d", "30D"),
    ("3m", "3M"),
    ("6m", "6M"),
    ("ytd", "YTD"),
)

# One row per (indicator, period), newest retrieval wins. period_start_date is
# the canonical period identifier; the released_at date is only a fallback for
# the ~1% of rows that carry no period.
_LEDGER_SQL = text(
    """
    SELECT DISTINCT ON (
        r.indicator_id,
        COALESCE(r.period_start_date::text, r.released_at::date::text)
    )
        r.id                              AS release_id,
        r.indicator_id                    AS indicator_id,
        r.period                          AS period,
        r.period_start_date               AS period_start_date,
        r.released_at                     AS released_at,
        r.actual                          AS actual,
        r.estimate                        AS estimate,
        r.previous                        AS previous,
        i.canonical_name                  AS canonical_name,
        i.display_name                    AS display_name,
        i.primary_category                AS primary_category,
        i.unit                            AS unit,
        i.importance                      AS importance,
        i.is_higher_better_for_currency   AS higher_is_better,
        i.country_code                    AS country_code
    FROM indicator_releases r
    JOIN indicators i ON i.id = r.indicator_id
    WHERE i.country_code = :country_code
      AND r.actual IS NOT NULL
      AND r.released_at >= :history_from
      AND r.released_at <= :window_to
    ORDER BY
        r.indicator_id,
        COALESCE(r.period_start_date::text, r.released_at::date::text),
        r.retrieved_at DESC,
        r.id DESC
    """
)


@dataclass(frozen=True)
class LedgerFilters:
    """A resolved ledger query. Build with :func:`resolve_filters`."""

    country_code: str
    date_from: date
    date_to: date
    category: str | None          # None means every category
    max_importance: int | None    # None means every tier; else importance <= value
    window_label: str             # human-readable description of the window
    month: str | None             # "YYYY-MM" when a specific month was picked
    range_key: str | None         # quick-range key when one was used


def resolve_filters(
    country_code: str,
    *,
    month: str | None = None,
    range_key: str | None = None,
    category: str | None = None,
    importance: str | None = None,
    today: date | None = None,
) -> LedgerFilters:
    """Turn raw query-string values into a validated window.

    A month always wins over a quick range so that picking a month in the UI
    does not silently keep an earlier range selection active.
    """
    today = today or datetime.now(timezone.utc).date()

    resolved_month: str | None = None
    resolved_range: str | None = None

    parsed_month = _parse_month(month)
    if parsed_month is not None:
        start = parsed_month
        end = _end_of_month(parsed_month)
        resolved_month = start.strftime("%Y-%m")
        label = start.strftime("%B %Y")
    else:
        key = (range_key or "30d").lower()
        if key not in dict(QUICK_RANGES):
            key = "30d"
        resolved_range = key
        end = today
        if key == "30d":
            start = today - timedelta(days=30)
            label = "Last 30 days"
        elif key == "3m":
            start = today - timedelta(days=91)
            label = "Last 3 months"
        elif key == "6m":
            start = today - timedelta(days=182)
            label = "Last 6 months"
        else:  # ytd
            start = date(today.year, 1, 1)
            label = f"{today.year} to date"

    # Never show scheduled-but-unreleased events in a history ledger.
    if end > today:
        end = today

    return LedgerFilters(
        country_code=country_code.upper(),
        date_from=start,
        date_to=end,
        category=category if category and category.lower() != "all" else None,
        max_importance=_parse_importance(importance),
        window_label=label,
        month=resolved_month,
        range_key=resolved_range,
    )


async def build_release_ledger(
    session: AsyncSession,
    filters: LedgerFilters,
) -> dict[str, Any]:
    """Return deduplicated releases for the window, scored against consensus."""
    history_from = datetime(
        filters.date_from.year - SURPRISE_HISTORY_YEARS,
        filters.date_from.month,
        filters.date_from.day,
        tzinfo=timezone.utc,
    )
    window_to = datetime(
        filters.date_to.year, filters.date_to.month, filters.date_to.day,
        23, 59, 59, tzinfo=timezone.utc,
    )
    window_from = datetime(
        filters.date_from.year, filters.date_from.month, filters.date_from.day,
        tzinfo=timezone.utc,
    )

    result = await session.execute(
        _LEDGER_SQL,
        {
            "country_code": filters.country_code,
            "history_from": history_from,
            "window_to": window_to,
        },
    )
    records = [dict(row) for row in result.mappings().all()]

    stdevs = _surprise_stdevs(records)

    rows: list[dict[str, Any]] = []
    for record in records:
        released_at = record["released_at"]
        if released_at is None or released_at < window_from:
            continue
        if filters.category and record["primary_category"] != filters.category:
            continue
        if filters.max_importance is not None:
            importance = record["importance"] or 3
            if importance > filters.max_importance:
                continue
        rows.append(_build_row(record, stdevs))

    rows.sort(key=lambda row: row["released_at"], reverse=True)
    return {
        "rows": rows,
        "summary": _summarize(rows),
        "category_pulse": _category_pulse(rows),
        "timeseries": _surprise_timeseries(rows, filters),
        "window_label": filters.window_label,
    }


def surprise_chart_payload(
    ledger: dict[str, Any],
    *,
    country_code: str,
    country_name: str,
) -> dict[str, Any]:
    """Shape the ledger for the client-side charts and their exports.

    Kept beside the aggregation so the chart, the copied image and the copied
    table are all fed from one structure and cannot drift apart.
    """
    return {
        "country_code": country_code,
        "country_name": country_name,
        "window_label": ledger["window_label"],
        "timeseries": ledger["timeseries"],
        "category_pulse": ledger["category_pulse"],
        "summary": ledger["summary"],
    }


# ── internals ────────────────────────────────────────────────────────────────


def _build_row(record: dict[str, Any], stdevs: dict[int, float]) -> dict[str, Any]:
    actual = _to_float(record["actual"])
    estimate = _to_float(record["estimate"])
    previous = _to_float(record["previous"])
    unit = record["unit"]
    higher_is_better = bool(record["higher_is_better"])

    surprise = None
    if actual is not None and estimate is not None:
        surprise = actual - estimate

    stdev = stdevs.get(record["indicator_id"])
    score = None
    if surprise is not None and stdev:
        raw_z = surprise / stdev
        # Orient the score to the currency, not the number: a lower-than-expected
        # unemployment rate is a positive surprise for the currency.
        oriented = raw_z if higher_is_better else -raw_z
        score = max(-MAX_ABS_Z, min(MAX_ABS_Z, oriented))

    released_at = record["released_at"]
    country_code = (record["country_code"] or "").lower()
    return {
        "release_id": record["release_id"],
        "released_at": released_at,
        "date_display": released_at.strftime("%b %d") if released_at else "—",
        "time_display": released_at.strftime("%H:%M") if released_at else "",
        "display_name": record["display_name"],
        "canonical_name": record["canonical_name"],
        "category": record["primary_category"],
        "importance": record["importance"] or 3,
        "importance_label": IMPORTANCE_LABELS.get(record["importance"] or 3, "Low"),
        "period": record["period"] or _period_from_date(record["period_start_date"]),
        "actual_display": _fmt(actual, unit),
        "estimate_display": _fmt(estimate, unit) if estimate is not None else "—",
        "previous_display": _fmt(previous, unit) if previous is not None else "—",
        "has_estimate": estimate is not None,
        "surprise_display": _fmt_signed(surprise, unit),
        "score": score,
        "score_display": f"{score:+.1f}σ" if score is not None else "—",
        "score_class": _score_class(score),
        "score_width": _score_width(score),
        "change_class": _change_class(actual, previous, higher_is_better),
        "detail_href": (
            f"/country/{country_code}/indicator/{record['canonical_name']}"
        ),
    }


def _surprise_stdevs(records: list[dict[str, Any]]) -> dict[int, float]:
    """Surprise scale per indicator across the loaded history.

    Despite the name (kept so callers and tests don't churn), this is no longer
    a full-history stdev. It is the winsorized EWMA scale from
    :mod:`app.processing.event_innovation`, which is what the persisted
    ``event_innovation_scores`` layer uses — a full-history stdev on our
    2020-2026 window is dominated by COVID-era prints, so the two would have
    disagreed on every score.

    Still a whole-window estimate rather than the point-in-time one the scoring
    layer computes: this feeds a display ledger, not a backtest.
    """
    by_indicator: dict[int, list[tuple[Any, float]]] = {}
    for record in records:
        actual = _to_float(record["actual"])
        estimate = _to_float(record["estimate"])
        if actual is None or estimate is None:
            continue
        by_indicator.setdefault(record["indicator_id"], []).append(
            (record["released_at"], actual - estimate)
        )

    scales: dict[int, float] = {}
    for indicator_id, entries in by_indicator.items():
        if len(entries) < MIN_SURPRISE_SAMPLES:
            continue
        # ewma_scale weights by position, newest last, so order matters here.
        entries.sort(key=lambda pair: pair[0])
        surprises = [value for _, value in entries]
        scale = ewma_scale(
            winsorize(surprises, WINSOR_LOWER_PERCENTILE, WINSOR_UPPER_PERCENTILE),
            EWMA_HALFLIFE_OBSERVATIONS,
        )
        # None means the indicator never deviated from consensus in the sample.
        # Any deviation it does show is unscoreable, so leave it without a score.
        if scale:
            scales[indicator_id] = scale
    return scales


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [row["score"] for row in rows if row["score"] is not None]
    beats = sum(1 for score in scored if score >= INLINE_Z_THRESHOLD)
    misses = sum(1 for score in scored if score <= -INLINE_Z_THRESHOLD)
    inline = len(scored) - beats - misses
    average = statistics.fmean(scored) if scored else None
    return {
        "total": len(rows),
        "scored": len(scored),
        "unscored": len(rows) - len(scored),
        "beats": beats,
        "misses": misses,
        "inline": inline,
        "average_score": average,
        "average_display": f"{average:+.2f}σ" if average is not None else "—",
        "average_class": _score_class(average),
        "pulse_label": _pulse_label(average),
    }


def _category_pulse(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Average currency-oriented surprise per category, strongest bias first."""
    buckets: dict[str, list[float]] = {}
    for row in rows:
        if row["score"] is None:
            continue
        buckets.setdefault(row["category"] or "Other", []).append(row["score"])

    pulse = []
    for category, scores in buckets.items():
        average = statistics.fmean(scores)
        pulse.append({
            "category": category,
            "count": len(scores),
            "average": average,
            "average_display": f"{average:+.2f}σ",
            "score_class": _score_class(average),
            "score_width": _score_width(average),
        })
    pulse.sort(key=lambda item: abs(item["average"]), reverse=True)
    return pulse


def _surprise_timeseries(
    rows: list[dict[str, Any]],
    filters: LedgerFilters,
) -> list[dict[str, Any]]:
    """Average surprise per time bucket, oldest first.

    Buckets are weekly for short windows and monthly for long ones, so the
    diverging column chart never collapses into a hairline forest or a bar chart
    of three columns. Empty buckets are emitted so gaps in the release schedule
    stay visible instead of being silently closed up.
    """
    if not rows:
        return []

    span_days = (filters.date_to - filters.date_from).days
    weekly = span_days <= WEEKLY_BUCKET_MAX_DAYS

    buckets: dict[date, list[float]] = {}
    for row in rows:
        if row["score"] is None:
            continue
        released = row["released_at"].date()
        key = _week_start(released) if weekly else released.replace(day=1)
        buckets.setdefault(key, []).append(row["score"])

    if not buckets:
        return []

    series: list[dict[str, Any]] = []
    cursor = min(buckets)
    last = max(buckets)
    while cursor <= last:
        scores = buckets.get(cursor, [])
        average = statistics.fmean(scores) if scores else None
        series.append({
            "key": cursor.isoformat(),
            "label": cursor.strftime("%d %b") if weekly else cursor.strftime("%b %Y"),
            "average": round(average, 3) if average is not None else None,
            "count": len(scores),
            "score_class": _score_class(average),
        })
        cursor = cursor + timedelta(days=7) if weekly else _next_month(cursor)
    return series


def _week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _score_class(score: float | None) -> str:
    if score is None:
        return "is-neutral"
    if score >= INLINE_Z_THRESHOLD:
        return "is-positive"
    if score <= -INLINE_Z_THRESHOLD:
        return "is-negative"
    return "is-neutral"


def _score_width(score: float | None) -> int:
    """Half-width bar percentage, measured out from a centre line."""
    if score is None:
        return 0
    return int(round(min(abs(score), MAX_ABS_Z) / MAX_ABS_Z * 50))


def _change_class(
    actual: float | None,
    previous: float | None,
    higher_is_better: bool,
) -> str:
    """Direction versus the prior print, oriented to the currency.

    This is the fallback read for indicators with no consensus — NZ publishes an
    estimate for only 18% of its releases.
    """
    if actual is None or previous is None or actual == previous:
        return "is-neutral"
    rising = actual > previous
    return "is-positive" if rising == higher_is_better else "is-negative"


def _pulse_label(average: float | None) -> str:
    if average is None:
        return "No scored releases"
    if average >= INLINE_Z_THRESHOLD:
        return "Data running hot"
    if average <= -INLINE_Z_THRESHOLD:
        return "Data running cold"
    return "Data in line"


def _parse_month(month: str | None) -> date | None:
    if not month:
        return None
    try:
        parsed = datetime.strptime(month.strip(), "%Y-%m")
    except ValueError:
        return None
    return date(parsed.year, parsed.month, 1)


def _end_of_month(start: date) -> date:
    if start.month == 12:
        return date(start.year, 12, 31)
    return date(start.year, start.month + 1, 1) - timedelta(days=1)


def _parse_importance(importance: str | None) -> int | None:
    if not importance or importance.lower() == "all":
        return None
    try:
        value = int(importance)
    except ValueError:
        return None
    return value if value in (1, 2, 3) else None


def _period_from_date(period_start: date | None) -> str:
    return period_start.strftime("%b %Y") if period_start else "—"


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: float | None, unit: str | None) -> str:
    if value is None:
        return "—"
    formatted = f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{formatted} {unit}".strip() if unit else formatted


def _fmt_signed(value: float | None, unit: str | None) -> str:
    if value is None:
        return "—"
    formatted = f"{value:+,.2f}".rstrip("0").rstrip(".")
    if formatted in ("+", "-"):
        formatted = "0"
    return f"{formatted} {unit}".strip() if unit else formatted
