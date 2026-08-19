"""Event innovation scoring — the canonical surprise layer.

This replaces the ad-hoc full-history-stdev z-score that lived in
``app.services.release_ledger``. Three things changed, and all three matter:

1. **Bundling.** US NFP day releases payrolls, the prior-month revision, the
   unemployment rate, participation and average hourly earnings within the same
   second. Scoring those as five independent shocks counts one event five times.
   Co-released indicators are grouped into a bundle (membership from
   ``config/bundle_config.yaml``, never inferred from timestamps) and collapsed
   to one latent score.

2. **EWMA scale instead of full-history stdev.** Our window is 2020-2026, so a
   full-history stdev is dominated by the COVID prints: an April-2020 payrolls
   miss of 20 million sets a denominator that mutes every surprise since. The
   scale is an exponentially-weighted RMS of *winsorized* prior surprises, so
   old regimes fade out and single extreme prints cannot permanently inflate it.

3. **Decay.** A surprise is information with a shelf life, and the shelf life
   depends on the series. Heavily-revised high-frequency prints (payrolls, flash
   PMI) are stale in days; quarterly structural prints stay live for months;
   policy decisions do not decay at all, they become the next baseline.

Scales are computed **point-in-time**: each release is normalized against the
distribution of the surprises that preceded it, never its own or later ones.
That costs an O(n²) pass per indicator (~70 prints each — irrelevant) and buys
a scoring layer that is safe to backtest against.

The pure functions (``winsorize``, ``ewma_scale``, ``normalize_surprise``,
``decay_factor``, ``bundle_score``) have no database or config dependency and
are the unit-tested core. Everything below ``build_event_innovation`` is I/O.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "bundle_config.yaml"

# The four decay buckets. meeting_adjacent is the odd one out: it carries no
# half-life and is flagged for roll-up into the next policy baseline instead.
DECAY_BUCKETS = (
    "high_freq_high_revision",
    "high_freq_low_revision",
    "low_freq_structural",
    "meeting_adjacent",
)

MEETING_ADJACENT = "meeting_adjacent"

LN2 = math.log(2.0)


class EventInnovationConfigError(Exception):
    """Raised when bundle_config.yaml is structurally invalid."""


# ── configuration ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BundleMember:
    """One indicator inside a bundle, with its market-moving weight."""

    canonical_name: str
    weight: float


@dataclass(frozen=True)
class BundleSpec:
    """A named group of co-released indicators for one country."""

    bundle_key: str
    country: str
    members: tuple[BundleMember, ...]
    description: str | None = None

    def weight_for(self, canonical_name: str) -> float | None:
        for member in self.members:
            if member.canonical_name == canonical_name:
                return member.weight
        return None


@dataclass(frozen=True)
class ScoringConfig:
    """Everything read out of bundle_config.yaml, parsed and validated."""

    ewma_halflife_observations: float
    ewma_halflife_by_frequency: dict[str, float]
    winsor_lower_percentile: float
    winsor_upper_percentile: float
    min_observations: int
    max_abs_normalized: float
    min_bundle_members: int
    scored_importance_max: int
    half_lives: dict[str, float | None]
    # (country, canonical_name) -> bucket, plus (None, canonical_name) globals.
    bucket_by_indicator: dict[tuple[str | None, str], str]
    bundles: tuple[BundleSpec, ...]

    def half_life_days(self, bucket: str | None) -> float | None:
        """Half-life for a bucket. None means "does not decay"."""
        if bucket is None:
            return None
        return self.half_lives.get(bucket)

    def decay_bucket_for(self, country: str, canonical_name: str) -> str | None:
        """Country-scoped assignment wins over the global one."""
        scoped = self.bucket_by_indicator.get((country, canonical_name))
        if scoped is not None:
            return scoped
        return self.bucket_by_indicator.get((None, canonical_name))

    def halflife_observations_for(self, frequency: str | None) -> float:
        if frequency and frequency in self.ewma_halflife_by_frequency:
            return self.ewma_halflife_by_frequency[frequency]
        return self.ewma_halflife_observations

    def bundles_for_country(self, country: str) -> tuple[BundleSpec, ...]:
        return tuple(b for b in self.bundles if b.country == country)


def load_config(path: Path | None = None) -> ScoringConfig:
    """Parse bundle_config.yaml into a ScoringConfig."""
    path = path or DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"Bundle config not found: {path}")
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return parse_config(data)


def parse_config(data: dict[str, Any]) -> ScoringConfig:
    """Validate an already-loaded config mapping. Split out so tests can
    build a config in memory without touching the filesystem."""
    defaults = data.get("defaults") or {}

    half_lives_raw = data.get("decay_half_lives_days") or {}
    unknown = set(half_lives_raw) - set(DECAY_BUCKETS)
    if unknown:
        raise EventInnovationConfigError(
            f"Unknown decay bucket(s) in decay_half_lives_days: {sorted(unknown)}"
        )
    half_lives: dict[str, float | None] = {}
    for bucket in DECAY_BUCKETS:
        value = half_lives_raw.get(bucket)
        if value is None:
            half_lives[bucket] = None
        else:
            half_life = float(value)
            if half_life <= 0:
                raise EventInnovationConfigError(
                    f"Half-life for {bucket} must be positive, got {half_life}"
                )
            half_lives[bucket] = half_life
    # meeting_adjacent must not decay — it rolls up into the policy baseline.
    if half_lives.get(MEETING_ADJACENT) is not None:
        raise EventInnovationConfigError(
            "meeting_adjacent must have a null half-life; it rolls up into the "
            "next policy baseline rather than decaying."
        )

    bucket_by_indicator: dict[tuple[str | None, str], str] = {}
    for bucket, names in (data.get("decay_buckets") or {}).items():
        if bucket not in DECAY_BUCKETS:
            raise EventInnovationConfigError(f"Unknown decay bucket: {bucket}")
        for raw_name in names or []:
            key = _split_indicator_ref(raw_name)
            if key in bucket_by_indicator and bucket_by_indicator[key] != bucket:
                raise EventInnovationConfigError(
                    f"Indicator {raw_name!r} assigned to two decay buckets: "
                    f"{bucket_by_indicator[key]} and {bucket}"
                )
            bucket_by_indicator[key] = bucket

    bundles: list[BundleSpec] = []
    seen_keys: set[str] = set()
    for entry in data.get("bundles") or []:
        bundle_key = entry.get("bundle_key")
        country = entry.get("country")
        if not bundle_key or not country:
            raise EventInnovationConfigError(
                f"Bundle entry missing bundle_key or country: {entry!r}"
            )
        if bundle_key in seen_keys:
            raise EventInnovationConfigError(f"Duplicate bundle_key: {bundle_key}")
        seen_keys.add(bundle_key)

        members: list[BundleMember] = []
        member_names: set[str] = set()
        for member in entry.get("members") or []:
            name = member.get("indicator")
            if not name:
                raise EventInnovationConfigError(
                    f"Bundle {bundle_key} has a member with no indicator"
                )
            if name in member_names:
                raise EventInnovationConfigError(
                    f"Bundle {bundle_key} lists {name!r} twice"
                )
            weight = float(member.get("weight", 1.0))
            if weight <= 0:
                raise EventInnovationConfigError(
                    f"Bundle {bundle_key} member {name!r} has non-positive weight"
                )
            member_names.add(name)
            members.append(BundleMember(canonical_name=name, weight=weight))
        if not members:
            raise EventInnovationConfigError(f"Bundle {bundle_key} has no members")

        bundles.append(
            BundleSpec(
                bundle_key=bundle_key,
                country=country.upper(),
                members=tuple(members),
                description=entry.get("description"),
            )
        )

    # An indicator may only belong to one bundle per country, otherwise a single
    # release would have to carry two bundle_ids.
    claimed: dict[tuple[str, str], str] = {}
    for bundle in bundles:
        for member in bundle.members:
            key = (bundle.country, member.canonical_name)
            if key in claimed:
                raise EventInnovationConfigError(
                    f"{member.canonical_name!r} ({bundle.country}) is claimed by "
                    f"both {claimed[key]} and {bundle.bundle_key}"
                )
            claimed[key] = bundle.bundle_key

    lower = float(defaults.get("winsor_lower_percentile", 1.0))
    upper = float(defaults.get("winsor_upper_percentile", 99.0))
    if not 0.0 <= lower < upper <= 100.0:
        raise EventInnovationConfigError(
            f"Invalid winsor percentiles: lower={lower} upper={upper}"
        )

    return ScoringConfig(
        ewma_halflife_observations=float(
            defaults.get("ewma_halflife_observations", 24)
        ),
        ewma_halflife_by_frequency={
            str(k): float(v)
            for k, v in (data.get("ewma_halflife_by_frequency") or {}).items()
        },
        winsor_lower_percentile=lower,
        winsor_upper_percentile=upper,
        min_observations=int(defaults.get("min_observations", 8)),
        max_abs_normalized=float(defaults.get("max_abs_normalized", 6.0)),
        min_bundle_members=int(defaults.get("min_bundle_members", 2)),
        scored_importance_max=int(data.get("scored_importance_max", 2)),
        half_lives=half_lives,
        bucket_by_indicator=bucket_by_indicator,
        bundles=tuple(bundles),
    )


def _split_indicator_ref(raw: str) -> tuple[str | None, str]:
    """"US:nfp" -> ("US", "nfp"); "nfp" -> (None, "nfp")."""
    if ":" in raw:
        country, _, name = raw.partition(":")
        return country.strip().upper(), name.strip()
    return None, raw.strip()


# ── pure scoring core ────────────────────────────────────────────────────────


def percentile(values: Sequence[float], pct: float) -> float:
    """Linear-interpolation percentile, matching numpy's default method.

    Written by hand rather than pulled from numpy so this module stays
    importable in the test environment without the scientific stack.
    """
    if not values:
        raise ValueError("percentile() of an empty sequence")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    lower_index = math.floor(rank)
    upper_index = math.ceil(rank)
    if lower_index == upper_index:
        return ordered[int(rank)]
    weight = rank - lower_index
    return ordered[lower_index] * (1 - weight) + ordered[upper_index] * weight


def winsorize(
    values: Sequence[float],
    lower_percentile: float,
    upper_percentile: float,
) -> list[float]:
    """Clamp values into the [lower, upper] percentile band.

    Clamping, not trimming: the count is preserved so the EWMA weighting stays
    aligned with the original ordering. This runs before the scale estimate so a
    single COVID-era print can bend the denominator but not break it.
    """
    if not values:
        return []
    low = percentile(values, lower_percentile)
    high = percentile(values, upper_percentile)
    return [min(max(value, low), high) for value in values]


def ewma_scale(
    surprises: Sequence[float],
    halflife_observations: float,
) -> float | None:
    """Exponentially-weighted RMS of a surprise sample, newest observation last.

    Returns None when the sample is empty or degenerate (every surprise exactly
    zero), which is unscoreable rather than infinitely significant.

    Mean-square about **zero**, not about the sample mean: under a rational
    consensus the surprise is already zero-centred, and any persistent bias in
    the forecaster is itself information we do not want to normalize away.

    ``halflife_observations`` is in prints, not days — the weight on an
    observation h prints back is exactly one half.
    """
    if not surprises:
        return None
    if halflife_observations <= 0:
        raise ValueError("halflife_observations must be positive")

    decay = 0.5 ** (1.0 / halflife_observations)
    weighted_sum = 0.0
    weight_total = 0.0
    # age 0 is the most recent observation, so iterate from the end.
    for age, value in enumerate(reversed(surprises)):
        weight = decay**age
        weighted_sum += weight * value * value
        weight_total += weight

    if weight_total <= 0:
        return None
    scale = math.sqrt(weighted_sum / weight_total)
    return scale if scale > 0 else None


def surprise_scale(
    prior_surprises: Sequence[float],
    config: ScoringConfig,
    *,
    frequency: str | None = None,
) -> float | None:
    """Winsorize a prior-surprise sample, then take its EWMA scale.

    Returns None when there is not enough history to trust the estimate — the
    caller stores the raw surprise with scored = false in that case.
    """
    if len(prior_surprises) < config.min_observations:
        return None
    cleaned = winsorize(
        prior_surprises,
        config.winsor_lower_percentile,
        config.winsor_upper_percentile,
    )
    return ewma_scale(cleaned, config.halflife_observations_for(frequency))


def normalize_surprise(
    actual: float | None,
    consensus: float | None,
    scale: float | None,
    *,
    higher_is_better_for_currency: bool,
    max_abs: float = 6.0,
) -> float | None:
    """(actual - consensus) / scale, oriented to the currency and clipped.

    The sign flip is the point: a lower-than-expected unemployment rate is a
    *positive* innovation for the currency even though the arithmetic surprise
    is negative. Direction comes from ``indicators.is_higher_better_for_currency``,
    which already exists — there is no second direction flag to maintain.
    """
    if actual is None or consensus is None or not scale:
        return None
    raw = (actual - consensus) / scale
    oriented = raw if higher_is_better_for_currency else -raw
    return max(-max_abs, min(max_abs, oriented))


def decay_factor(days_elapsed: float, half_life_days: float | None) -> float:
    """Exponential decay: I(t) = I(0) * exp(-ln(2) * t / half_life).

    A None half-life means the event does not decay — meeting_adjacent bundles
    hold full weight until the policy-baseline roll-up consumes them. Negative
    elapsed time (a future-dated release) is clamped to full weight rather than
    amplified.
    """
    if half_life_days is None:
        return 1.0
    if half_life_days <= 0:
        raise ValueError("half_life_days must be positive or None")
    if days_elapsed <= 0:
        return 1.0
    return math.exp(-LN2 * days_elapsed / half_life_days)


def decayed_innovation(
    innovation: float | None,
    days_elapsed: float,
    half_life_days: float | None,
) -> float | None:
    """Apply :func:`decay_factor` to a normalized innovation."""
    if innovation is None:
        return None
    return innovation * decay_factor(days_elapsed, half_life_days)


def bundle_score(members: Iterable[tuple[float, float]]) -> float | None:
    """Collapse ``(normalized_surprise, weight)`` pairs into one latent score.

    A plain weighted average, renormalized over the members that actually
    printed — so an NFP day missing the participation rate does not silently
    scale the whole bundle down. Members with a None surprise must be filtered
    out by the caller before they get here.

    TODO(next): this is deliberately the simplest defensible aggregator. The
    upgrade is a factor model — fit the first principal component of the member
    surprise matrix (or regress realized FX reaction on members) and use the
    loadings as weights instead of the hand-set ones in bundle_config.yaml.
    Not worth doing until the scores have been validated against price data.
    """
    total_weight = 0.0
    weighted_sum = 0.0
    for value, weight in members:
        if weight <= 0:
            continue
        weighted_sum += value * weight
        total_weight += weight
    if total_weight <= 0:
        return None
    return weighted_sum / total_weight


def rolls_up_to_policy_baseline(decay_bucket: str | None) -> bool:
    """Whether a scored release feeds the policy baseline instead of decaying.

    TODO(next session): the actual roll-up needs the cb_tracking table, which is
    not built yet. Until then this is only a flag: consumers should treat these
    rows as non-decaying and leave them in place. Once cb_tracking exists, the
    roll-up should fold the flagged bundle's score into the bank's baseline at
    the next meeting and retire the row.
    """
    return decay_bucket == MEETING_ADJACENT


# ── release scoring ──────────────────────────────────────────────────────────


@dataclass
class ReleaseRecord:
    """One deduplicated print, as loaded from indicator_releases."""

    release_id: int
    indicator_id: int
    canonical_name: str
    country_code: str
    release_date: date
    actual: float | None
    consensus: float | None
    importance: int
    higher_is_better: bool
    frequency: str | None = None


@dataclass
class ScoredRelease:
    """One row destined for event_innovation_scores."""

    release_id: int
    indicator_id: int
    country_code: str
    release_date: date
    canonical_name: str
    actual: float | None
    consensus: float | None
    surprise_raw: float | None
    surprise_scale: float | None
    surprise_normalized: float | None
    decay_bucket: str | None
    half_life_days: float | None
    scored: bool
    bundle_key: str | None = None
    bundle_id: int | None = None


@dataclass
class BundleResult:
    """One row destined for release_bundles, plus its member release ids."""

    bundle_key: str
    country: str
    release_date: date
    indicator_ids: list[int] = field(default_factory=list)
    release_ids: list[int] = field(default_factory=list)
    score: float | None = None
    decay_bucket: str | None = None
    half_life_days: float | None = None
    member_count: int = 0


def score_releases(
    records: Sequence[ReleaseRecord],
    config: ScoringConfig,
) -> list[ScoredRelease]:
    """Score every record point-in-time against its own indicator's history.

    Records may arrive in any order; they are sorted per indicator by release
    date so each print only ever sees the surprises that preceded it.
    """
    by_indicator: dict[int, list[ReleaseRecord]] = defaultdict(list)
    for record in records:
        by_indicator[record.indicator_id].append(record)

    scored: list[ScoredRelease] = []
    for indicator_records in by_indicator.values():
        indicator_records.sort(key=lambda r: (r.release_date, r.release_id))
        prior: list[float] = []
        for record in indicator_records:
            scored.append(_score_one(record, prior, config))
            surprise = _raw_surprise(record)
            if surprise is not None:
                prior.append(surprise)

    scored.sort(key=lambda s: (s.release_date, s.indicator_id, s.release_id))
    return scored


def _raw_surprise(record: ReleaseRecord) -> float | None:
    if record.actual is None or record.consensus is None:
        return None
    return record.actual - record.consensus


def _score_one(
    record: ReleaseRecord,
    prior_surprises: Sequence[float],
    config: ScoringConfig,
) -> ScoredRelease:
    bucket = config.decay_bucket_for(record.country_code, record.canonical_name)
    half_life = config.half_life_days(bucket)
    surprise = _raw_surprise(record)

    scale = surprise_scale(prior_surprises, config, frequency=record.frequency)
    normalized = normalize_surprise(
        record.actual,
        record.consensus,
        scale,
        higher_is_better_for_currency=record.higher_is_better,
        max_abs=config.max_abs_normalized,
    )

    # Low-impact releases are stored, not scored: the row is kept so a later
    # change to scored_importance_max can be applied without re-ingesting.
    impact_ok = record.importance <= config.scored_importance_max
    scored = bool(impact_ok and bucket is not None and normalized is not None)

    return ScoredRelease(
        release_id=record.release_id,
        indicator_id=record.indicator_id,
        country_code=record.country_code,
        release_date=record.release_date,
        canonical_name=record.canonical_name,
        actual=record.actual,
        consensus=record.consensus,
        surprise_raw=surprise,
        surprise_scale=scale,
        surprise_normalized=normalized,
        decay_bucket=bucket,
        half_life_days=half_life,
        scored=scored,
    )


def build_bundles(
    scored: Sequence[ScoredRelease],
    config: ScoringConfig,
) -> list[BundleResult]:
    """Group scored releases into bundles and collapse each to one score.

    Only ``scored`` releases contribute to a bundle score, but the bundle is
    keyed on (bundle_key, country, release_date) so an unscored member released
    the same day still gets attached to the bundle by the caller.

    A bundle's decay bucket is taken from its highest-weighted scored member —
    NFP day decays like payrolls, not like the participation rate.
    """
    lookup: dict[tuple[str, str], BundleSpec] = {}
    for bundle in config.bundles:
        for member in bundle.members:
            lookup[(bundle.country, member.canonical_name)] = bundle

    groups: dict[tuple[str, str, date], list[tuple[ScoredRelease, float]]] = defaultdict(list)
    for row in scored:
        spec = lookup.get((row.country_code, row.canonical_name))
        if spec is None:
            continue
        weight = spec.weight_for(row.canonical_name)
        if weight is None:
            continue
        groups[(spec.bundle_key, spec.country, row.release_date)].append((row, weight))

    results: list[BundleResult] = []
    for (bundle_key, country, release_date), entries in sorted(groups.items()):
        contributing = [
            (row, weight)
            for row, weight in entries
            if row.scored and row.surprise_normalized is not None
        ]
        if len(contributing) < config.min_bundle_members:
            continue

        # Heaviest scored member sets the bundle's decay behaviour.
        anchor = max(contributing, key=lambda pair: pair[1])[0]
        bucket = anchor.decay_bucket

        result = BundleResult(
            bundle_key=bundle_key,
            country=country,
            release_date=release_date,
            # Every member released that day is recorded, scored or not, so the
            # bundle stays a faithful record of what actually printed.
            indicator_ids=sorted({row.indicator_id for row, _ in entries}),
            release_ids=sorted(row.release_id for row, _ in entries),
            score=bundle_score(
                (row.surprise_normalized, weight) for row, weight in contributing
            ),
            decay_bucket=bucket,
            half_life_days=config.half_life_days(bucket),
            member_count=len(contributing),
        )
        results.append(result)

        for row, _ in entries:
            row.bundle_key = bundle_key

    return results


# ── database I/O ─────────────────────────────────────────────────────────────

# One row per (indicator, period), newest retrieval wins. indicator_releases
# stores one row per *retrieval*, so a single print reappears on every revision;
# without this collapse the surprise history would be counted many times over.
_RELEASES_SQL = text(
    """
    SELECT DISTINCT ON (
        r.indicator_id,
        COALESCE(r.period_start_date::text, r.released_at::date::text)
    )
        r.id                              AS release_id,
        r.indicator_id                    AS indicator_id,
        r.released_at::date               AS release_date,
        r.actual                          AS actual,
        r.estimate                        AS consensus,
        i.canonical_name                  AS canonical_name,
        i.country_code                    AS country_code,
        i.importance                      AS importance,
        i.frequency                       AS frequency,
        i.is_higher_better_for_currency   AS higher_is_better
    FROM indicator_releases r
    JOIN indicators i ON i.id = r.indicator_id
    WHERE r.indicator_id IS NOT NULL
      AND r.actual IS NOT NULL
      AND (CAST(:country_code AS text) IS NULL OR i.country_code = :country_code)
      AND (CAST(:date_from AS date) IS NULL OR r.released_at::date >= :date_from)
    ORDER BY
        r.indicator_id,
        COALESCE(r.period_start_date::text, r.released_at::date::text),
        r.retrieved_at DESC,
        r.id DESC
    """
)


async def load_release_records(
    session: AsyncSession,
    *,
    country_code: str | None = None,
    date_from: date | None = None,
) -> list[ReleaseRecord]:
    """Load deduplicated releases ready for scoring."""
    result = await session.execute(
        _RELEASES_SQL,
        {"country_code": country_code, "date_from": date_from},
    )
    records: list[ReleaseRecord] = []
    for row in result.mappings():
        records.append(
            ReleaseRecord(
                release_id=row["release_id"],
                indicator_id=row["indicator_id"],
                canonical_name=row["canonical_name"],
                country_code=row["country_code"],
                release_date=row["release_date"],
                actual=_to_float(row["actual"]),
                consensus=_to_float(row["consensus"]),
                importance=row["importance"] or 3,
                higher_is_better=bool(row["higher_is_better"]),
                frequency=row["frequency"],
            )
        )
    return records


async def persist(
    session: AsyncSession,
    scored: Sequence[ScoredRelease],
    bundles: Sequence[BundleResult],
    *,
    truncate: bool = False,
) -> dict[str, int]:
    """Write bundles then scores, wiring each score to its bundle id.

    Both tables are upserted on their natural keys so a re-run is idempotent
    and can be pointed at a narrow date window without disturbing older rows.
    Pass ``truncate=True`` for a clean full rebuild.
    """
    if truncate:
        # release_bundles is referenced by event_innovation_scores, so the
        # cascade has to come from the parent.
        await session.execute(text("TRUNCATE TABLE release_bundles CASCADE"))

    bundle_ids: dict[tuple[str, date], int] = {}
    for bundle in bundles:
        result = await session.execute(
            text(
                """
                INSERT INTO release_bundles (
                    bundle_key, country, release_date, indicator_ids,
                    bundle_score, decay_bucket, half_life_days, member_count
                ) VALUES (
                    :bundle_key, :country, :release_date, :indicator_ids,
                    :bundle_score, :decay_bucket, :half_life_days, :member_count
                )
                ON CONFLICT (bundle_key, release_date) DO UPDATE SET
                    indicator_ids = EXCLUDED.indicator_ids,
                    bundle_score  = EXCLUDED.bundle_score,
                    decay_bucket  = EXCLUDED.decay_bucket,
                    half_life_days = EXCLUDED.half_life_days,
                    member_count  = EXCLUDED.member_count
                RETURNING id
                """
            ),
            {
                "bundle_key": bundle.bundle_key,
                "country": bundle.country,
                "release_date": bundle.release_date,
                "indicator_ids": bundle.indicator_ids,
                "bundle_score": bundle.score,
                "decay_bucket": bundle.decay_bucket,
                "half_life_days": bundle.half_life_days,
                "member_count": bundle.member_count,
            },
        )
        bundle_ids[(bundle.bundle_key, bundle.release_date)] = result.scalar_one()

    rows = []
    for row in scored:
        bundle_id = None
        if row.bundle_key is not None:
            bundle_id = bundle_ids.get((row.bundle_key, row.release_date))
        rows.append(
            {
                "release_id": row.release_id,
                "bundle_id": bundle_id,
                "indicator_id": row.indicator_id,
                "release_date": row.release_date,
                "actual": row.actual,
                "consensus": row.consensus,
                "surprise_raw": row.surprise_raw,
                "surprise_scale": row.surprise_scale,
                "surprise_normalized": row.surprise_normalized,
                "decay_bucket": row.decay_bucket,
                "half_life_days": row.half_life_days,
                "scored": row.scored,
            }
        )

    if rows:
        await session.execute(
            text(
                """
                INSERT INTO event_innovation_scores (
                    release_id, bundle_id, indicator_id, release_date,
                    actual, consensus, surprise_raw, surprise_scale,
                    surprise_normalized, decay_bucket, half_life_days, scored
                ) VALUES (
                    :release_id, :bundle_id, :indicator_id, :release_date,
                    :actual, :consensus, :surprise_raw, :surprise_scale,
                    :surprise_normalized, :decay_bucket, :half_life_days, :scored
                )
                ON CONFLICT (release_id) DO UPDATE SET
                    bundle_id           = EXCLUDED.bundle_id,
                    indicator_id        = EXCLUDED.indicator_id,
                    release_date        = EXCLUDED.release_date,
                    actual              = EXCLUDED.actual,
                    consensus           = EXCLUDED.consensus,
                    surprise_raw        = EXCLUDED.surprise_raw,
                    surprise_scale      = EXCLUDED.surprise_scale,
                    surprise_normalized = EXCLUDED.surprise_normalized,
                    decay_bucket        = EXCLUDED.decay_bucket,
                    half_life_days      = EXCLUDED.half_life_days,
                    scored              = EXCLUDED.scored
                """
            ),
            rows,
        )

    return {"bundles": len(bundles), "scores": len(rows)}


async def build_event_innovation(
    session: AsyncSession,
    *,
    config: ScoringConfig | None = None,
    country_code: str | None = None,
    date_from: date | None = None,
    truncate: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Load, score, bundle and persist. The one entry point worth calling."""
    config = config or load_config()

    records = await load_release_records(
        session, country_code=country_code, date_from=date_from
    )
    logger.info("Loaded %d deduplicated releases", len(records))

    scored = score_releases(records, config)
    bundles = build_bundles(scored, config)
    logger.info(
        "Scored %d of %d releases into %d bundles",
        sum(1 for row in scored if row.scored),
        len(scored),
        len(bundles),
    )

    written = {"bundles": 0, "scores": 0}
    if not dry_run:
        written = await persist(session, scored, bundles, truncate=truncate)

    return {
        "records_loaded": len(records),
        "scores_total": len(scored),
        "scores_scored": sum(1 for row in scored if row.scored),
        "scores_unscored": sum(1 for row in scored if not row.scored),
        "bundles": len(bundles),
        "meeting_adjacent_pending_rollup": sum(
            1 for row in scored if row.scored and rolls_up_to_policy_baseline(row.decay_bucket)
        ),
        "written": written,
        "dry_run": dry_run,
    }


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
