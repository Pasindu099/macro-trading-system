"""Build daily benchmark yield spreads for configured currency pairs."""

from datetime import date, timedelta
from pathlib import Path

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_sessionmaker


PAIRS_CONFIG = Path("config/pairs.yaml")


def _business_days_between(earlier: date, later: date) -> int:
    day = earlier
    count = 0
    while day < later:
        day += timedelta(days=1)
        if day.weekday() < 5:
            count += 1
    return count


def align_spread(
    base: dict[date, float], quote: dict[date, float], *, max_fill_days: int = 2,
) -> list[dict]:
    """Align observed dates, carrying the other side for at most two business days."""
    output = []
    base_date = quote_date = None
    for day in sorted(set(base) | set(quote)):
        if day in base:
            base_date = day
        if day in quote:
            quote_date = day
        if base_date is None or quote_date is None:
            continue
        if any(_business_days_between(source_day, day) > max_fill_days for source_day in (base_date, quote_date)):
            continue
        base_yield, quote_yield = base[base_date], quote[quote_date]
        output.append({
            "obs_date": day, "base_yield": base_yield, "quote_yield": quote_yield,
            "spread_bp": (base_yield - quote_yield) * 100,
        })
    return output


async def build_yield_spreads(session: AsyncSession) -> int:
    config = yaml.safe_load(PAIRS_CONFIG.read_text(encoding="utf-8"))
    result = await session.execute(text("""
        SELECT DISTINCT ON (country_code, maturity, market_observation_date)
            country_code, maturity, market_observation_date, yield_value::float AS yield_value
        FROM government_yield_observations
        WHERE quality_status = 'valid' AND NOT is_outlier
        ORDER BY country_code, maturity, market_observation_date, ingested_at DESC, id DESC
    """))
    curves: dict[tuple[str, str], dict[date, float]] = {}
    for row in result:
        curves.setdefault((row.country_code, row.maturity), {})[row.market_observation_date] = row.yield_value

    benchmarks = config["currency_benchmarks"]
    specs = [
        (pair["pair"], benchmarks[pair["base"]], benchmarks[pair["quote"]], ("2Y", "10Y", "30Y"))
        for pair in config["pairs"]
    ]
    specs += [
        (entry["name"], entry["base_country"], entry["quote_country"], entry["tenors"])
        for entry in config["named_spreads"]
    ]
    rows = []
    for name, base_country, quote_country, tenors in specs:
        for tenor in tenors:
            base = curves.get((base_country, tenor), {})
            quote = curves.get((quote_country, tenor), {})
            for aligned in align_spread(base, quote):
                rows.append({"spread_name": name, "tenor": tenor, **aligned})

    await session.execute(text("DELETE FROM yield_spreads"))
    if rows:
        await session.execute(text("""
            INSERT INTO yield_spreads
                (spread_name, tenor, obs_date, base_yield, quote_yield, spread_bp)
            VALUES (:spread_name, :tenor, :obs_date, :base_yield, :quote_yield, :spread_bp)
        """), rows)
    return len(rows)


async def get_spread(pair: str, tenor: str = "2Y", days: int = 250) -> dict:
    config = yaml.safe_load(PAIRS_CONFIG.read_text(encoding="utf-8"))
    names = [item["pair"] for item in config["pairs"]] + [item["name"] for item in config["named_spreads"]]
    name = next((value for value in names if value.replace("/", "").upper() == pair.replace("/", "").upper()), None)
    if name is None:
        return {"pair": pair, "tenor": tenor, "status": "unavailable", "reason": "Unknown spread"}
    if tenor not in {"2Y", "10Y", "30Y"}:
        return {"pair": name, "tenor": tenor, "status": "unavailable", "reason": "Unsupported tenor"}
    async with get_sessionmaker()() as session:
        result = await session.execute(text("""
            SELECT obs_date, base_yield::float AS base_yield,
                   quote_yield::float AS quote_yield, spread_bp::float AS spread_bp
            FROM yield_spreads
            WHERE spread_name = :name AND tenor = :tenor
            ORDER BY obs_date DESC LIMIT :days
        """), {"name": name, "tenor": tenor, "days": days})
        rows = [dict(row._mapping) for row in result]
    if not rows:
        reason = "FR yield data is absent" if name == "FR-DE" else "30Y yield data is absent" if tenor == "30Y" else "No overlapping yields"
        return {"pair": name, "tenor": tenor, "status": "unavailable", "reason": reason, "rows": []}
    return {"pair": name, "tenor": tenor, "status": "available", "rows": list(reversed(rows))}
