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
DATA_STATE_STALE_SOURCE = "stale_source"

# A static override older than this is never served (audit #8).
OVERRIDE_MAX_AGE = timedelta(days=14)
# ZQ: a meeting this close to month end leaves too few post-meeting days to
# de-average reliably, so the next month's contract gives the post-meeting rate.
ZQ_LATE_MONTH_DAYS = 7
# Banks whose cached curve is a strip of monthly-average futures (ZQ = 30-day fed funds).
MONTHLY_AVERAGE_FUTURES = {"FED"}


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _days_in_month(month: date) -> int:
    nxt = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return (nxt - month).days


def zq_monthly_averages(curve: dict[int, float], curve_date: date) -> dict[date, float]:
    """Map a cached ZQ strip back to {contract month: average EFFR}.

    The fetcher stores each contract at days-to-the-1st of its month; the current
    month's contract sits at tenor <= 1.
    """
    out: dict[date, float] = {}
    for tenor, rate in sorted(curve.items()):
        if tenor <= 0:
            month = _month_start(curve_date)
        else:
            month = _month_start(curve_date + timedelta(days=tenor))
            if tenor == 1 and month != _month_start(curve_date) and _month_start(curve_date) not in out:
                month = _month_start(curve_date)  # legacy current-month contract on the last day of a month
        out.setdefault(month, float(rate))
    return out


def deaverage_meeting_rates(
    month_averages: dict[date, float],
    meeting_dates: list[date],
    fallback_start_rate: float,
) -> dict[date, tuple[float, float]]:
    """{meeting_date: (pre_meeting_rate, post_meeting_rate)} from monthly-average futures.

    Rates are piecewise constant: a decision takes effect the day after the meeting.
    Months without a meeting pin the rate to their average; for a meeting month
        r_post = (avg × days_in_month − r_pre × days_before) / days_after,
    where days_before counts the meeting day itself. A meeting in the last
    ZQ_LATE_MONTH_DAYS days takes r_post from the next month's contract, and r_pre
    can be backed out of the meeting month when r_post is known.
    """
    months = sorted(month_averages)
    by_month = {_month_start(d): d for d in meeting_dates}
    start: dict[date, float] = {}
    end: dict[date, float] = {}
    for month in months:
        if month not in by_month:
            start[month] = end[month] = month_averages[month]

    def nxt(month: date) -> date:
        return month + timedelta(days=_days_in_month(month))

    def solve(*, allow_fallbacks: bool) -> bool:
        changed = False
        for month in months:
            if month in start and month in end:
                continue
            prev_month = _month_start(month - timedelta(days=1))
            next_month = nxt(month)
            if month not in start and prev_month in end:
                start[month] = end[prev_month]
                changed = True
            meeting = by_month.get(month)
            if meeting is None:
                continue
            total, before = _days_in_month(month), meeting.day
            after = total - before
            avg = month_averages[month]
            late = before > total - ZQ_LATE_MONTH_DAYS
            if month not in end:
                if late and next_month in start:
                    end[month] = start[next_month]
                    changed = True
                elif month in start and after > 0 and (not late or allow_fallbacks):
                    end[month] = (avg * total - start[month] * before) / after
                    changed = True
            if month not in start and month in end and before > 0:
                start[month] = (avg * total - end[month] * after) / before
                changed = True
            if month in end and next_month in month_averages and next_month not in start:
                start[next_month] = end[month]
                changed = True
        return changed

    for allow in (False, True):
        while solve(allow_fallbacks=allow):
            pass
        if allow is False:
            first = next((m for m in months if m in by_month and m not in start), None)
            if first is not None and _month_start(months[0]) == first:
                start[first] = fallback_start_rate  # current month has a meeting and nothing anchors it
    return {
        by_month[month]: (start[month], end[month])
        for month in months if month in by_month and month in start and month in end
    }


def step_path_rate(meeting_rates: list[tuple[date, float]], start_rate: float, target: date) -> float:
    """Piecewise-constant policy path: the post-meeting rate of the last meeting before `target`."""
    rate = start_rate
    for meeting_date, post in sorted(meeting_rates):
        if meeting_date < target:
            rate = post
    return rate


