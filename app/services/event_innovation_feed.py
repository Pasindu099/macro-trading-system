"""Panel feed for the Event-driven policy delta view.

Reads ``event_innovation_scores`` / ``release_bundles`` and shapes them for the
dashboard panel. Two things are deliberate here:

**Decay is computed live, at request time.** The database stores the innovation
at t=0 plus the bucket's half-life; how much of it is *left* depends on when you
ask, so persisting a decayed value would be stale the moment it was written. The
decay maths itself is imported from :mod:`app.processing.event_innovation` rather
than re-derived, so the panel and the scoring layer cannot drift.

**One row per bundle, not per member.** That is the whole point of the bundling
layer: NFP day is a single event with a single latent score, and showing its five
members as five independent shocks would undo it. Members are still available —
they populate the row's expansion, so the underlying numbers stay verifiable.

Positive = hawkish. ``surprise_normalized`` is already currency-oriented (a
lower-than-expected unemployment rate scores positive), and a currency-positive
surprise is the one that pushes the policy path tighter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.processing.event_innovation import decay_factor

# Default lookback for the panel. Two weeks covers a full monthly release cycle
# for the majors without the list growing past a scannable length.
DEFAULT_WINDOW_DAYS = 14
MAX_WINDOW_DAYS = 90

# Below this much remaining signal a row is styled as spent. It still shows —
# knowing a catalyst has decayed out is information — but it stops competing
# for attention with live ones.
SPENT_FILL_THRESHOLD = 0.15

# |score| that fills the bar to 100%. Scores are clipped at 6 sigma upstream,
# but a 3-sigma print is already an outsized event; scaling to 6 would leave
# ordinary releases as invisible slivers.
BAR_FULL_SCALE = 3.0

# Acronyms that must not be title-cased when a bundle_key becomes a label.
_ACRONYMS = frozenset({"NFP", "CPI", "PCE", "HICP", "PPI", "GDP", "PMI", "ISM"})


@dataclass(frozen=True)
class FeedFilters:
    """A resolved panel query."""

    days: int
    include_unscored: bool
    country_code: str | None


def resolve_feed_filters(
    days: int | None = None,
    *,
    include_unscored: bool = False,
    country_code: str | None = None,
) -> FeedFilters:
    """Clamp the query-string values into something safe to run."""
    window = DEFAULT_WINDOW_DAYS if days is None else days
    window = max(1, min(int(window), MAX_WINDOW_DAYS))
    return FeedFilters(
        days=window,
        include_unscored=include_unscored,
        country_code=country_code.upper() if country_code else None,
    )


# Bundle fields come from release_bundles; everything else is per-release. The
# LEFT JOIN keeps unbundled releases (most of them) in the same result set.
_FEED_SQL = text(
    """
    SELECT
        s.id                    AS score_id,
        s.release_id            AS release_id,
        s.bundle_id             AS bundle_id,
        s.indicator_id          AS indicator_id,
        s.release_date          AS release_date,
        s.actual                AS actual,
        s.consensus             AS consensus,
        s.surprise_raw          AS surprise_raw,
        s.surprise_normalized   AS surprise_normalized,
        s.decay_bucket          AS decay_bucket,
        s.half_life_days        AS half_life_days,
        s.scored                AS scored,
        i.display_name          AS display_name,
        i.canonical_name        AS canonical_name,
        i.unit                  AS unit,
        i.primary_category      AS category,
        i.country_code          AS country_code,
        c.currency_code         AS currency_code,
        c.name                  AS country_name,
        -- Period disambiguates same-day, same-indicator prints: the ONS
        -- publishes quarterly and monthly GDP (YoY) on one morning, and both
        -- map to gdp_yoy, so without this they render as identical rows.
        r.period                AS period,
        b.bundle_key            AS bundle_key,
        b.bundle_score          AS bundle_score,
        b.decay_bucket          AS bundle_bucket,
        b.half_life_days        AS bundle_half_life,
        b.member_count          AS bundle_member_count
    FROM event_innovation_scores s
    JOIN indicators i ON i.id = s.indicator_id
    JOIN countries  c ON c.code = i.country_code
    JOIN indicator_releases r ON r.id = s.release_id
    LEFT JOIN release_bundles b ON b.id = s.bundle_id
    WHERE s.release_date >= :date_from
      AND s.release_date <= :date_to
      AND (:include_unscored OR s.scored)
      -- CAST(...) rather than ':country_code::text'. SQLAlchemy's bind-param
      -- regex has a negative lookahead for ':', so a param followed directly by
      -- a '::' cast is left in the SQL as literal text and Postgres rejects it.
      AND (CAST(:country_code AS text) IS NULL OR i.country_code = :country_code)
    ORDER BY s.release_date DESC, s.indicator_id
    """
)


async def build_event_innovation_feed(
    session: AsyncSession,
    filters: FeedFilters,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return decayed, bundle-collapsed rows ready for the panel template."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    date_from = date.fromordinal(today.toordinal() - filters.days)

    result = await session.execute(
        _FEED_SQL,
        {
            "date_from": date_from,
            "date_to": today,
            "include_unscored": filters.include_unscored,
            "country_code": filters.country_code,
        },
    )
    records = [dict(row) for row in result.mappings().all()]

    rows = _assemble_rows(records, today)
    # Freshest first; within a day the loudest surviving signal leads.
    rows.sort(key=lambda row: (row["release_date"], abs(row["current"])), reverse=True)

    return {
        "rows": rows,
        "summary": _summarize(rows),
        "window_days": filters.days,
        "window_label": f"last {filters.days} day{'s' if filters.days != 1 else ''}",
        "include_unscored": filters.include_unscored,
        "country_code": filters.country_code,
        "generated_at": now,
    }


# ── row assembly ─────────────────────────────────────────────────────────────


def _assemble_rows(records: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Collapse bundled records into one row each; pass singles through."""
    bundles: dict[int, list[dict[str, Any]]] = {}
    singles: list[dict[str, Any]] = []

    for record in records:
        if record["bundle_id"] is not None:
            bundles.setdefault(record["bundle_id"], []).append(record)
        else:
            singles.append(record)

    rows = [_bundle_row(members, today) for members in bundles.values()]
    rows.extend(_single_row(record, today) for record in singles)
    # A member with no usable score leaves an empty row behind; drop those
    # rather than rendering a bar with nothing in it.
    return [row for row in rows if row is not None]


