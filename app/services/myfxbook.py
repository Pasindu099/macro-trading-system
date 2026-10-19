"""Step 14 A: Myfxbook community outlook via the official API (login -> session -> outlook).

Credentials come from MYFXBOOK_EMAIL / MYFXBOOK_PASSWORD and are never logged: request URLs
carry them as query parameters, so errors are logged by type and status code only.
The session is reused until the API reports it invalid; then one re-login is attempted.
Snapshots are stored every 4 hours in retail_sentiment_snapshots (kept permanently).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from itertools import combinations
from pathlib import Path
from typing import Any

import httpx
import yaml
from sqlalchemy import text

from app.db.session import session_scope
from app.services import source_identity
from app.settings import get_settings

logger = logging.getLogger(__name__)

API = "https://www.myfxbook.com/api"
SOURCE = "myfxbook"
NOT_CONFIGURED = "Myfxbook credentials not configured"
RETRY_DELAYS = (2.0, 8.0, 30.0)
PAIRS_CONFIG = Path(__file__).resolve().parents[2] / "config" / "pairs.yaml"


class MyfxbookError(RuntimeError):
    """Error with a message that never contains credentials or session ids."""


def g10_pairs() -> list[str]:
    """28 pairs of the eight dashboard currencies, base = higher market priority (EURUSD, USDJPY...)."""
    priority = yaml.safe_load(PAIRS_CONFIG.read_text(encoding="utf-8"))["currency_priority"]
    return [a + b for a, b in combinations(priority, 2)]


def credentials() -> tuple[str, str] | None:
    settings = get_settings()
    email, password = (settings.myfxbook_email or "").strip(), (settings.myfxbook_password or "").strip()
    return (email, password) if email and password else None


def _is_invalid_session(payload: dict[str, Any]) -> bool:
    return bool(payload.get("error")) and "session" in str(payload.get("message", "")).lower()


class MyfxbookClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, sleep=asyncio.sleep) -> None:
        self._transport = transport
        self._sleep = sleep
        self._session: str | None = None
        self.logins = 0

    async def _get(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        last: str = "no attempt"
        async with httpx.AsyncClient(timeout=20.0, headers=source_identity.headers(),
                                     transport=self._transport) as client:
            for delay in (0.0, *RETRY_DELAYS):
                if delay:
                    await self._sleep(delay)
                try:
                    response = await client.get(f"{API}/{endpoint}", params=params)
                except httpx.HTTPError as exc:
                    last = type(exc).__name__  # never str(exc): it contains the URL with credentials
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    last = f"HTTP {response.status_code}"
                    continue
                if response.status_code >= 400:
                    raise MyfxbookError(f"{endpoint}: HTTP {response.status_code}")
                try:
                    return response.json()
                except ValueError:
                    raise MyfxbookError(f"{endpoint}: non-JSON response") from None
        raise MyfxbookError(f"{endpoint}: failed after {len(RETRY_DELAYS) + 1} attempts ({last})")

    async def login(self) -> str:
        creds = credentials()
        if creds is None:
            raise MyfxbookError(NOT_CONFIGURED)
        payload = await self._get("login.json", {"email": creds[0], "password": creds[1]})
        self.logins += 1
        if payload.get("error") or not payload.get("session"):
            raise MyfxbookError(f"login rejected: {str(payload.get('message') or 'no session')[:80]}")
        self._session = str(payload["session"])
        return self._session

    async def outlook(self) -> list[dict[str, Any]]:
        """Community outlook symbols; reuses the session, re-logs in once if it expired."""
        for attempt in range(2):
            session = self._session or await self.login()
            payload = await self._get("get-community-outlook.json", {"session": session})
            if _is_invalid_session(payload) and attempt == 0:
                self._session = None
                continue
            if payload.get("error"):
                raise MyfxbookError(f"outlook error: {str(payload.get('message'))[:80]}")
            return list(payload.get("symbols") or [])
        raise MyfxbookError("outlook: session still invalid after re-login")


_client = MyfxbookClient()


def _dec(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _int(value: Any) -> int | None:
    number = _dec(value)
    return int(number) if number is not None else None


def snapshot_rows(symbols: list[dict[str, Any]], taken_at: datetime) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows for the 28 G10 pairs present in the outlook, and the G10 pairs missing from it."""
    wanted = set(g10_pairs())
    rows = []
    for item in symbols:
        pair = str(item.get("name", "")).upper().replace("/", "").strip()
        long_pct, short_pct = _dec(item.get("longPercentage")), _dec(item.get("shortPercentage"))
        if pair not in wanted or long_pct is None or short_pct is None:
            continue
        rows.append({
            "source": SOURCE, "pair": pair, "taken_at": taken_at, "long_pct": long_pct, "short_pct": short_pct,
            "long_positions": _int(item.get("longPositions")), "short_positions": _int(item.get("shortPositions")),
            "avg_long_price": _dec(item.get("avgLongPrice")), "avg_short_price": _dec(item.get("avgShortPrice")),
        })
    found = {row["pair"] for row in rows}
    return rows, [pair for pair in g10_pairs() if pair not in found]


