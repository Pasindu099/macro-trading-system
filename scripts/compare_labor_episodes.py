"""Re-run situations and report changes to stored labor episodes."""

import asyncio

from sqlalchemy import text

from app.db.session import session_scope
from app.services.situations import run_situations_job


async def labor_rows():
    async with session_scope() as session:
        rows = (await session.execute(text("""
            SELECT scope_key, started_at, ended_at,
                   evidence->'current'->>'unemployment_gap_pp' AS gap
            FROM situation_episodes WHERE situation_id = 'labor_deterioration'
            ORDER BY scope_key, started_at
        """))).all()
    return {(row.scope_key, str(row.started_at)): (str(row.ended_at), row.gap) for row in rows}


async def main():
    before = await labor_rows()
    written = await run_situations_job()
    after = await labor_rows()
    changed = [(key, before.get(key), after.get(key)) for key in sorted(before.keys() | after.keys())
               if before.get(key) != after.get(key)]
    print(f"situations_written={written} labor_before={len(before)} labor_after={len(after)} changed={len(changed)}")
    for key, old, new in changed[:15]:
        print(f"changed {key}: {old} -> {new}")


if __name__ == "__main__":
    asyncio.run(main())
