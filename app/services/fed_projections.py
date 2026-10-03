"""Load Fed SEP rounds into cb_projection_values / cb_dots / cb_risk_balance / cb_projection_errors."""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope
from app.ingestion.run_logger import run_logger
from app.processing.fed_sep import SepRound, SepValidationError, decode_html, parse_sep, validate

logger = logging.getLogger(__name__)

BANK = "FED"
FED_BASE = "https://www.federalreserve.gov"
CALENDAR_URL = f"{FED_BASE}/monetarypolicy/fomccalendars.htm"
HEADERS = {"User-Agent": "MacroDashboard/0.1 sep-parser"}
# 2020 rounds are not linked from the current calendar page (March 2020 had no SEP).
PRE_CALENDAR_ROUNDS = {
    date(2020, 6, 10): f"{FED_BASE}/monetarypolicy/fomcprojtabl20200610.htm",
    date(2020, 9, 16): f"{FED_BASE}/monetarypolicy/fomcprojtabl20200916.htm",
    date(2020, 12, 16): f"{FED_BASE}/monetarypolicy/fomcprojtabl20201216.htm",
}


def discover_rounds(calendar_html: str, since: date = date(2020, 1, 1)) -> dict[date, str]:
    """SEP accessible pages linked from the calendar; March 2022 is spelled 'fomcprojtable'."""
    rounds = dict(PRE_CALENDAR_ROUNDS)
    for name, stamp in re.findall(r"/monetarypolicy/(fomcprojtable?(\d{8})\.htm)", calendar_html):
        day = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
        rounds[day] = f"{FED_BASE}/monetarypolicy/{name}"
    return {d: u for d, u in sorted(rounds.items()) if d >= since}


async def store_round(session: AsyncSession, sep: SepRound) -> dict[str, int]:
    """Replace one round's rows (idempotent)."""
    params = {"b": BANK, "d": sep.release_date}
    for table in ("cb_projection_values", "cb_dots", "cb_risk_balance"):
        await session.execute(text(f"DELETE FROM {table} WHERE bank = :b AND release_date = :d"), params)
    if sep.values:
        await session.execute(text("""
            INSERT INTO cb_projection_values (bank, release_date, variable, horizon, stat, value)
            VALUES (:b, :d, :variable, :horizon, :stat, :value)
        """), [{**params, "variable": v, "horizon": h, "stat": s, "value": val} for (v, h, s), val in sep.values.items()])
    if sep.dots:
        await session.execute(text("""
            INSERT INTO cb_dots (bank, release_date, horizon, rate, participants) VALUES (:b, :d, :horizon, :rate, :n)
        """), [{**params, "horizon": h, "rate": r, "n": n} for (h, r), n in sep.dots.items()])
    if sep.risk:
        await session.execute(text("""
            INSERT INTO cb_risk_balance (bank, release_date, variable, kind, lower_or_downside, similar_or_balanced, higher_or_upside)
            VALUES (:b, :d, :variable, :kind, :lo, :mid, :hi)
        """), [{**params, "variable": v, "kind": k, "lo": c[0], "mid": c[1], "hi": c[2]} for (v, k), c in sep.risk.items()])
    if sep.errors:
        await session.execute(text("""
            INSERT INTO cb_projection_errors (bank, publication_year, variable, horizon, rmse)
            VALUES (:b, :y, :variable, :horizon, :rmse)
            ON CONFLICT (bank, publication_year, variable, horizon) DO UPDATE SET rmse = EXCLUDED.rmse
        """), [{"b": BANK, "y": sep.release_date.year, "variable": v, "horizon": h, "rmse": r}
               for (v, h), r in sep.errors.items()])
    return {"values": len(sep.values), "dots": len(sep.dots), "risk": len(sep.risk), "errors": len(sep.errors)}


async def load_sep_rounds(since: date = date(2020, 1, 1)) -> dict[str, Any]:
    """Fetch, parse, validate and store every SEP round; a round failing validation is rejected whole."""
    summary: dict[str, Any] = {"loaded": [], "rejected": {}, "skipped_parts": {}}
    async with run_logger("job:fed_sep") as run, httpx.AsyncClient(timeout=60, follow_redirects=True, headers=HEADERS) as client:
        calendar = await client.get(CALENDAR_URL)
        calendar.raise_for_status()
        for release_date, url in discover_rounds(calendar.text, since).items():
            try:
                response = await client.get(url)
                response.raise_for_status()
                sep = parse_sep(decode_html(response.content), release_date)
                problems = validate(sep)
            except (httpx.HTTPError, SepValidationError, ValueError, StopIteration) as exc:
                problems = [f"parse failed: {exc}"]
                sep = None
            if problems:
                logger.warning("SEP %s rejected: %s", release_date, problems)
                summary["rejected"][release_date.isoformat()] = problems
                run.errors.append(f"{release_date}: {problems[0]}")
                continue
            async with session_scope() as session:
                counts = await store_round(session, sep)
            run.record_rows(sum(counts.values()))
            summary["loaded"].append(release_date.isoformat())
            if sep.skipped:
                summary["skipped_parts"][release_date.isoformat()] = sep.skipped
    return summary
