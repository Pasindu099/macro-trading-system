"""Persist CFTC Traders in Financial Futures (TFF) positions per currency."""

from __future__ import annotations

import asyncio
import csv
from datetime import date, datetime, timedelta
from io import BytesIO, TextIOWrapper
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import httpx
import sqlalchemy as sa
import yaml
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope
from app.ingestion.run_logger import run_logger
from app.services.job_lock import job_lock

COT_CONFIG = Path("config/cot_contracts.yaml")
UPSERT_CHUNK = 1000

# Category → TFF column stem. Nonreportable has no spreading column.
CATEGORY_COLUMNS = {
    "dealer": "Dealer_Positions",
    "asset_manager": "Asset_Mgr_Positions",
    "leveraged_funds": "Lev_Money_Positions",
    "other_reportable": "Other_Rept_Positions",
    "nonreportable": "NonRept_Positions",
}

cot_positions = sa.table(
    "cot_positions",
    sa.column("report_date", sa.Date),
    sa.column("contract_code", sa.String),
    sa.column("category", sa.String),
    sa.column("currency", sa.String),
    sa.column("long", sa.BigInteger),
    sa.column("short", sa.BigInteger),
    sa.column("spreading", sa.BigInteger),
    sa.column("open_interest", sa.BigInteger),
    sa.column("ingested_at", sa.DateTime(timezone=True)),
)


def load_cot_config(path: Path = COT_CONFIG) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def contracts_by_code(config: dict[str, Any]) -> dict[str, str]:
    """Contract code → currency for directly sourced contracts."""
    return {str(c["code"]): ccy for ccy, c in config["contracts"].items() if not c.get("derived")}


def parse_tff_zip(content: bytes, codes: dict[str, str], *, start: date | None = None) -> list[dict[str, Any]]:
    """Parse a TFF zip into one row per (report_date, contract, category)."""
    with ZipFile(BytesIO(content)) as archive:
        name = next(n for n in archive.namelist() if n.lower().endswith((".txt", ".csv")))
        with archive.open(name) as raw:
            return parse_tff_rows(csv.DictReader(TextIOWrapper(raw, encoding="latin-1", newline="")), codes, start=start)


def parse_tff_rows(reader, codes: dict[str, str], *, start: date | None = None) -> list[dict[str, Any]]:
    output = []
    for raw in reader:
        row = {str(k).strip().lower(): v for k, v in raw.items() if k}
        code = str(row.get("cftc_contract_market_code", "")).strip()
        if code not in codes:
            continue
        # As_of_Date_In_Form_YYMMDD is the Tuesday position date and is present in every year's file.
        report_date = datetime.strptime(str(row["as_of_date_in_form_yymmdd"]).strip(), "%y%m%d").date()
        if start and report_date < start:
            continue
        open_interest = _int(row.get("open_interest_all"))
        for category, stem in CATEGORY_COLUMNS.items():
            stem = stem.lower()
            output.append({
                "report_date": report_date,
                "contract_code": code,
                "category": category,
                "currency": codes[code],
                "long": _int(row.get(f"{stem}_long_all")),
                "short": _int(row.get(f"{stem}_short_all")),
                "spreading": None if category == "nonreportable" else _int(row.get(f"{stem}_spread_all")),
                "open_interest": open_interest,
            })
    return output


def upsert_statement(rows: list[dict[str, Any]]):
    """Insert or update; unchanged rows are not touched, so a re-run reports 0."""
    stmt = insert(cot_positions).values(rows)
    excluded = stmt.excluded
    changed = sa.or_(*(
        getattr(cot_positions.c, col).is_distinct_from(getattr(excluded, col))
        for col in ("currency", "long", "short", "spreading", "open_interest")
    ))
    return stmt.on_conflict_do_update(
        index_elements=["report_date", "contract_code", "category"],
        set_={
            "currency": excluded.currency, "long": excluded.long, "short": excluded.short,
            "spreading": excluded.spreading, "open_interest": excluded.open_interest,
            "ingested_at": sa.func.now(),
        },
        where=changed,
    )


async def upsert_cot_positions(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    written = 0
    for i in range(0, len(rows), UPSERT_CHUNK):
        result = await session.execute(upsert_statement(rows[i:i + UPSERT_CHUNK]))
        written += int(result.rowcount or 0)
    return written


async def download(client: httpx.AsyncClient, url: str) -> bytes:
    response = await client.get(url)
    response.raise_for_status()
    return response.content


def expected_report_date(today: date) -> date:
    """Tuesday of the latest report that should be published by `today`.

    Friday release covers that week's Tuesday; on Monday the expected report is
    still the previous Tuesday (covers holiday-delayed Monday releases).
    """
    anchor = today - timedelta(days=3)
    return anchor - timedelta(days=(anchor.weekday() - 1) % 7)


async def latest_report_date(session: AsyncSession) -> date | None:
    return (await session.execute(text("SELECT max(report_date) FROM cot_positions"))).scalar_one()


async def run_cot_weekly(*, retry: bool = False, today: date | None = None) -> dict[str, Any]:
    """job:cot_weekly — fetch the current-year TFF file and upsert it.

    The Monday retry does nothing when the expected report is already stored.
    """
    today = today or date.today()
    expected = expected_report_date(today)
    async with job_lock("cot_weekly") as acquired:
        async with run_logger("job:cot_weekly") as run:
            if not acquired:
                run.mark_skipped()
                return {"status": "skipped"}
            if retry:
                async with session_scope() as session:
                    latest = await latest_report_date(session)
                if latest is not None and latest >= expected:
                    run.mark_skipped()
                    return {"status": "skipped", "expected": expected, "latest": latest}

            async def _work() -> dict[str, Any]:
                config = load_cot_config()
                codes = contracts_by_code(config)
                rows: list[dict[str, Any]] = []
                async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                    for year in sorted({expected.year, today.year}):
                        content = await download(client, config["yearly_url"].format(year=year))
                        rows += parse_tff_zip(content, codes)
                async with session_scope(statement_timeout="5min") as session:
                    written = await upsert_cot_positions(session, rows)
                    latest = await latest_report_date(session)
                return {"rows_seen": len(rows), "written": written, "expected": expected, "latest": latest}

            result = await asyncio.wait_for(_work(), timeout=10 * 60)
            run.record_rows(result["written"])
            if result["latest"] is None or result["latest"] < expected:
                # Holiday-delayed release: record it so the Monday retry runs.
                run.errors.append(f"expected report_date {expected} not yet published")
            return result


def _int(value: Any) -> int:
    cleaned = str(value or "").replace(",", "").strip()
    if not cleaned or cleaned == ".":
        return 0
    return int(float(cleaned))
