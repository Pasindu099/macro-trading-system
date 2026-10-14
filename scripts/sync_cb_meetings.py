"""Make cb_meetings match config/cb_meetings.yaml (Step 12 Part A).

The startup seed only fills an empty table, so corrected schedules need this
one-off sync: rows absent from config are deleted, missing rows inserted and
is_official updated.
"""

import argparse
import asyncio

from sqlalchemy import text

from app.db.session import session_scope
from app.services.meeting_calendar import SUPPORTED_BANKS, _load_config, _parse_dt, normalize_bank


def planned_rows() -> dict[tuple[str, object], bool]:
    rows = {}
    for bank, config in _load_config().items():
        if str(bank).upper() not in SUPPORTED_BANKS:
            continue
        for meeting in config.get("meetings", []):
            rows[(normalize_bank(bank), _parse_dt(meeting["dt"]))] = bool(meeting.get("official", True))
    return rows


async def main(dry_run: bool) -> None:
    planned = planned_rows()
    async with session_scope() as session:
        current = {(r.bank, r.meeting_dt): (r.id, r.is_official) for r in
                   (await session.execute(text("SELECT id, bank, meeting_dt, is_official FROM cb_meetings"))).all()}
        delete = [v[0] for k, v in current.items() if k not in planned]
        insert = [k for k in planned if k not in current]
        update = [(current[k][0], official) for k, official in planned.items() if k in current and current[k][1] != official]
        print(f"delete={len(delete)} insert={len(insert)} update={len(update)} dry_run={dry_run}")
        if dry_run:
            await session.rollback()
            return
        if delete:
            await session.execute(text("DELETE FROM cb_meetings WHERE id = ANY(:ids)"), {"ids": delete})
        for bank, meeting_dt in insert:
            await session.execute(text("INSERT INTO cb_meetings (bank, meeting_dt, is_official) VALUES (:b, :d, :o)"),
                                  {"b": bank, "d": meeting_dt, "o": planned[(bank, meeting_dt)]})
        for row_id, official in update:
            await session.execute(text("UPDATE cb_meetings SET is_official = :o WHERE id = :i"), {"o": official, "i": row_id})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    asyncio.run(main(parser.parse_args().dry_run))