def override_is_fresh(bank_payload: dict[str, Any], now: datetime) -> bool:
    as_of = bank_payload.get("as_of_date")
    if not as_of:
        return False
    return now.date() - date.fromisoformat(str(as_of)) <= OVERRIDE_MAX_AGE


async def get_market_data_status(bank: str, session: AsyncSession) -> dict[str, Any]:
    """Latest OIS source and curve date for a bank, without page formatting."""
    result = await session.execute(text("""
        SELECT source, MAX(curve_date) AS curve_date, COUNT(*) AS rows
        FROM ois_cache
        WHERE bank = :bank
        GROUP BY source
        ORDER BY curve_date DESC, rows DESC
        LIMIT 1
    """), {"bank": bank.upper()})
    row = result.first()
    if row is None:
        return {"available": False, "source": None, "curve_date": None,
                "rows": 0, "is_proxy": False}
    source = str(row.source)
    return {
        "available": True, "source": source, "curve_date": row.curve_date,
        "rows": int(row.rows), "is_proxy": source.endswith("_proxy"),
    }


async def get_rate_probability_view(bank: str, session: AsyncSession) -> dict[str, Any]:
    """Combined raw rate-probability inputs for a new Central Banks view."""
    normalized = normalize_bank(bank)
    config = _bank_config(normalized)
    current_rate = float(config["current_rate"])
    step_bps = int(config.get("step_bps") or 25)
    try:
        summary = await get_next_meeting_summary(normalized, db_session=session)
    except ValueError:
        summary = {}
    try:
        outlook = await get_twelve_month_outlook(normalized, db_session=session)
    except Exception:
        outlook = {}
    meetings = await get_upcoming_meetings(normalized, n=12, db_session=session)
    try:
        probabilities = await compute_meeting_probabilities(
            normalized, step_bps=float(step_bps), n_meetings=12, db_session=session,
        ) if summary else []
    except Exception:
        probabilities = []
    try:
        market_data = await get_market_data_status(normalized, session)
    except Exception:
        market_data = {"available": False, "source": None, "curve_date": None,
                       "rows": 0, "is_proxy": False}
    next_meeting_at = summary.get("meeting_dt")
    if next_meeting_at is None and meetings:
        next_meeting_at = datetime.fromisoformat(str(meetings[0]["meeting_dt"]))
    combined_meetings = []
    for index, meta in enumerate(meetings):
        probability = probabilities[index] if index < len(probabilities) else None
        live = probability is not None and probability.data_state == DATA_STATE_LIVE
        outcomes = ({"HIKE": probability.hike_prob or 0.0,
                     "HOLD": probability.hold_prob or 0.0,
                     "CUT": probability.cut_prob or 0.0} if live else {})
        dominant = max(outcomes, key=outcomes.get) if outcomes else None
        combined_meetings.append({
            "meeting_at": probability.meeting_dt if probability else datetime.fromisoformat(str(meta["meeting_dt"])),
            "implied_rate": probability.implied_rate if probability else current_rate,
            "cut_prob": probability.cut_prob if probability else None,
            "hold_prob": probability.hold_prob if probability else None,
            "hike_prob": probability.hike_prob if probability else None,
            "dominant_outcome": dominant,
            "dominant_prob": outcomes[dominant] if dominant else None,
            "num_moves": probability.num_moves if probability else None,
            "cumulative_num_moves": round(probability.cumulative_delta_bps / step_bps, 4) if live else None,
            "delta_bps": probability.delta_bps if probability else None,
            "cumulative_delta_bps": probability.cumulative_delta_bps if live else None,
            "is_official": bool(meta.get("is_official", True)),
            "market_data_available": live,
            "data_state": probability.data_state if probability else DATA_STATE_NO_CURVE,
        })
    total_bps = float(outlook.get("total_bps") or 0.0)
    return {
        "bank": normalized, "current_rate": current_rate, "step_bps": step_bps,
        "deposit_rate": current_rate - 0.5 if normalized == "ECB" else current_rate,
        "main_rate": current_rate,
        "lending_rate": current_rate + 0.5 if normalized == "ECB" else current_rate,
        "next_meeting_at": next_meeting_at,
        "last_ois_rate": float(summary.get("last_ois_rate") or current_rate),
        "dominant_outcome": summary.get("dominant_outcome"),
        "dominant_prob_pct": float(summary.get("dominant_prob_pct") or 0.0),
        "implied_delta_bps": float(summary.get("implied_delta_bps") or 0.0),
        "outlook_total_bps": total_bps,
        "outlook_direction": "up" if total_bps > 3 else "down" if total_bps < -3 else "hold",
        "meetings": combined_meetings,
        "market_data": market_data,
    }


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
    """Market-implied policy rate on a target date: a step path, constant between meetings."""
    bank = normalize_bank(bank)
    if db_session is None:
        async with session_scope() as session:
            return await get_ois_implied_rate(bank, target_date, curve_date, session)

    loaded = await _load_curve(db_session, bank, curve_date)
    if loaded is None:
        raise ValueError(f"No OIS cache available for {bank}")

    config = _bank_config(bank)
    adj = float(config["rate_basis_adj"])
    probabilities = [
        p for p in await compute_meeting_probabilities(bank, curve_date=loaded[0], db_session=db_session)
        if p.data_state == DATA_STATE_LIVE
    ]
    if not probabilities:
        return float(config["current_rate"])
    start = probabilities[0].current_rate - adj
    return step_path_rate([(p.meeting_dt.date(), p.implied_rate - adj) for p in probabilities], start, target_date)


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
            override = _load_override_payload(bank)
            if override:
                # An override exists but is too old to serve: say so instead of showing it.
                return [
                    _unavailable_probability(
                        bank=bank,
                        meeting_dt=_parse_dt(meeting["meeting_dt"]),
                        current_rate=today_rate,
                        data_state=DATA_STATE_STALE_SOURCE,
                        message=f"Stale source: {bank} override dated {override.get('as_of_date')} is older than 14 days.",
                        config=config,
                    )
                    for meeting in meetings
                ]
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
    if bank in MONTHLY_AVERAGE_FUTURES:
        implied_steps = compute_deaveraged_implied_rates(
            meeting_dates, curve, today_rate, float(config["rate_basis_adj"]), curve_date=resolved_curve_date,
        )
        if implied_steps:
            # Cumulative moves are measured from the market's own pre-meeting rate, not the config rate.
            first = implied_steps[0]
            baseline_rate = first.implied_rate - first.delta_bps / 100.0
    else:
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


