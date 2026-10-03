"""Country curve slopes, policy gap, regime and un-inversion events."""

from datetime import date, timedelta
from typing import Any

from sqlalchemy import text

from app.db.session import get_sessionmaker


POLICY_INDICATORS = {
    "US": ("US", "fed_interest_rate_decision"),
    "DE": ("EU", "ecb_deposit_rate"),
    "UK": ("UK", "bank_rate"),
    "JP": ("JP", "boj_interest_rate_decision"),
    "AU": ("AU", "cash_rate"),
    "CA": ("CA", "overnight_rate"),
    "NZ": ("NZ", "official_cash_rate"),
    "CH": ("CH", "policy_rate"),
}

WINDOW_DAYS = {"1W": 7, "1M": 30, "3M": 90}


def classify_regime(d2_bp: float, d10_bp: float) -> str:
    if d2_bp > 0 and d10_bp > 0:
        return "bear_flattener" if d2_bp >= d10_bp else "bear_steepener"
    if d2_bp < 0 and d10_bp < 0:
        return "bull_steepener" if abs(d2_bp) >= abs(d10_bp) else "bull_flattener"
    if d2_bp <= 0 < d10_bp:
        return "twist_steepener"
    return "twist_flattener"


def latest_uninversion(slopes: dict[date, float]) -> date | None:
    inverted_since = None
    latest = None
    previous = None
    for day, slope in sorted(slopes.items()):
        if slope < 0:
            if inverted_since is None:
                inverted_since = day
        else:
            if previous is not None and previous < 0 and inverted_since is not None:
                if (day - inverted_since).days >= 90:
                    latest = day
            inverted_since = None
        previous = slope
    return latest


def _available(value: float, **extra: Any) -> dict[str, Any]:
    return {"status": "available", "value_bp": value, **extra}


def _unavailable(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason, **extra}


def calculate_curve(
    country: str,
    curves: dict[str, dict[date, float]],
    *,
    window: str = "1M",
    policy_rate: float | None = None,
    policy_date: date | None = None,
) -> dict[str, Any]:
    if window not in WINDOW_DAYS:
        raise ValueError(f"Unsupported window: {window}")
    two, ten, thirty = (curves.get(tenor, {}) for tenor in ("2Y", "10Y", "30Y"))
    shared = sorted(set(two) & set(ten))
    if not shared:
        return {"country": country, "status": "unavailable", "reason": "2Y and 10Y do not share a date", "regime_tenors": ["2Y", "10Y"]}
    latest = shared[-1]
    slope = {day: (ten[day] - two[day]) * 100 for day in shared}
    long_shared = sorted(set(ten) & set(thirty))
    long_day = long_shared[-1] if long_shared else None
    anchor_cutoff = latest - timedelta(days=WINDOW_DAYS[window])
    anchor = next((day for day in reversed(shared) if day <= anchor_cutoff), None)
    if anchor is None:
        regime = _unavailable("Insufficient 2Y/10Y history", tenors=["2Y", "10Y"])
    else:
        d2_bp = (two[latest] - two[anchor]) * 100
        d10_bp = (ten[latest] - ten[anchor]) * 100
        regime = {"status": "available", "label": classify_regime(d2_bp, d10_bp),
                  "tenors": ["2Y", "10Y"], "from_date": anchor, "to_date": latest,
                  "d2_bp": d2_bp, "d10_bp": d10_bp}

    policy = (
        _available((two[latest] - policy_rate) * 100, tenor="2Y", policy_rate=policy_rate, policy_date=policy_date)
        if policy_rate is not None and policy_date is not None and 0 <= (latest - policy_date).days <= 365
        else _unavailable("Dated policy rate is missing or stale", tenor="2Y")
    )
    return {
        "country": country, "status": "available", "as_of": latest, "window": window,
        "regime_tenors": ["2Y", "10Y"],
        "two_ten": _available(slope[latest], tenors=["2Y", "10Y"]),
        "ten_thirty": (
            _available((thirty[long_day] - ten[long_day]) * 100, tenors=["10Y", "30Y"], as_of=long_day)
            if long_day else _unavailable("30Y government yield is absent", tenors=["10Y", "30Y"])
        ),
        "two_policy": policy,
        "regime": regime,
        "is_inverted": slope[latest] < 0,
        "latest_uninversion": latest_uninversion(slope),
    }


async def get_curve(country: str, window: str = "1M") -> dict[str, Any]:
    if country not in POLICY_INDICATORS:
        return {"country": country, "status": "unavailable", "reason": "No benchmark country"}
    async with get_sessionmaker()() as session:
        result = await session.execute(text("""
            SELECT DISTINCT ON (maturity, market_observation_date)
                maturity, market_observation_date, yield_value::float AS yield_value
            FROM government_yield_observations
            WHERE country_code = :country AND maturity IN ('2Y', '10Y', '30Y')
              AND quality_status = 'valid' AND NOT is_outlier
            ORDER BY maturity, market_observation_date, ingested_at DESC, id DESC
        """), {"country": country})
        curves: dict[str, dict[date, float]] = {}
        for row in result:
            curves.setdefault(row.maturity, {})[row.market_observation_date] = row.yield_value
        policy_country, indicator = POLICY_INDICATORS[country]
        as_of = max(curves.get("2Y", {}), default=None)
        policy = (await session.execute(text("""
            SELECT r.actual::float AS rate, r.released_at::date AS release_date
            FROM indicator_releases r JOIN indicators i ON i.id = r.indicator_id
            WHERE i.country_code = :country AND i.canonical_name = :indicator
              AND r.actual IS NOT NULL
              AND r.released_at::date <= :as_of
              -- No is_latest filter: decisions have no period, so ingestion's null-period identity
              -- (REPORT_NULL_PERIODS.md) can leave the newest decision flagged not-latest.
            ORDER BY r.released_at DESC, r.retrieved_at DESC, r.id DESC LIMIT 1
        """), {"country": policy_country, "indicator": indicator, "as_of": as_of})).first() if as_of else None
    return calculate_curve(
        country, curves, window=window,
        policy_rate=policy.rate if policy else None,
        policy_date=policy.release_date if policy else None,
    )