INSERT = text("""
    INSERT INTO retail_sentiment_snapshots (source, pair, taken_at, long_pct, short_pct, long_positions,
        short_positions, avg_long_price, avg_short_price)
    VALUES (:source, :pair, :taken_at, :long_pct, :short_pct, :long_positions, :short_positions,
        :avg_long_price, :avg_short_price)
    ON CONFLICT (source, pair, taken_at) DO NOTHING
""")


async def store_snapshot(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    async with session_scope() as session:
        await session.execute(INSERT, rows)
    return len(rows)


async def run_snapshot(client: MyfxbookClient | None = None) -> dict[str, Any]:
    """4-hourly job body. Missing credentials is a reported state, not an error."""
    from app.ingestion.run_logger import run_logger

    async with run_logger("job:myfxbook_retail") as tracker:
        if credentials() is None:
            tracker.mark_skipped()
            logger.info("Myfxbook snapshot skipped: %s", NOT_CONFIGURED)
            return {"status": "not_configured", "message": NOT_CONFIGURED}
        taken_at = datetime.now(UTC).replace(second=0, microsecond=0)
        symbols = await (client or _client).outlook()
        tracker.record_api_call()
        rows, missing = snapshot_rows(symbols, taken_at)
        stored = await store_snapshot(rows)
        tracker.record_rows(stored)
        logger.info("Myfxbook snapshot: %d pairs stored, missing %s", stored, ",".join(missing) or "none")
        return {"status": "ok", "stored": stored, "missing": missing}


async def latest(pair: str | None = None) -> list[dict[str, Any]]:
    """Most recent snapshot per pair."""
    async with session_scope() as session:
        rows = (await session.execute(text("""
            SELECT DISTINCT ON (pair) pair, taken_at, long_pct, short_pct, long_positions, short_positions,
                   avg_long_price, avg_short_price
            FROM retail_sentiment_snapshots WHERE source = :source
              AND (cast(:pair AS text) IS NULL OR pair = cast(:pair AS text))
            ORDER BY pair, taken_at DESC
        """), {"source": SOURCE, "pair": pair})).mappings().all()
    return [dict(row) for row in rows]


async def daily(pair: str, days: int = 90) -> list[dict[str, Any]]:
    """Last snapshot of each UTC day (retail_sentiment_daily view), oldest first."""
    async with session_scope() as session:
        rows = (await session.execute(text("""
            SELECT day, taken_at, long_pct, short_pct FROM retail_sentiment_daily
            WHERE source = :source AND pair = :pair AND day >= current_date - :days
            ORDER BY day
        """), {"source": SOURCE, "pair": pair, "days": days})).mappings().all()
    return [dict(row) for row in rows]
