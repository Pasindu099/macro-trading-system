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


@dataclass
class MeetingProbability:
    bank: str
    meeting_dt: datetime
    current_rate: float
    implied_rate: float
    cut_prob: float
    hold_prob: float
    hike_prob: float
    delta_bps: float
    num_moves: float
    cumulative_delta_bps: float


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
    step_bps: float = 25.0,
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
    today_rate = float(config["current_rate"])
    step = float(step_bps or config.get("step_bps") or 25.0)
    loaded = await _load_curve(db_session, bank, curve_date)
    if loaded is None:
        return []

    resolved_curve_date, curve = loaded
    baseline_rate = market_baseline_rate(bank, today_rate, curve)
    meetings = await get_upcoming_meetings(bank, n_meetings, db_session)
    probabilities: list[MeetingProbability] = []
    prior_implied = baseline_rate

    for meeting in meetings:
        meeting_dt = _parse_dt(meeting["meeting_dt"])
        implied_rate = interpolate_ois_rate(
            curve_date=resolved_curve_date,
            curve=curve,
            target_date=meeting_dt.date(),
            current_rate=today_rate,
        )
        delta_bps = (implied_rate - prior_implied) * 100.0
        cut_prob, hold_prob, hike_prob = probability_from_delta(delta_bps, step)
        probabilities.append(
            MeetingProbability(
                bank=bank,
                meeting_dt=meeting_dt,
                current_rate=round(prior_implied, 4),
                implied_rate=round(implied_rate, 4),
                cut_prob=cut_prob,
                hold_prob=hold_prob,
                hike_prob=hike_prob,
                delta_bps=round(delta_bps, 2),
                num_moves=round(delta_bps / step, 4) if step else 0.0,
                cumulative_delta_bps=round((implied_rate - baseline_rate) * 100.0, 2),
            )
        )
        prior_implied = implied_rate

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
    if not probabilities:
        return {
            "total_bps": 0.0,
            "num_moves": 0.0,
            "direction": "hold",
            "description": "Data unavailable",
        }

    cutoff = date.today() + timedelta(days=365)
    selected = [item for item in probabilities if item.meeting_dt.date() <= cutoff]
    anchor = selected[-1] if selected else probabilities[-1]
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
    if not probabilities:
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
    if target_tenor < points[0][0]:
        return float(current_rate)
    if target_tenor >= points[-1][0]:
        return points[-1][1]
    for (left_days, left_rate), (right_days, right_rate) in zip(points, points[1:], strict=True):
        if left_days <= target_tenor <= right_days:
            weight = (target_tenor - left_days) / (right_days - left_days)
            return left_rate + ((right_rate - left_rate) * weight)
    return points[-1][1]


def probability_from_delta(delta_bps: float, step_bps: float) -> tuple[float, float, float]:
    """Return cut/hold/hike probabilities as fractions from a bps delta."""
    if step_bps <= 0:
        raise ValueError("step_bps must be positive")
    cut_prob = _clamp(max(0.0, -delta_bps / step_bps))
    hike_prob = _clamp(max(0.0, delta_bps / step_bps))
    if cut_prob + hike_prob > 1.0:
        scale = cut_prob + hike_prob
        cut_prob /= scale
        hike_prob /= scale
    hold_prob = _clamp(1.0 - cut_prob - hike_prob)
    return round(cut_prob, 4), round(hold_prob, 4), round(hike_prob, 4)


def market_baseline_rate(bank: str, configured_rate: float, curve: dict[int, float]) -> float:
    """Return the market front-rate baseline when the curve supplies one."""
    if bank in {"FED", "RBA"} and curve:
        shortest_tenor, front_rate = min((int(tenor), float(rate)) for tenor, rate in curve.items())
        if (bank == "FED" and shortest_tenor <= 7) or (bank == "RBA" and shortest_tenor <= 35):
            return front_rate
    return float(configured_rate)


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


def _bank_config(bank: str) -> dict[str, float]:
    with CB_MEETINGS_PATH.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    config = payload.get(bank, {})
    return {
        "current_rate": float(config.get("current_rate", 0.0)),
        "step_bps": float(config.get("step_bps", 25.0)),
    }


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
