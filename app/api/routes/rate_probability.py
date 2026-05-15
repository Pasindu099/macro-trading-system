"""Rate probability JSON endpoints — /api/rate-prob/*."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.meeting_calendar import SUPPORTED_BANKS, get_upcoming_meetings, normalize_bank
from app.services.rate_probability import (
    DATA_STATE_NO_CURVE,
    DATA_STATE_LIVE,
    LOW_LIQUIDITY_NOTE,
    _bank_config,
    compute_meeting_probabilities,
    get_next_meeting_summary,
    get_twelve_month_outlook,
)

router = APIRouter(prefix="/api/rate-prob", tags=["rate-probability"])
SessionDep = Depends(get_session)

_RATE_LABELS: dict[str, str] = {
    "FED":  "Fed Funds Rate",
    "ECB":  "Deposit Facility Rate",
    "BOE":  "Bank Rate",
    "BOJ":  "Policy Rate",
    "RBA":  "Cash Rate",
    "BOC":  "Overnight Rate",
    "RBNZ": "OCR",
    "SNB":  "SNB Policy Rate",
}


def _dt_iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _snapshot_label(snap_date: date, today: date) -> str:
    days = (today - snap_date).days
    if days <= 0:
        return "Current"
    if days <= 4:
        return f"{days}d ago"
    if days <= 10:
        return "1w ago"
    if days <= 17:
        return "2w ago"
    if days <= 28:
        return "3w ago"
    if days <= 49:
        return "6w ago"
    if days <= 77:
        return "10w ago"
    return snap_date.isoformat()


# ══════════════════════════════════════════════════════════════════════
#  Endpoint 1 — summary/{bank}
# ══════════════════════════════════════════════════════════════════════

@router.get("/summary/{bank}")
async def rate_summary(
    bank: str,
    session: AsyncSession = SessionDep,
) -> dict:
    """Four summary cards for a single central bank."""
    try:
        bank_key = normalize_bank(bank)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    config = _bank_config(bank_key)
    next_summary = await _next_summary_or_hold(bank_key, session)
    outlook = await _outlook_or_hold(bank_key, session)

    as_of_result = await session.execute(
        text("SELECT MAX(curve_date) FROM ois_cache WHERE bank = :bank"),
        {"bank": bank_key},
    )
    as_of_date: date | None = as_of_result.scalar_one_or_none()
    market_data = await _market_data_status(bank_key, session)
    probabilities = await compute_meeting_probabilities(bank_key, db_session=session)

    return {
        "bank": bank_key,
        "current_rate": config["current_rate"],
        "current_rate_label": _RATE_LABELS.get(bank_key, "Policy Rate"),
        "last_ois_rate": next_summary["last_ois_rate"],
        "as_of_date": as_of_date.isoformat() if as_of_date else date.today().isoformat(),
        "market_data": market_data,
        "next_meeting": {
            "meeting_dt": _dt_iso(next_summary["meeting_dt"]),
            "seconds_until": next_summary["seconds_until"],
            "dominant_outcome": next_summary["dominant_outcome"],
            "dominant_prob_pct": next_summary["dominant_prob_pct"],
            "implied_delta_bps": next_summary["implied_delta_bps"],
        },
        "twelve_month_outlook": {
            "total_bps": outlook["total_bps"],
            "direction": outlook["direction"],
            "description": outlook["description"],
        },
        "step_bps": int(config["step_bps"]),
        "low_liquidity_curve": bool(config["low_liquidity_curve"]),
        "curve_confidence_note": LOW_LIQUIDITY_NOTE if config["low_liquidity_curve"] else "",
        "rate_path": _rate_path_rows(probabilities, config["current_rate"]),
    }


# ══════════════════════════════════════════════════════════════════════
#  Endpoint 2 — meetings/{bank}
# ══════════════════════════════════════════════════════════════════════

@router.get("/meetings/{bank}")
async def rate_meetings(
    bank: str,
    step: int | None = Query(None, ge=1, le=200, description="Move size in bps"),
    n: int = Query(12, ge=1, le=50, description="Number of meetings to return"),
    snapshot: str = Query("current", pattern="^(current|previous_day|one_week_ago|one_month_ago)$"),
    session: AsyncSession = SessionDep,
) -> dict:
    """Full meeting-by-meeting probability table for one bank."""
    try:
        bank_key = normalize_bank(bank)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    meetings_meta = await get_upcoming_meetings(bank_key, n, session)
    probabilities = await compute_meeting_probabilities(
        bank_key,
        step_bps=float(step or _bank_config(bank_key)["step_bps"]),
        n_meetings=n,
        db_session=session,
    )

    config = _bank_config(bank_key)
    today = date.today()

    rows = []
    snapshot_date: date | None = None
    snapshot_label = "Current"
    selected_probabilities = probabilities
    if snapshot != "current":
        target_days = {
            "previous_day": 1,
            "one_week_ago": 7,
            "one_month_ago": 30,
        }[snapshot]
        target_date = today - timedelta(days=target_days)
        snapshot_date = await _resolve_curve_date(bank_key, target_date, session)
        snapshot_label = {
            "previous_day": "Previous Day",
            "one_week_ago": "1 Week Ago",
            "one_month_ago": "1 Month Ago",
        }[snapshot]
        if snapshot_date is not None:
            selected_probabilities = await compute_meeting_probabilities(
                bank_key,
                step_bps=float(step or config["step_bps"]),
                curve_date=snapshot_date,
                n_meetings=n,
                db_session=session,
            )
        else:
            selected_probabilities = []

    has_market_data = any(prob.data_state == DATA_STATE_LIVE for prob in selected_probabilities)
    if selected_probabilities:
        for prob, meta in zip(selected_probabilities, meetings_meta, strict=False):
            rows.append(_meeting_probability_row(prob, bool(meta.get("is_official", True)), float(step or config["step_bps"])))
    else:
        rows = _hold_meeting_rows(bank_key, meetings_meta)

    return {
        "bank": bank_key,
        "current_rate": config["current_rate"],
        "snapshot_mode": snapshot,
        "snapshot_label": snapshot_label,
        "snapshot_date": snapshot_date.isoformat() if snapshot_date else None,
        "market_data": await _market_data_status(bank_key, session),
        "market_data_available": has_market_data if snapshot == "current" else snapshot_date is not None,
        "meetings": rows,
        "rate_path": _rate_path_rows(probabilities, config["current_rate"]),
    }


@router.get("/meetings")
async def rate_meetings_legacy(
    bank: str = Query(..., examples=["FED"]),
    step: int | None = Query(None, ge=1, le=200),
    n: int = Query(12, ge=1, le=50),
    session: AsyncSession = SessionDep,
) -> dict:
    """Compatibility wrapper for older /meetings?bank=FED callers."""
    return await rate_meetings(bank=bank, step=step, n=n, session=session)


def _hold_meeting_rows(bank_key: str, meetings_meta: list[dict[str, Any]]) -> list[dict[str, Any]]:
    config = _bank_config(bank_key)
    rows = []
    for meta in meetings_meta:
        rows.append(
            {
                "meeting_dt": _dt_iso(meta.get("meeting_dt")),
                "implied_rate": config["current_rate"],
                "cut_prob": None,
                "hold_prob": None,
                "hike_prob": None,
                "outcome_distribution": None,
                "num_moves": None,
                "cumulative_num_moves": None,
                "delta_bps": None,
                "cumulative_delta_bps": None,
                "is_official": bool(meta.get("is_official", True)),
                "market_data_available": False,
                "proximity_lock": False,
                "proximity_warning": "",
                "low_liquidity_curve": bool(config["low_liquidity_curve"]),
                "curve_confidence_note": LOW_LIQUIDITY_NOTE if config["low_liquidity_curve"] else "",
                "data_state": DATA_STATE_NO_CURVE,
                "data_state_message": f"No OIS/futures curve available for {bank_key}.",
            }
        )
    return rows


def _meeting_probability_row(prob: Any, is_official: bool, step_bps: float) -> dict[str, Any]:
    return {
        "meeting_dt": _dt_iso(prob.meeting_dt),
        "implied_rate": prob.implied_rate,
        "cut_prob": prob.cut_prob,
        "hold_prob": prob.hold_prob,
        "hike_prob": prob.hike_prob,
        "outcome_distribution": prob.outcome_distribution,
        "num_moves": prob.num_moves,
        "cumulative_num_moves": (
            round(prob.cumulative_delta_bps / float(step_bps), 4)
            if prob.data_state == DATA_STATE_LIVE and step_bps
            else None
        ),
        "delta_bps": prob.delta_bps,
        "cumulative_delta_bps": prob.cumulative_delta_bps if prob.data_state == DATA_STATE_LIVE else None,
        "is_official": is_official,
        "market_data_available": prob.data_state == DATA_STATE_LIVE,
        "proximity_lock": prob.proximity_lock,
        "proximity_warning": prob.proximity_warning,
        "low_liquidity_curve": prob.low_liquidity_curve,
        "curve_confidence_note": prob.curve_confidence_note,
        "data_state": prob.data_state,
        "data_state_message": prob.data_state_message,
    }


async def _resolve_snapshot_date(
    bank_key: str,
    target_date: date,
    session: AsyncSession,
) -> date | None:
    result = await session.execute(
        text(
            """
            SELECT max(snapshot_date)
            FROM rate_snapshots
            WHERE bank = :bank
              AND snapshot_date <= :target_date
            """
        ),
        {"bank": bank_key, "target_date": target_date},
    )
    resolved = result.scalar_one_or_none()
    if resolved is not None:
        return resolved
    fallback_result = await session.execute(
        text(
            """
            SELECT min(snapshot_date)
            FROM rate_snapshots
            WHERE bank = :bank
            """
        ),
        {"bank": bank_key},
    )
    return fallback_result.scalar_one_or_none()


async def _resolve_curve_date(
    bank_key: str,
    target_date: date,
    session: AsyncSession,
) -> date | None:
    result = await session.execute(
        text(
            """
            SELECT max(curve_date)
            FROM ois_cache
            WHERE bank = :bank
              AND curve_date <= :target_date
            """
        ),
        {"bank": bank_key, "target_date": target_date},
    )
    resolved = result.scalar_one_or_none()
    if resolved is not None:
        return resolved
    fallback_result = await session.execute(
        text(
            """
            SELECT min(curve_date)
            FROM ois_cache
            WHERE bank = :bank
            """
        ),
        {"bank": bank_key},
    )
    return fallback_result.scalar_one_or_none()


def _rate_path_rows(probabilities: list[Any], current_policy_rate: float) -> list[dict[str, Any]]:
    return [
        {
            "meeting_date": prob.meeting_dt.date().isoformat(),
            "implied_rate": prob.implied_rate,
            "delta_bps": prob.delta_bps,
            "current_policy_rate": current_policy_rate,
        }
        for prob in probabilities
        if prob.data_state == DATA_STATE_LIVE
    ]


# ══════════════════════════════════════════════════════════════════════
#  Endpoint 3 — rate-path/{bank}
# ══════════════════════════════════════════════════════════════════════

@router.get("/rate-path/{bank}")
async def rate_path(
    bank: str,
    snapshots: str | None = Query(
        None,
        description="Comma-separated past snapshot dates, e.g. 2026-05-01,2026-04-17",
    ),
    session: AsyncSession = SessionDep,
) -> dict:
    """Implied rate path chart data, with optional historical snapshot overlays."""
    try:
        bank_key = normalize_bank(bank)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    config = _bank_config(bank_key)
    today = date.today()

    meetings_meta = await get_upcoming_meetings(bank_key, 12, session)
    probabilities = await compute_meeting_probabilities(bank_key, db_session=session)
    live_probabilities = [prob for prob in probabilities if prob.data_state == DATA_STATE_LIVE]
    current_points = (
        [
            {"meeting_dt": _dt_iso(prob.meeting_dt), "implied_rate": prob.implied_rate}
            for prob in live_probabilities
        ]
        if live_probabilities
        else []
    )

    series: list[dict[str, Any]] = [
        {
            "label": "Current",
            "snapshot_date": today.isoformat(),
            "points": current_points,
        }
    ]

    if snapshots:
        requested_dates: list[date] = []
        for raw in snapshots.split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                requested_dates.append(date.fromisoformat(raw))
            except ValueError:
                continue

        if requested_dates:
            used_dates: set[date] = set()
            for snap_date in requested_dates:
                resolved_date = await _resolve_snapshot_date(bank_key, snap_date, session)
                if resolved_date is None or resolved_date in used_dates:
                    continue
                used_dates.add(resolved_date)
                snap_result = await session.execute(
                    text(
                        """
                        SELECT meeting_dt, implied_rate
                        FROM rate_snapshots
                        WHERE bank = :bank AND snapshot_date = :snap_date
                        ORDER BY meeting_dt
                        """
                    ),
                    {"bank": bank_key, "snap_date": resolved_date},
                )
                points = [
                    {
                        "meeting_dt": _dt_iso(row.meeting_dt),
                        "implied_rate": float(row.implied_rate),
                    }
                    for row in snap_result.all()
                ]
                if points:
                    series.append(
                        {
                            "label": _snapshot_label(resolved_date, today),
                            "snapshot_date": resolved_date.isoformat(),
                            "points": points,
                        }
                    )

    return {
        "bank": bank_key,
        "current_rate": config["current_rate"],
        "series": series,
        "rate_path": _rate_path_rows(probabilities, config["current_rate"]),
        "data_state": DATA_STATE_LIVE if live_probabilities else DATA_STATE_NO_CURVE,
    }


# ══════════════════════════════════════════════════════════════════════
#  Endpoint 4 — all-banks
# ══════════════════════════════════════════════════════════════════════

@router.get("/all-banks")
async def all_banks_summary() -> dict:
    """Next-meeting summary for all 8 supported central banks."""

    async def _bank_row(bank_key: str) -> dict[str, Any] | None:
        try:
            # Each call creates its own session via session_scope internally
            # (db_session=None triggers the self-managed branch).
            config = _bank_config(bank_key)
            next_summary = await _next_summary_or_hold(bank_key, None)
            outlook = await _outlook_or_hold(bank_key, None)
            return {
                "bank": bank_key,
                "current_rate": config["current_rate"],
                "next_meeting_dt": _dt_iso(next_summary["meeting_dt"]),
                "dominant_outcome": next_summary["dominant_outcome"],
                "dominant_prob_pct": next_summary["dominant_prob_pct"],
                "twelve_month_bps": outlook["total_bps"],
            }
        except Exception:
            return None

    results = await asyncio.gather(*[_bank_row(b) for b in SUPPORTED_BANKS])
    return {"banks": [r for r in results if r is not None]}


async def _next_summary_or_hold(
    bank_key: str,
    session: AsyncSession | None,
) -> dict[str, Any]:
    config = _bank_config(bank_key)
    try:
        return await get_next_meeting_summary(bank_key, db_session=session)
    except ValueError:
        meetings = await get_upcoming_meetings(bank_key, 1, session)
        if not meetings:
            raise
        meeting_dt = datetime.fromisoformat(str(meetings[0]["meeting_dt"]))
        seconds_until = max(
            0,
            int((meeting_dt.astimezone(UTC) - datetime.now(UTC)).total_seconds()),
        )
        return {
            "meeting_dt": meeting_dt,
            "seconds_until": seconds_until,
            "dominant_outcome": "N/A",
            "dominant_prob_pct": None,
            "implied_delta_bps": None,
            "current_rate": config["current_rate"],
            "last_ois_rate": config["current_rate"],
            "data_state": DATA_STATE_NO_CURVE,
            "data_state_message": f"No OIS/futures curve available for {bank_key}.",
        }


async def _outlook_or_hold(
    bank_key: str,
    session: AsyncSession | None,
) -> dict[str, Any]:
    try:
        return await get_twelve_month_outlook(bank_key, db_session=session)
    except ValueError:
        return {
            "total_bps": 0.0,
            "num_moves": 0.0,
            "direction": "hold",
            "description": "Data unavailable",
        }


async def _market_data_status(bank_key: str, session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(
        text(
            """
            SELECT source, MAX(curve_date) AS curve_date, COUNT(*) AS rows
            FROM ois_cache
            WHERE bank = :bank
            GROUP BY source
            ORDER BY curve_date DESC, rows DESC
            LIMIT 1
            """
        ),
        {"bank": bank_key},
    )
    row = result.first()
    if row is None:
        return {
            "available": False,
            "label": "Calendar only",
            "source": None,
            "as_of_date": None,
            "is_proxy": False,
        }
    source = str(row.source)
    is_proxy = source.endswith("_proxy")
    label_by_source = {
        "yfinance_ZQ": "Live futures",
        "ecb_estr_ois": "Live OIS",
        "ecb_yc_proxy": "ECB yield-curve proxy",
        "boe_sonia": "Live OIS",
        "rba_f17": "Live OIS",
        "tradingview_IB": "ASX cash-rate futures",
        "tradingview_CORRA_proxy": "CORRA futures proxy",
        "tfx_TONA_proxy": "TFX TONA futures proxy",
        "rbnz_wholesale_proxy": "RBNZ wholesale proxy",
        "tradingview_SARON_proxy": "SARON futures proxy",
    }
    return {
        "available": True,
        "label": label_by_source.get(source, source),
        "source": source,
        "as_of_date": row.curve_date.isoformat() if row.curve_date else None,
        "is_proxy": is_proxy,
    }
