"""Core rate-probability calculation engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope
from app.services.meeting_calendar import get_next_meeting, get_upcoming_meetings, normalize_bank

CB_MEETINGS_PATH = Path("config/cb_meetings.yaml")
RATE_PROBABILITY_OVERRIDES_PATH = Path("config/rate_probability_overrides.yaml")
PROXIMITY_LOCK_DAYS = 5
PROXIMITY_WARNING = (
    "Meeting within 5 days — probabilities may reflect settlement noise rather than policy expectations"
)
LOW_LIQUIDITY_NOTE = (
    "Indicative only — OIS market for this currency has limited liquidity. "
    "Probabilities may be less reliable than G3 currencies."
)

DATA_STATE_LIVE = "live"
DATA_STATE_NO_CURVE = "no_curve"
DATA_STATE_NO_CALENDAR = "no_calendar"
DATA_STATE_UNAVAILABLE = "unavailable"


@dataclass
class MeetingImplied:
    meeting_date: date
    implied_rate: float
    delta_bps: float


@dataclass
class MeetingProbability:
    bank: str
    meeting_dt: datetime
    current_rate: float
    implied_rate: float
    cut_prob: float | None
    hold_prob: float | None
    hike_prob: float | None
    delta_bps: float | None
    num_moves: float | None
    cumulative_delta_bps: float
    outcome_distribution: dict[int, float] | None
    proximity_lock: bool = False
    proximity_warning: str = ""
    low_liquidity_curve: bool = False
    curve_confidence_note: str = ""
    data_state: str = DATA_STATE_LIVE
    data_state_message: str = ""


async def get_ois_implied_rate(
    bank: str,
    target_date: date,
    curve_date: date | None = None,
    db_session: AsyncSession | None = None,
) -> float:
    """Interpolate cached OIS/futures curve to a target date."""
    bank = normalize_bank(bank)
    if db_session is None:
        async with session_scope() as session:
            return await get_ois_implied_rate(bank, target_date, curve_date, session)

    loaded = await _load_curve(db_session, bank, curve_date)
    if loaded is None:
        raise ValueError(f"No OIS cache available for {bank}")

    resolved_curve_date, curve = loaded
    current_rate = _bank_config(bank)["current_rate"]
    return interpolate_ois_rate(
        curve_date=resolved_curve_date,
        curve=curve,
        target_date=target_date,
        current_rate=current_rate,
    )


async def compute_meeting_probabilities(
    bank: str,
    step_bps: float | None = None,
    curve_date: date | None = None,
    n_meetings: int = 12,
    db_session: AsyncSession | None = None,
) -> list[MeetingProbability]:
    """Compute per-meeting cut/hold/hike probabilities from cached curves."""
    bank = normalize_bank(bank)
    if db_session is None:
        async with session_scope() as session:
            return await compute_meeting_probabilities(
                bank,
                step_bps=step_bps,
                curve_date=curve_date,
                n_meetings=n_meetings,
                db_session=session,
            )

    config = _bank_config(bank)
    meetings = await get_upcoming_meetings(bank, n_meetings, db_session)
    if not meetings:
        return [
            _unavailable_probability(
                bank=bank,
                meeting_dt=datetime.now(UTC),
                current_rate=float(config["current_rate"]),
                data_state=DATA_STATE_NO_CALENDAR,
                message=f"No meeting calendar available for {bank}.",
                config=config,
            )
        ]

    today_rate = float(config["current_rate"])
    step = int(step_bps or config.get("step_bps") or 25)
    loaded = await _load_curve(db_session, bank, curve_date)
    if loaded is None:
        # No live OIS data in DB — fall back to override file if available.
        if curve_date is None:
            override_probabilities = _load_probability_overrides(bank, config, n_meetings)
            if override_probabilities:
                return override_probabilities
        return [
            _unavailable_probability(
                bank=bank,
                meeting_dt=_parse_dt(meeting["meeting_dt"]),
                current_rate=today_rate,
                data_state=DATA_STATE_NO_CURVE,
                message=f"No OIS/futures curve available for {bank}.",
                config=config,
            )
            for meeting in meetings
        ]

    resolved_curve_date, curve = loaded
    baseline_rate = today_rate + float(config["rate_basis_adj"])
    meeting_dates = [_parse_dt(meeting["meeting_dt"]) for meeting in meetings]
    implied_steps = compute_step_implied_rates(
        meeting_dates,
        curve,
        today_rate,
        step,
        float(config["rate_basis_adj"]),
        curve_date=resolved_curve_date,
    )
    probabilities: list[MeetingProbability] = []

    for meeting_dt, implied in zip(meeting_dates, implied_steps, strict=False):
        days_to_meeting = (meeting_dt.date() - date.today()).days
        locked_snapshot = None
        if days_to_meeting <= PROXIMITY_LOCK_DAYS:
            locked_snapshot = await _load_proximity_snapshot(db_session, bank, meeting_dt, step)

        delta_bps = locked_snapshot["delta_bps"] if locked_snapshot else implied.delta_bps
        implied_rate = locked_snapshot["implied_rate"] if locked_snapshot else implied.implied_rate
        distribution = compute_outcome_distribution(delta_bps, step)
        cut_prob, hold_prob, hike_prob = summarize_distribution(distribution)
        probabilities.append(
            MeetingProbability(
                bank=bank,
                meeting_dt=meeting_dt,
                current_rate=round(baseline_rate if not probabilities else probabilities[-1].implied_rate, 4),
                implied_rate=round(implied_rate, 4),
                cut_prob=cut_prob,
                hold_prob=hold_prob,
                hike_prob=hike_prob,
                delta_bps=round(delta_bps, 2),
                num_moves=round(delta_bps / step, 4) if step else 0.0,
                cumulative_delta_bps=round((implied_rate - baseline_rate) * 100.0, 2),
                outcome_distribution=distribution,
                proximity_lock=bool(locked_snapshot),
                proximity_warning=PROXIMITY_WARNING if days_to_meeting <= PROXIMITY_LOCK_DAYS else "",
                low_liquidity_curve=bool(config["low_liquidity_curve"]),
                curve_confidence_note=LOW_LIQUIDITY_NOTE if config["low_liquidity_curve"] else "",
                data_state=DATA_STATE_LIVE,
                data_state_message="",
            )
        )

    return probabilities


async def get_twelve_month_outlook(
    bank: str,
    db_session: AsyncSession | None = None,
) -> dict[str, Any]:
    """Return the total change priced over roughly twelve months."""
    bank = normalize_bank(bank)
    if db_session is None:
        async with session_scope() as session:
            return await get_twelve_month_outlook(bank, session)

    config = _bank_config(bank)
    step_bps = float(config.get("step_bps") or 25.0)
    probabilities = await compute_meeting_probabilities(
        bank,
        step_bps=step_bps,
        n_meetings=12,
        db_session=db_session,
    )
    live_probabilities = [item for item in probabilities if item.data_state == DATA_STATE_LIVE]
    if not live_probabilities:
        return {
            "total_bps": 0.0,
            "num_moves": 0.0,
            "direction": "hold",
            "description": "Data unavailable",
        }

    cutoff = date.today() + timedelta(days=365)
    selected = [item for item in live_probabilities if item.meeting_dt.date() <= cutoff]
    anchor = selected[-1] if selected else live_probabilities[-1]
    total_bps = anchor.cumulative_delta_bps
    num_moves = total_bps / step_bps if step_bps else 0.0
    direction = "hike" if total_bps > 3 else "cut" if total_bps < -3 else "hold"
    return {
        "total_bps": round(total_bps, 2),
        "num_moves": round(num_moves, 2),
        "direction": direction,
        "description": _moves_description(num_moves),
    }


async def get_next_meeting_summary(
    bank: str,
    db_session: AsyncSession | None = None,
) -> dict[str, Any]:
    """Return the most actionable single-meeting summary."""
    bank = normalize_bank(bank)
    if db_session is None:
        async with session_scope() as session:
            return await get_next_meeting_summary(bank, session)

    probabilities = await compute_meeting_probabilities(bank, n_meetings=1, db_session=db_session)
    if not probabilities or probabilities[0].data_state != DATA_STATE_LIVE:
        raise ValueError(f"No rate probability data available for {bank}")

    next_meeting = await get_next_meeting(bank, db_session)
    probability = probabilities[0]
    outcomes = {
        "CUT": probability.cut_prob,
        "HOLD": probability.hold_prob,
        "HIKE": probability.hike_prob,
    }
    dominant_outcome, dominant_prob = max(outcomes.items(), key=lambda item: item[1])
    return {
        "meeting_dt": probability.meeting_dt,
        "seconds_until": int(next_meeting["seconds_until"]),
        "dominant_outcome": dominant_outcome,
        "dominant_prob_pct": round(dominant_prob * 100.0, 1),
        "implied_delta_bps": probability.delta_bps,
        "current_rate": probability.current_rate,
        "last_ois_rate": probability.implied_rate,
    }


async def save_snapshot(bank: str, db_session: AsyncSession) -> None:
    """Compute probabilities for today and upsert into rate_snapshots."""
    bank = normalize_bank(bank)
    snapshot_date = date.today()
    probabilities = await compute_meeting_probabilities(bank, db_session=db_session)
    for probability in probabilities:
        if probability.data_state != DATA_STATE_LIVE:
            continue
        await db_session.execute(
            text(
                """
                INSERT INTO rate_snapshots (
                    bank, snapshot_date, meeting_dt, implied_rate,
                    cut_prob, hold_prob, hike_prob, delta_bps
                )
                VALUES (
                    :bank, :snapshot_date, :meeting_dt, :implied_rate,
                    :cut_prob, :hold_prob, :hike_prob, :delta_bps
                )
                ON CONFLICT (bank, snapshot_date, meeting_dt)
                DO UPDATE SET implied_rate = EXCLUDED.implied_rate,
                              cut_prob = EXCLUDED.cut_prob,
                              hold_prob = EXCLUDED.hold_prob,
                              hike_prob = EXCLUDED.hike_prob,
                              delta_bps = EXCLUDED.delta_bps,
                              fetched_at = now()
                """
            ),
            {
                "bank": probability.bank,
                "snapshot_date": snapshot_date,
                "meeting_dt": probability.meeting_dt,
                "implied_rate": probability.implied_rate,
                "cut_prob": probability.cut_prob,
                "hold_prob": probability.hold_prob,
                "hike_prob": probability.hike_prob,
                "delta_bps": probability.delta_bps,
            },
        )


def interpolate_ois_rate(
    *,
    curve_date: date,
    curve: dict[int, float],
    target_date: date,
    current_rate: float,
) -> float:
    """Pure interpolation helper used by DB-backed APIs and tests."""
    if not curve:
        raise ValueError("curve cannot be empty")
    target_tenor = (target_date - curve_date).days
    points = sorted((int(tenor), float(rate)) for tenor, rate in curve.items())
    if target_tenor <= 0:
        return float(current_rate)
    if target_tenor < points[0][0]:
        left_days, left_rate = 0, float(current_rate)
        right_days, right_rate = points[0]
        weight = target_tenor / right_days
        return left_rate + ((right_rate - left_rate) * weight)
    if target_tenor >= points[-1][0]:
        return points[-1][1]
    for (left_days, left_rate), (right_days, right_rate) in zip(points, points[1:], strict=True):
        if left_days <= target_tenor <= right_days:
            weight = (target_tenor - left_days) / (right_days - left_days)
            return left_rate + ((right_rate - left_rate) * weight)
    return points[-1][1]


def compute_step_implied_rates(
    meeting_dates: list[datetime],
    ois_curve: dict[int, float],
    current_policy_rate: float,
    step_bps: int,
    rate_basis_adj: float,
    *,
    curve_date: date | None = None,
) -> list[MeetingImplied]:
    """Read the curve just after each meeting settles and compute meeting deltas."""
    anchor_date = curve_date or date.today()
    prior_implied = float(current_policy_rate) + float(rate_basis_adj)
    results: list[MeetingImplied] = []
    for meeting_dt in meeting_dates:
        meeting_date = meeting_dt.date()
        tenor_days_after = (meeting_date - anchor_date).days + 2
        raw_implied = interpolate_ois_rate_by_tenor(
            curve=ois_curve,
            target_tenor_days=tenor_days_after,
            current_rate=current_policy_rate,
        )
        # USD: SOFR OIS tracks effective fed funds rate ~8bp below target upper bound.
        # Adjust implied rate upward to align with target rate for probability display.
        implied_rate = raw_implied + float(rate_basis_adj)
        delta_bps = (implied_rate - prior_implied) * 100.0
        results.append(
            MeetingImplied(
                meeting_date=meeting_date,
                implied_rate=round(implied_rate, 6),
                delta_bps=round(delta_bps, 6),
            )
        )
        prior_implied = implied_rate
    return results


def interpolate_ois_rate_by_tenor(
    *,
    curve: dict[int, float],
    target_tenor_days: int,
    current_rate: float,
) -> float:
    """Interpolate a curve at an explicit tenor in days."""
    if not curve:
        raise ValueError("curve cannot be empty")
    points = sorted((int(tenor), float(rate)) for tenor, rate in curve.items())
    if target_tenor_days <= 0:
        return float(current_rate)
    if target_tenor_days < points[0][0]:
        left_days, left_rate = 0, float(current_rate)
        right_days, right_rate = points[0]
        weight = target_tenor_days / right_days
        return left_rate + ((right_rate - left_rate) * weight)
    if target_tenor_days >= points[-1][0]:
        return points[-1][1]
    for (left_days, left_rate), (right_days, right_rate) in zip(points, points[1:], strict=True):
        if left_days <= target_tenor_days <= right_days:
            weight = (target_tenor_days - left_days) / (right_days - left_days)
            return left_rate + ((right_rate - left_rate) * weight)
    return points[-1][1]


def compute_outcome_distribution(delta_bps: float, step_bps: int) -> dict[int, float]:
    """Map an implied bps move into adjacent discrete policy outcomes."""
    if step_bps <= 0:
        raise ValueError("step_bps must be positive")
    if delta_bps == 0:
        return {0: 1.0}

    direction = -1 if delta_bps < 0 else 1
    abs_delta = abs(float(delta_bps))
    full_steps = int(abs_delta // step_bps)
    partial = (abs_delta % step_bps) / step_bps
    lower_outcome = direction * full_steps * step_bps
    upper_outcome = direction * (full_steps + 1) * step_bps
    distribution = {
        int(lower_outcome): round(1.0 - partial, 4),
    }
    if partial > 0:
        distribution[int(upper_outcome)] = round(partial, 4)
    distribution.setdefault(0, 0.0)
    return dict(sorted(distribution.items()))


def summarize_distribution(distribution: dict[int, float]) -> tuple[float, float, float]:
    cut_prob = sum(prob for outcome, prob in distribution.items() if outcome < 0)
    hold_prob = distribution.get(0, 0.0)
    hike_prob = sum(prob for outcome, prob in distribution.items() if outcome > 0)
    return round(cut_prob, 4), round(hold_prob, 4), round(hike_prob, 4)


def probability_from_delta(delta_bps: float, step_bps: float) -> tuple[float, float, float]:
    """Return cut/hold/hike probabilities as fractions from a bps delta."""
    return summarize_distribution(compute_outcome_distribution(delta_bps, int(step_bps)))


def market_baseline_rate(bank: str, configured_rate: float, curve: dict[int, float]) -> float:
    """Return the market front-rate baseline when the curve supplies one."""
    if bank in {"FED", "RBA"} and curve:
        shortest_tenor, front_rate = min((int(tenor), float(rate)) for tenor, rate in curve.items())
        if (bank == "FED" and shortest_tenor <= 7) or (bank == "RBA" and shortest_tenor <= 35):
            return front_rate
    return float(configured_rate)


def _unavailable_probability(
    *,
    bank: str,
    meeting_dt: datetime,
    current_rate: float,
    data_state: str,
    message: str,
    config: dict[str, Any],
) -> MeetingProbability:
    return MeetingProbability(
        bank=bank,
        meeting_dt=meeting_dt,
        current_rate=round(current_rate, 4),
        implied_rate=round(current_rate, 4),
        cut_prob=None,
        hold_prob=None,
        hike_prob=None,
        delta_bps=None,
        num_moves=None,
        cumulative_delta_bps=0.0,
        outcome_distribution=None,
        proximity_lock=False,
        proximity_warning="",
        low_liquidity_curve=bool(config["low_liquidity_curve"]),
        curve_confidence_note=LOW_LIQUIDITY_NOTE if config["low_liquidity_curve"] else "",
        data_state=data_state,
        data_state_message=message,
    )


async def _load_proximity_snapshot(
    db_session: AsyncSession,
    bank: str,
    meeting_dt: datetime,
    step_bps: int,
) -> dict[str, float] | None:
    result = await db_session.execute(
        text(
            """
            SELECT implied_rate, delta_bps, snapshot_date
            FROM rate_snapshots
            WHERE bank = :bank
              AND meeting_dt = :meeting_dt
              AND snapshot_date < :cutoff_date
            ORDER BY snapshot_date DESC
            LIMIT 1
            """
        ),
        {
            "bank": bank,
            "meeting_dt": meeting_dt,
            "cutoff_date": meeting_dt.date() - timedelta(days=PROXIMITY_LOCK_DAYS),
        },
    )
    row = result.first()
    if row is None or row.delta_bps is None:
        return None
    return {
        "implied_rate": float(row.implied_rate),
        "delta_bps": float(row.delta_bps),
        "step_bps": float(step_bps),
    }


async def _load_curve(
    db_session: AsyncSession,
    bank: str,
    curve_date: date | None,
) -> tuple[date, dict[int, float]] | None:
    if curve_date is None:
        date_result = await db_session.execute(
            text("SELECT MAX(curve_date) FROM ois_cache WHERE bank = :bank"),
            {"bank": bank},
        )
        curve_date = date_result.scalar_one_or_none()
    if curve_date is None:
        return None

    result = await db_session.execute(
        text(
            """
            SELECT tenor_days, rate
            FROM ois_cache
            WHERE bank = :bank AND curve_date = :curve_date
            ORDER BY tenor_days
            """
        ),
        {"bank": bank, "curve_date": curve_date},
    )
    curve = {
        int(row.tenor_days): float(row.rate)
        for row in result.all()
        if row.tenor_days is not None and row.rate is not None
    }
    return (curve_date, curve) if curve else None


def _bank_config(bank: str) -> dict[str, Any]:
    with CB_MEETINGS_PATH.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    config = payload.get(bank, {})
    return {
        "current_rate": float(config.get("current_rate", 0.0)),
        "step_bps": int(config.get("step_bps", 25)),
        "rate_basis_adj": float(config.get("rate_basis_adj", 0.0)),
        "low_liquidity_curve": bool(config.get("low_liquidity_curve", False)),
    }


def _load_probability_overrides(
    bank: str,
    config: dict[str, Any],
    n_meetings: int,
) -> list[MeetingProbability]:
    if not RATE_PROBABILITY_OVERRIDES_PATH.exists():
        return []
    with RATE_PROBABILITY_OVERRIDES_PATH.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    rows = (payload.get(bank) or {}).get("current") or []
    baseline_rate = float(config["current_rate"]) + float(config.get("rate_basis_adj") or 0.0)
    probabilities: list[MeetingProbability] = []
    prior_implied = baseline_rate
    now = datetime.now(UTC)
    future_rows = [r for r in rows if _parse_dt(r["meeting_dt"]).astimezone(UTC) >= now]
    for row in future_rows[:n_meetings]:
        meeting_dt = _parse_dt(row["meeting_dt"])
        cumulative_delta_bps = float(row["cumulative_delta_bps"])
        delta_bps = float(row.get("delta_bps", cumulative_delta_bps - ((prior_implied - baseline_rate) * 100.0)))
        implied_rate = baseline_rate + (cumulative_delta_bps / 100.0)
        cut_prob = _clamp(float(row.get("cut_prob") or 0.0))
        hold_prob = _clamp(float(row.get("hold_prob") or 0.0))
        hike_prob = _clamp(float(row.get("hike_prob") or 0.0))
        probabilities.append(
            MeetingProbability(
                bank=bank,
                meeting_dt=meeting_dt,
                current_rate=round(prior_implied, 4),
                implied_rate=round(implied_rate, 4),
                cut_prob=round(cut_prob, 4),
                hold_prob=round(hold_prob, 4),
                hike_prob=round(hike_prob, 4),
                delta_bps=round(delta_bps, 2),
                num_moves=round(delta_bps / float(config["step_bps"]), 4),
                cumulative_delta_bps=round(cumulative_delta_bps, 2),
                outcome_distribution={
                    "CUT": round(cut_prob, 4),
                    "HOLD": round(hold_prob, 4),
                    "HIKE": round(hike_prob, 4),
                },
                low_liquidity_curve=bool(config["low_liquidity_curve"]),
                curve_confidence_note=LOW_LIQUIDITY_NOTE if config["low_liquidity_curve"] else "",
                data_state=DATA_STATE_LIVE,
                data_state_message="Terminal reference override",
            )
        )
        prior_implied = implied_rate
    return probabilities


def _parse_dt(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _moves_description(num_moves: float) -> str:
    abs_moves = abs(num_moves)
    if abs_moves < 0.25:
        return "Hold"
    rounded = round(abs_moves)
    direction = "Hike" if num_moves > 0 else "Cut"
    if abs(abs_moves - rounded) < 0.2 and rounded:
        suffix = "" if rounded == 1 else "s"
        return f"{rounded} {direction}{suffix}"
    low = int(abs_moves)
    high = low + 1
    suffix = "" if high == 1 else "s"
    return f"{low} or {high} {direction}{suffix}"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
