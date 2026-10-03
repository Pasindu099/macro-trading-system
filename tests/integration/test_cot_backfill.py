"""Idempotent TFF upserts against the compose database."""

import asyncio
from datetime import date

from sqlalchemy import text

from app.db.session import get_engine, session_scope
from app.services.cot_positions import parse_tff_rows, upsert_cot_positions
from tests.unit.test_cot_positions import _row

FIXTURE_CODE = "TEST01"


async def _run() -> tuple[int, int, int, int]:
    rows = parse_tff_rows([_row(FIXTURE_CODE, "000104")], {FIXTURE_CODE: "EUR"})
    try:
        async with session_scope() as session:
            first = await upsert_cot_positions(session, rows)
        async with session_scope() as session:
            second = await upsert_cot_positions(session, rows)
        changed = [dict(r, long=r["long"] + 1) if r["category"] == "dealer" else r for r in rows]
        async with session_scope() as session:
            third = await upsert_cot_positions(session, changed)
            count = (await session.execute(text(
                "SELECT count(*) FROM cot_positions WHERE contract_code = :c AND report_date = :d"
            ), {"c": FIXTURE_CODE, "d": date(2000, 1, 4)})).scalar_one()
        return first, second, third, count
    finally:
        async with session_scope() as session:
            await session.execute(text("DELETE FROM cot_positions WHERE contract_code = :c"), {"c": FIXTURE_CODE})
        await get_engine().dispose()


def test_rerun_writes_no_duplicates():
    first, second, third, count = asyncio.run(_run())
    assert (first, second, third, count) == (5, 0, 1, 5)
