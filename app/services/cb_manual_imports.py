"""Manual imports for blocked / PDF-only central-bank sources (Step 12 decision).

Uploaded official files go through the same parsers as the loaders; manually
entered projection values go through the same bounds and horizon rules.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from sqlalchemy import text

from app.db.session import get_sessionmaker, session_scope
from app.ingestion.run_logger import run_logger
from app.processing import cb_projection_parsers as parsers
from app.services.cb_summary import bank_config

KINDS = ("projections", "statements", "speeches")
HORIZON = re.compile(r"^(20\d\d(Q[1-4])?|constant)$")
JOB_NAME = "job:cb_manual_import"


def manual_sources(bank: str) -> dict[str, str]:
    """{kind: reason} for sources that are filled by manual import."""
    return dict(bank_config()[bank].get("manual_import") or {})


def is_manual(bank: str, kind: str) -> bool:
    return kind in manual_sources(bank)


async def mark_manual_run(job_name: str, bank: str, kind: str) -> dict[str, Any]:
    """Log a scheduled run for a blocked source as status manual_import (not an error)."""
    reason = manual_sources(bank)[kind]
    async with run_logger(f"{job_name}:{bank}") as run:
        run.mark_manual_import(f"{bank} {kind}: manual import ({reason})")
    return {"status": "manual_import", "reason": reason, "last_import": await last_import(bank, kind)}


async def last_import(bank: str, kind: str) -> date | None:
    async with get_sessionmaker()() as session:
        stamp = (await session.execute(text("""SELECT max(imported_at) FROM cb_manual_imports
            WHERE bank = :b AND kind = :k"""), {"b": bank, "k": kind})).scalar_one_or_none()
    return stamp.date() if stamp else None


async def _record(bank: str, kind: str, method: str, source: str | None, round_date: date | None,
                  rows: int, user: str | None) -> None:
    async with session_scope() as session:
        await session.execute(text("""INSERT INTO cb_manual_imports
            (bank, kind, method, source, round_date, rows, imported_by)
            VALUES (:b, :k, :m, :s, :d, :n, :u)"""),
                              {"b": bank, "k": kind, "m": method, "s": source, "d": round_date, "n": rows, "u": user})
    async with run_logger(JOB_NAME) as run:
        run.record_rows(rows)


# ── Projections ───────────────────────────────────────────────────────

def parse_projection_file(bank: str, filename: str, content: bytes, release: date) -> list[dict[str, Any]]:
    """Run the bank's existing parser on an uploaded official file."""
    from app.processing.xlsx_reader import read_sheet, sheet_names
    name = filename.lower()
    if bank == "RBNZ" and name.endswith(".xlsx"):
        sheet = next((s for s in sheet_names(content) if s.strip() in ("Projections", "Baseline scenario")), None)
        if sheet is None:
            raise ValueError("Workbook has no Projections/Baseline scenario sheet")
        return parsers.parse_rbnz_projections(read_sheet(content, sheet), release)
    if bank == "BOE" and name.endswith((".html", ".htm")):
        return parsers.parse_boe_mpr(content.decode("utf-8", "ignore"), release)
    if bank == "BOC" and name.endswith((".html", ".htm")):
        return parsers.parse_boc_mpr(content.decode("utf-8", "ignore"), release)
    raise ValueError(f"No file parser for {bank} {filename}; enter the values manually")


def validate_values(bank: str, release: date, entries: list[tuple[str, str, float]]) -> list[dict[str, Any]]:
    """Same rules as the loaders: known variable, year/quarter horizon, plausible bounds."""
    rows = []
    for variable, horizon, value in entries:
        variable, horizon = variable.strip(), horizon.strip().upper().replace("CONSTANT", "constant")
        if variable not in parsers.BOUNDS:
            raise ValueError(f"Unknown variable {variable!r}; use one of {sorted(parsers.BOUNDS)}")
        if not HORIZON.match(horizon):
            raise ValueError(f"Horizon {horizon!r} must be YYYY or YYYYQn")
        stat = "assumption" if horizon == "constant" else "point"
        rows.append(parsers._row(bank, release, variable, horizon, float(value), stat=stat))
    if not rows:
        raise ValueError("No values entered")
    return rows


def parse_value_lines(raw: str) -> list[tuple[str, str, float]]:
    """'variable, horizon, value' per line."""
    entries = []
    for number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            raise ValueError(f"Line {number}: expected 'variable, horizon, value'")
        try:
            entries.append((parts[0], parts[1], float(parts[2])))
        except ValueError as exc:
            raise ValueError(f"Line {number}: value {parts[2]!r} is not a number") from exc
    return entries


async def write_projection_rows(rows: list[dict[str, Any]], replace_round: bool) -> int:
    bank, release = rows[0]["bank"], rows[0]["release_date"]
    async with session_scope() as session:
        if replace_round:
            await session.execute(text("DELETE FROM cb_projection_values WHERE bank = :b AND release_date = :d"),
                                  {"b": bank, "d": release})
        else:
            for row in rows:
                await session.execute(text("""DELETE FROM cb_projection_values WHERE bank = :bank
                    AND release_date = :release_date AND variable = :variable AND horizon = :horizon AND stat = :stat"""),
                                      row)
        await session.execute(text("""INSERT INTO cb_projection_values (bank, release_date, variable, horizon, stat, value)
            VALUES (:bank, :release_date, :variable, :horizon, :stat, :value)"""), rows)
    return len(rows)


async def import_projection_file(bank: str, filename: str, content: bytes, release: date, user: str | None) -> int:
    rows = parse_projection_file(bank, filename, content, release)
    count = await write_projection_rows(rows, replace_round=True)
    await _record(bank, "projections", "file", filename, release, count, user)
    return count


async def import_projection_values(bank: str, release: date, raw: str, user: str | None) -> int:
    rows = validate_values(bank, release, parse_value_lines(raw))
    count = await write_projection_rows(rows, replace_round=False)
    await _record(bank, "projections", "manual_entry", None, release, count, user)
    return count


# ── Statements ────────────────────────────────────────────────────────

def statement_text(bank: str, filename: str, content: bytes) -> str:
    from app.processing.cb_statements import clean_text, html_text, pdf_text
    name = filename.lower()
    if name.endswith(".pdf"):
        body = pdf_text(content)
    elif name.endswith((".html", ".htm")):
        body = html_text(content.decode("utf-8", "ignore"), bank)
    else:
        body = clean_text(content.decode("utf-8", "ignore"))
    if len(body) < 300:
        raise ValueError(f"Statement text too short ({len(body)} chars)")
    return body


async def import_statement(bank: str, decision: date, filename: str, content: bytes, user: str | None) -> int:
    from app.processing.cb_statements import store
    body = statement_text(bank, filename, content)
    await store(bank, decision, f"manual:{filename}", body)
    await _record(bank, "statements", "file", filename, decision, 1, user)
    return 1


async def import_summary() -> list[dict[str, Any]]:
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text("""SELECT bank, kind, method, source, round_date, rows, imported_by,
            imported_at FROM cb_manual_imports ORDER BY imported_at DESC LIMIT 30"""))).mappings().all()
    return [dict(r) for r in rows]