def compute_deaveraged_implied_rates(
    meeting_dates: list[datetime],
    curve: dict[int, float],
    current_policy_rate: float,
    rate_basis_adj: float,
    *,
    curve_date: date | None = None,
) -> list[MeetingImplied]:
    """Meeting implied rates from a monthly-average futures strip (ZQ), de-averaged.

    Stops at the first meeting the strip cannot cover, so later meetings are not
    paired with the wrong rates by the caller's zip().
    """
    anchor = curve_date or date.today()
    days = [dt.date() for dt in meeting_dates]
    path = deaverage_meeting_rates(zq_monthly_averages(curve, anchor), days, current_policy_rate)
    results: list[MeetingImplied] = []
    for day in days:
        if day not in path:
            break
        pre, post = path[day]
        results.append(MeetingImplied(
            meeting_date=day,
            implied_rate=round(post + float(rate_basis_adj), 6),
            delta_bps=round((post - pre) * 100.0, 6),
        ))
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


def _load_override_payload(bank: str) -> dict[str, Any]:
    if not RATE_PROBABILITY_OVERRIDES_PATH.exists():
        return {}
    with RATE_PROBABILITY_OVERRIDES_PATH.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return payload.get(bank) or {}


def _load_probability_overrides(
    bank: str,
    config: dict[str, Any],
    n_meetings: int,
    *,
    now: datetime | None = None,
) -> list[MeetingProbability]:
    """Static override rows, or [] when there are none or they are older than OVERRIDE_MAX_AGE."""
    now = now or datetime.now(UTC)
    bank_payload = _load_override_payload(bank)
    if not bank_payload or not override_is_fresh(bank_payload, now):
        return []
    rows = bank_payload.get("current") or []
    baseline_rate = float(config["current_rate"]) + float(config.get("rate_basis_adj") or 0.0)
    probabilities: list[MeetingProbability] = []
    prior_implied = baseline_rate
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
