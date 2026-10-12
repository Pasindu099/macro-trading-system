"""Daily four-factor attribution of the seven USD-major FX pairs."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from itertools import combinations
from math import factorial

import numpy as np
from sqlalchemy import text

from app.db.session import get_sessionmaker, session_scope
from app.ingestion.run_logger import run_logger
from app.services.job_lock import job_lock

FACTORS = ("risk", "rates", "data", "oil")
PAIRS = {"EUR": "EUR/USD", "GBP": "GBP/USD", "AUD": "AUD/USD", "NZD": "NZD/USD",
         "CAD": "USD/CAD", "CHF": "USD/CHF", "JPY": "USD/JPY"}
COUNTRIES = {"EUR": "DE", "GBP": "UK", "AUD": "AU", "NZD": "NZ", "CAD": "CA",
             "CHF": "CH", "JPY": "JP", "USD": "US"}


def shapley_shares(returns: list[float], factors: list[list[float]]) -> dict[str, float] | None:
    """Exact Shapley decomposition of R² across all sixteen factor subsets."""
    if len(returns) < 20 or len(factors) != len(returns):
        return None
    y = np.asarray(returns, dtype=float)
    x = np.asarray(factors, dtype=float)
    if x.shape != (len(y), 4) or not np.isfinite(y).all() or not np.isfinite(x).all():
        return None
    total = float(np.sum((y - y.mean()) ** 2))
    if total <= 0:
        return None
    r2 = {0: 0.0}
    for mask in range(1, 16):
        columns = [i for i in range(4) if mask & (1 << i)]
        design = np.column_stack((np.ones(len(y)), x[:, columns]))
        coefficients = np.linalg.lstsq(design, y, rcond=None)[0]
        residual = y - design @ coefficients
        r2[mask] = max(0.0, min(1.0, 1 - float(residual @ residual) / total))
    shares = {}
    for i, name in enumerate(FACTORS):
        shares[name] = sum(
            factorial(len(subset)) * factorial(3 - len(subset)) / factorial(4)
            * (r2[sum(1 << j for j in subset) | (1 << i)] - r2[sum(1 << j for j in subset)])
            for size in range(4) for subset in combinations([j for j in range(4) if j != i], size)
        )
    explained = sum(shares.values())
    return {name: max(0.0, value / explained) for name, value in shares.items()} if explained > 0 else None


async def load_inputs(start: date, end: date) -> dict:
    """Read point-in-time observations; later releases never enter an earlier snapshot."""
    async with get_sessionmaker()() as session:
        spot = (await session.execute(text("""
            SELECT DISTINCT ON (pair, observation_date) pair, observation_date AS day,
                   close_value::float AS value FROM fx_spot_observations
            WHERE pair = ANY(:pairs) AND observation_date BETWEEN :start AND :end
              AND quality_status = 'valid' AND NOT is_outlier
            ORDER BY pair, observation_date, ingested_at DESC, id DESC
        """), {"pairs": list(PAIRS.values()), "start": start, "end": end})).mappings().all()
        yields = (await session.execute(text("""
            SELECT DISTINCT ON (country_code, market_observation_date) country_code,
                   market_observation_date AS day, yield_value::float AS value
            FROM government_yield_observations
            WHERE maturity = '2Y' AND market_observation_date BETWEEN :start AND :end
              AND quality_status = 'valid' AND NOT is_outlier
            ORDER BY country_code, market_observation_date, ingested_at DESC, id DESC
        """), {"start": start, "end": end})).mappings().all()
        fred = (await session.execute(text("""
            SELECT DISTINCT ON (series_id, observation_date) series_id, observation_date AS day,
                   value::float AS value FROM fred_observations
            WHERE series_id IN ('VIXCLS', 'DCOILBRENTEU') AND observation_date BETWEEN :start AND :end
            ORDER BY series_id, observation_date, ingested_at DESC, id DESC
        """), {"start": start, "end": end})).mappings().all()
        surprises = (await session.execute(text("""
            SELECT i.country_code, e.release_date AS day,
                   SUM(e.surprise_normalized)::float AS value
            FROM event_innovation_scores e JOIN indicators i ON i.id = e.indicator_id
            WHERE e.scored AND e.release_date BETWEEN :start AND :end
            GROUP BY i.country_code, e.release_date
        """), {"start": start, "end": end})).mappings().all()
    out = defaultdict(dict)
    for kind, rows, key in (("spot", spot, "pair"), ("yield", yields, "country_code"),
                            ("fred", fred, "series_id"), ("surprise", surprises, "country_code")):
        for row in rows:
            out[kind].setdefault(row[key], {})[row["day"]] = row["value"]
    return out


def snapshots(inputs: dict, start: date, end: date) -> list[dict]:
    rows = []
    day = start
    while day <= end:
        for window in (20, 60):
            by_factor = defaultdict(list)
            for currency, pair in PAIRS.items():
                prices = inputs["spot"].get(pair, {})
                dates = sorted(d for d in prices if d <= day)
                observations = []
                for previous, current in zip(dates, dates[1:]):
                    if (current - previous).days > 4:
                        continue
                    vix = inputs["fred"].get("VIXCLS", {})
                    oil = inputs["fred"].get("DCOILBRENTEU", {})
                    home = inputs["yield"].get(COUNTRIES[currency], {})
                    usd = inputs["yield"].get("US", {})
                    if any(d not in series for series in (vix, oil, home, usd) for d in (previous, current)):
                        continue
                    if prices[previous] <= 0 or oil[previous] <= 0:
                        continue
                    direction = 1 if pair.endswith("/USD") else -1
                    observations.append((current, direction * (prices[current] / prices[previous] - 1) * 100,
                        (vix[current] - vix[previous],
                         (home[current] - usd[current]) - (home[previous] - usd[previous]),
                         inputs["surprise"].get(COUNTRIES[currency], {}).get(current, 0)
                         - inputs["surprise"].get("US", {}).get(current, 0),
                         (oil[current] / oil[previous] - 1) * 100)))
                recent = observations[-window:]
                if len(recent) < window or (day - recent[-1][0]).days > 5:
                    continue
                shares = shapley_shares([item[1] for item in recent], [item[2] for item in recent])
                if shares is not None:
                    for factor, share in shares.items():
                        by_factor[factor].append(share)
            count = len(by_factor["risk"])
            if count and all(len(by_factor[factor]) == count for factor in FACTORS):
                rows.append({"snapshot_date": day, "window_days": window, "pair_count": count,
                             "shares": {factor: sum(by_factor[factor]) / count for factor in FACTORS}})
        day += timedelta(days=1)
    return rows


async def run_dominance_job() -> int:
    async with job_lock("dominance") as acquired:
        async with run_logger("job:dominance") as run:
            if not acquired:
                run.mark_skipped()
                return 0
            today = datetime.now(UTC).date()
            async with get_sessionmaker()() as session:
                latest = (await session.execute(text("SELECT max(snapshot_date) FROM dominance_snapshots"))).scalar_one()
            start = max(today - timedelta(days=730), latest + timedelta(days=1)) if latest else today - timedelta(days=730)
            if start > today:
                return 0
            inputs = await load_inputs(start - timedelta(days=100), today)
            rows = snapshots(inputs, start, today)
            async with session_scope() as session:
                for row in rows:
                    await session.execute(text("""
                        INSERT INTO dominance_snapshots (snapshot_date, window_days, pair_count, shares)
                        VALUES (:snapshot_date, :window_days, :pair_count, CAST(:shares AS jsonb))
                        ON CONFLICT (snapshot_date, window_days) DO UPDATE
                        SET pair_count = EXCLUDED.pair_count, shares = EXCLUDED.shares
                    """), {**row, "shares": __import__("json").dumps(row["shares"])})
            run.record_rows(len(rows))
            return len(rows)