def _bundle_row(
    members: list[dict[str, Any]],
    today: date,
) -> dict[str, Any] | None:
    head = members[0]
    initial = _to_float(head["bundle_score"])
    if initial is None:
        return None

    half_life = _to_float(head["bundle_half_life"])
    row = _decay_fields(initial, head["release_date"], half_life, today)
    row.update(
        {
            "kind": "bundle",
            "row_id": f"bundle-{head['bundle_id']}",
            "name": _bundle_label(head["bundle_key"]),
            "detail": f"{len(members)} indicators",
            "currency_code": head["currency_code"],
            "country_code": head["country_code"],
            "country_name": head["country_name"],
            "release_date": head["release_date"],
            "decay_bucket": head["bundle_bucket"],
            "half_life_days": half_life,
            "members": [
                _member_detail(member)
                for member in sorted(
                    members,
                    key=lambda m: abs(_to_float(m["surprise_normalized"]) or 0.0),
                    reverse=True,
                )
            ],
        }
    )
    return row


def _single_row(record: dict[str, Any], today: date) -> dict[str, Any] | None:
    initial = _to_float(record["surprise_normalized"])
    if initial is None:
        return None

    half_life = _to_float(record["half_life_days"])
    row = _decay_fields(initial, record["release_date"], half_life, today)
    row.update(
        {
            "kind": "release",
            "row_id": f"release-{record['release_id']}",
            "name": record["display_name"],
            "detail": " · ".join(
                part for part in (record["category"], record["period"]) if part
            ),
            "currency_code": record["currency_code"],
            "country_code": record["country_code"],
            "country_name": record["country_name"],
            "release_date": record["release_date"],
            "decay_bucket": record["decay_bucket"],
            "half_life_days": half_life,
            "members": [_member_detail(record)],
        }
    )
    return row


