"""Release-day FX reactions: daily close-to-close moves, not intraday."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_sessionmaker, session_scope
from app.ingestion.run_logger import run_logger
from app.services.job_lock import job_lock

MIN_RELEASES = 8
BIG_SURPRISE = 1.0
HIT_SURPRISE = 0.5
REACTION_PAIRS: dict[str, list[str]] = yaml.safe_load(
    Path("config/pairs.yaml").read_text(encoding="utf-8"))["event_reaction_pairs"]

_MOVES_SQL = text("""
WITH rel AS (
    SELECT r.id AS release_id, r.indicator_id, r.released_at, r.period,
           (r.released_at AT TIME ZONE 'UTC')::date AS day,
           s.surprise_normalized::float AS sigma
    FROM indicator_releases r
    LEFT JOIN event_innovation_scores s ON s.release_id = r.id
    WHERE r.is_latest AND r.actual IS NOT NULL AND r.released_at <= now()
      AND r.indicator_id = ANY(:ids)
)
SELECT rel.*, p.pair, c1.close AS close, c0.close AS prev_close
FROM rel CROSS JOIN unnest(CAST(:pairs AS text[])) AS p(pair)
JOIN LATERAL (SELECT close_value::float AS close FROM fx_spot_observations f
              WHERE f.pair = p.pair AND f.observation_date = rel.day
                AND f.data_frequency = 'daily' AND NOT f.is_outlier LIMIT 1) c1 ON true
JOIN LATERAL (SELECT close_value::float AS close FROM fx_spot_observations f
              WHERE f.pair = p.pair AND f.observation_date < rel.day
                AND f.data_frequency = 'daily' AND NOT f.is_outlier
              ORDER BY f.observation_date DESC LIMIT 1) c0 ON true
ORDER BY rel.released_at DESC
""")


def pairs_for(currency: str) -> list[str]:
    return REACTION_PAIRS.get(currency, [])


def direction_currency(pair: str, currency: str) -> str:
    """The currency whose move is reported; calendar-only CNY reads as the pair's base."""
    base, quote = pair.split("/")
    return currency if currency in (base, quote) else base


def signed_move_pips(pair: str, prev_close: float, close: float, currency: str) -> float:
    """Close-to-close move in pips; positive means ``currency`` (or its proxy) rose."""
    base, quote = pair.split("/")
    pips = (close - prev_close) / (0.01 if quote == "JPY" else 0.0001)
    return -pips if direction_currency(pair, currency) == quote else pips


def compute_stats(moves: Iterable[tuple[float | None, float]]) -> dict[str, Any]:
    """``moves`` holds (surprise σ, signed move) per past release."""
    moves = list(moves)
    beats = [move for sigma, move in moves if sigma is not None and sigma > BIG_SURPRISE]
    misses = [move for sigma, move in moves if sigma is not None and sigma < -BIG_SURPRISE]
    hit_pool = [(sigma, move) for sigma, move in moves if sigma is not None and abs(sigma) >= HIT_SURPRISE]
    hits = sum(1 for sigma, move in hit_pool if move != 0 and (sigma > 0) == (move > 0))
    mean = lambda values: round(sum(values) / len(values), 2) if values else None  # noqa: E731
    return {
        "sample_n": len(moves), "avg_abs_move_pips": mean([abs(move) for _, move in moves]),
        "beat_n": len(beats), "beat_avg_pips": mean(beats),
        "miss_n": len(misses), "miss_avg_pips": mean(misses),
        "hit_n": len(hit_pool), "hit_rate": round(hits / len(hit_pool), 4) if hit_pool else None,
    }


async def release_moves(session: AsyncSession, indicator_ids: list[int], currency: str,
                        pairs: list[str] | None = None) -> list[dict[str, Any]]:
    pairs = pairs if pairs is not None else pairs_for(currency)
    if not indicator_ids or not pairs:
        return []
    rows = (await session.execute(_MOVES_SQL, {"ids": indicator_ids, "pairs": pairs})).mappings().all()
    return [dict(row) | {"move_pips": round(signed_move_pips(row["pair"], row["prev_close"],
                                                             row["close"], currency), 1)}
            for row in rows]


async def stats_for(session: AsyncSession, indicator_id: int) -> dict[str, dict[str, Any]]:
    rows = (await session.execute(text(
        "SELECT * FROM event_reaction_stats WHERE indicator_id = :id"), {"id": indicator_id})).mappings().all()
    return {row["pair"]: {key: (float(value) if key.endswith(("pips", "rate")) and value is not None else value)
                          for key, value in row.items()} for row in rows}


async def run_event_reaction_stats_job() -> int:
    async with job_lock("event_reaction_stats") as acquired:
        async with run_logger("job:event_reaction_stats") as run:
            if not acquired:
                run.mark_skipped()
                return 0
            async with get_sessionmaker()() as session:
                indicators = (await session.execute(text("""
                    SELECT i.id, COALESCE(c.currency_code, 'CNY') AS currency
                    FROM indicators i JOIN countries c ON c.code = i.country_code
                """))).mappings().all()
                by_currency: dict[str, list[int]] = {}
                for row in indicators:
                    by_currency.setdefault(row["currency"], []).append(row["id"])
                output = []
                for currency, ids in by_currency.items():
                    grouped: dict[tuple[int, str], list[tuple[float | None, float]]] = {}
                    for move in await release_moves(session, ids, currency):
                        grouped.setdefault((move["indicator_id"], move["pair"]), []).append(
                            (move["sigma"], move["move_pips"]))
                    output += [{"indicator_id": indicator_id, "pair": pair,
                                "direction_currency": direction_currency(pair, currency),
                                **compute_stats(values)}
                               for (indicator_id, pair), values in grouped.items()]
            async with session_scope() as session:
                await session.execute(text("DELETE FROM event_reaction_stats"))
                for row in output:
                    await session.execute(text("""
                        INSERT INTO event_reaction_stats (indicator_id, pair, direction_currency, sample_n,
                            avg_abs_move_pips, beat_n, beat_avg_pips, miss_n, miss_avg_pips, hit_n, hit_rate)
                        VALUES (:indicator_id, :pair, :direction_currency, :sample_n, :avg_abs_move_pips,
                            :beat_n, :beat_avg_pips, :miss_n, :miss_avg_pips, :hit_n, :hit_rate)
                    """), row)
            run.record_rows(len(output))
            return len(output)
