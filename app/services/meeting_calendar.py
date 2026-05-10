"""Central-bank meeting calendar helpers for rate probability tools."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope

logger = logging.getLogger(__name__)

CB_MEETINGS_PATH = Path("config/cb_meetings.yaml")
SUPPORTED_BANKS = ("FED", "ECB", "BOE", "BOJ", "RBA", "BOC", "RBNZ", "SNB")


async def get_upcoming_meetings(
    bank: str,
    n: int = 12,
    db_session: AsyncSession | None = None,
) -> list[dict[str, Any]]:
    """Return next n meetings for a bank.

    Reads from cb_meetings first and falls back to config/cb_meetings.yaml when
    the table is unavailable or empty.
    """
    bank = normalize_bank(bank)
    limit = max(1, min(int(n), 50))
    if db_session is None:
        async with session_scope() as session:
            return await get_upcoming_meetings(bank, limit, session)

    table_empty = await _is_meeting_table_empty(db_session)
    if not table_empty:
        rows = await _load_upcoming_from_db(db_session, bank, limit)
        if rows:
            return rows

    return _load_upcoming_from_yaml(bank, limit)


async def get_next_meeting(
    bank: str,
    db_session: AsyncSession | None = None,
) -> dict[str, Any]:
    """Return the single next upcoming meeting plus seconds until it."""
    meetings = await get_upcoming_meetings(bank, 1, db_session)
    if not meetings:
        raise ValueError(f"No upcoming meetings found for {normalize_bank(bank)}")

    meeting = dict(meetings[0])
    meeting_dt = _parse_dt(meeting["meeting_dt"])
    now = datetime.now(UTC)
    meeting["seconds_until"] = max(0, int((meeting_dt.astimezone(UTC) - now).total_seconds()))
    return meeting


async def seed_meetings_from_yaml(db_session: AsyncSession) -> int:
    """One-time load of config/cb_meetings.yaml into cb_meetings.

    Rows already present are skipped with ON CONFLICT DO NOTHING. If the
    migration has not been applied yet, this logs and exits without crashing
    startup.
    """
    if not await _is_meeting_table_empty(db_session):
        return 0

    payload = _load_config()
    inserted = 0
    try:
        for bank, config in payload.items():
            if normalize_bank(bank) not in SUPPORTED_BANKS:
                continue
            for meeting in config.get("meetings", []):
                meeting_dt = _parse_dt(meeting.get("dt"))
                result = await db_session.execute(
                    text(
                        """
                        INSERT INTO cb_meetings (bank, meeting_dt, is_official)
                        VALUES (:bank, :meeting_dt, :is_official)
                        ON CONFLICT (bank, meeting_dt) DO NOTHING
                        """
                    ),
                    {
                        "bank": normalize_bank(bank),
                        "meeting_dt": meeting_dt,
                        "is_official": bool(meeting.get("official", True)),
                    },
                )
                inserted += int(result.rowcount or 0)
    except (SQLAlchemyError, OSError):
        await db_session.rollback()
        logger.info(
            "Skipping rate-probability meeting seed; run alembic upgrade head first."
        )
        return 0

    logger.info("Seeded %d central-bank meeting rows.", inserted)
    return inserted


def normalize_bank(bank: str) -> str:
    normalized = str(bank or "").strip().upper()
    if normalized not in SUPPORTED_BANKS:
        raise ValueError(f"Unsupported central bank {bank!r}")
    return normalized


async def _is_meeting_table_empty(db_session: AsyncSession) -> bool:
    try:
        result = await db_session.execute(text("SELECT COUNT(*) FROM cb_meetings"))
    except (SQLAlchemyError, OSError):
        await db_session.rollback()
        return True
    return int(result.scalar_one() or 0) == 0


async def _load_upcoming_from_db(
    db_session: AsyncSession,
    bank: str,
    limit: int,
) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    try:
        result = await db_session.execute(
            text(
                """
                SELECT bank, meeting_dt, is_official
                FROM cb_meetings
                WHERE bank = :bank AND meeting_dt >= :now
                ORDER BY meeting_dt
                LIMIT :limit
                """
            ),
            {"bank": bank, "now": now, "limit": limit},
        )
    except (SQLAlchemyError, OSError):
        await db_session.rollback()
        return []

    return [
        _meeting_dict(row.bank, row.meeting_dt, row.is_official, source="db")
        for row in result.all()
    ]


def _load_upcoming_from_yaml(bank: str, limit: int) -> list[dict[str, Any]]:
    payload = _load_config()
    bank_config = payload.get(bank, {})
    now = datetime.now(UTC)
    meetings = []
    for meeting in bank_config.get("meetings", []):
        meeting_dt = _parse_dt(meeting.get("dt"))
        if meeting_dt.astimezone(UTC) < now:
            continue
        meetings.append(
            _meeting_dict(
                bank,
                meeting_dt,
                bool(meeting.get("official", True)),
                source="yaml",
            )
        )
    meetings.sort(key=lambda item: _parse_dt(item["meeting_dt"]))
    return meetings[:limit]


def _load_config() -> dict[str, Any]:
    if not CB_MEETINGS_PATH.exists():
        return {}
    with CB_MEETINGS_PATH.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return payload if isinstance(payload, dict) else {}


def _meeting_dict(
    bank: str,
    meeting_dt: datetime,
    official: bool | None,
    *,
    source: str,
) -> dict[str, Any]:
    return {
        "bank": normalize_bank(bank),
        "meeting_dt": meeting_dt.isoformat(),
        "is_official": bool(official),
        "source": source,
    }


def _parse_dt(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