def _decay_fields(
    initial: float,
    release_date: date,
    half_life: float | None,
    today: date,
) -> dict[str, Any]:
    """The live decay calculation — the reason this runs per request."""
    days_elapsed = max(0, (today - release_date).days)
    remaining = decay_factor(days_elapsed, half_life)
    current = initial * remaining

    return {
        "initial": initial,
        "current": current,
        "remaining": remaining,
        "days_elapsed": days_elapsed,
        "age_display": _relative_age(days_elapsed),
        # Bar geometry: the track is |initial| scaled against BAR_FULL_SCALE,
        # and the fill inside it is what has survived the decay. Reading the
        # two together gives "how big was it" and "how much is left" at once.
        "track_width": _bar_width(initial),
        "fill_pct": round(remaining * 100),
        "initial_display": _fmt_sigma(initial),
        "current_display": _fmt_sigma(current),
        "remaining_display": f"{round(remaining * 100)}%",
        "direction": _direction(initial),
        "direction_class": _direction_class(initial),
        "is_spent": remaining < SPENT_FILL_THRESHOLD,
        "does_not_decay": half_life is None,
        "half_life_display": (
            f"{half_life:g}d" if half_life is not None else "no decay"
        ),
    }


def _member_detail(record: dict[str, Any]) -> dict[str, Any]:
    """The verify-the-number payload behind an expanded row."""
    normalized = _to_float(record["surprise_normalized"])
    unit = record["unit"]
    return {
        "display_name": record["display_name"],
        "actual_display": _fmt(_to_float(record["actual"]), unit),
        "consensus_display": _fmt(_to_float(record["consensus"]), unit),
        "surprise_display": _fmt_signed(_to_float(record["surprise_raw"]), unit),
        "normalized_display": _fmt_sigma(normalized),
        "normalized_class": _direction_class(normalized),
        "decay_bucket": record["decay_bucket"] or "unbucketed",
        "decay_bucket_label": _bucket_label(record["decay_bucket"]),
        "half_life_display": (
            f"{_to_float(record['half_life_days']):g} days"
            if record["half_life_days"] is not None
            else "does not decay"
        ),
        "scored": bool(record["scored"]),
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Net surviving bias across the window."""
    live = [row for row in rows if not row["is_spent"]]
    net = sum(row["current"] for row in rows)
    hawkish = sum(1 for row in rows if row["initial"] > 0)
    dovish = sum(1 for row in rows if row["initial"] < 0)

    return {
        "total": len(rows),
        "live": len(live),
        "hawkish": hawkish,
        "dovish": dovish,
        "net": net,
        "net_display": _fmt_sigma(net),
        "net_class": _direction_class(net),
        "net_label": _net_label(net),
    }


# ── formatting ───────────────────────────────────────────────────────────────


def _bundle_label(bundle_key: str | None) -> str:
    """'US_NFP_DAY' -> 'NFP Day'.

    Derived rather than configured: the country is already shown in its own
    column, so repeating it in the label wastes the width, and adding a label
    field to bundle_config.yaml would be a second thing to keep in sync.
    """
    if not bundle_key:
        return "Bundle"
    parts = bundle_key.split("_")
    if len(parts) > 1:
        parts = parts[1:]  # drop the country prefix
    return " ".join(
        part if part in _ACRONYMS else part.capitalize() for part in parts
    )


def _bucket_label(bucket: str | None) -> str:
    if not bucket:
        return "Unbucketed"
    return bucket.replace("_", " ").capitalize()


def _relative_age(days: int) -> str:
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def _direction(value: float | None) -> str:
    if value is None or value == 0:
        return "neutral"
    return "hawkish" if value > 0 else "dovish"


def _direction_class(value: float | None) -> str:
    direction = _direction(value)
    return f"is-{direction}"


def _net_label(net: float) -> str:
    if net > 0.5:
        return "Net hawkish impulse"
    if net < -0.5:
        return "Net dovish impulse"
    return "Impulse broadly balanced"


def _bar_width(initial: float) -> int:
    """Track width as a percentage of the row's bar column.

    Floored at a visible sliver: an exactly-in-line print (actual == consensus,
    which happens often for rate-style indicators) would otherwise render a
    zero-width track that reads as a broken row rather than as "no surprise".
    """
    scaled = min(abs(initial) / BAR_FULL_SCALE, 1.0) * 100
    return max(2, int(round(scaled)))


def _fmt_sigma(value: float | None) -> str:
    return f"{value:+.2f}σ" if value is not None else "—"


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
