"""Build daily benchmark yield spreads for configured currency pairs."""

from datetime import date, timedelta
from pathlib import Path

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


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
