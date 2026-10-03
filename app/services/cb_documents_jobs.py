"""job:cb_documents — CB policy documents on decision and minutes-release days.

Run times come from config/cb_meetings.yaml: each meeting's statement time + 30 min,
and meeting + ``minutes_offset_days`` (same clock time) + 30 min for minutes/accounts.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from app.db.session import session_scope
from app.ingestion.run_logger import run_logger
from app.services.job_lock import job_lock

MEETINGS_CONFIG = Path("config/cb_meetings.yaml")
DOCUMENT_BANKS = ("FED", "ECB")
# Minutes keep the decision's local clock time; adding days to a fixed UTC offset would drift at DST changes.
BANK_TZ = {"FED": ZoneInfo("America/New_York"), "ECB": ZoneInfo("Europe/Berlin")}
RELEASE_DELAY = timedelta(minutes=30)
LOOKBACK_MONTHS = 13


def document_run_times(config: dict[str, Any], banks=DOCUMENT_BANKS, *, after: datetime) -> list[tuple[str, str, datetime]]:
    """(bank, kind, run_at) for every statement and minutes release after `after`."""
    runs = []
    for bank in banks:
        bank_cfg = config.get(bank, {})
        offset = bank_cfg.get("minutes_offset_days")
        for meeting in bank_cfg.get("meetings", []):
            dt = datetime.fromisoformat(str(meeting["dt"]))
            candidates = [("statement", dt + RELEASE_DELAY)]
            if offset:
                tz = BANK_TZ.get(bank)
                local = dt.astimezone(tz).replace(tzinfo=None) if tz else dt.replace(tzinfo=None)
                minutes_at = (local + timedelta(days=int(offset))).replace(tzinfo=tz or dt.tzinfo)
                candidates.append(("minutes", minutes_at + RELEASE_DELAY))
            runs += [(bank, kind, at) for kind, at in candidates if at > after]
    return sorted(runs, key=lambda r: r[2])


def load_meetings_config() -> dict[str, Any]:
    return yaml.safe_load(MEETINGS_CONFIG.read_text(encoding="utf-8"))


async def run_cb_documents(bank: str, kind: str = "manual", *, lookback_months: int = LOOKBACK_MONTHS) -> dict[str, Any]:
    """Scrape + analyse statements, fetch Fed PDFs, then ingest local PDFs for one bank."""
    from app.processing.cb_document_ingester import ingest_documents
    from app.processing.cb_fed_documents import fetch_fed_documents
    from app.processing.cb_policy_analyzer import run_full_pipeline
    from app.processing.cb_policy_scraper import scrape_bank

    async with job_lock("cb_documents") as acquired:
        async with run_logger("job:cb_documents") as run:
            if not acquired:
                run.mark_skipped()
                return {"status": "skipped"}

            async def _work() -> dict[str, Any]:
                since = date.today() - timedelta(days=round(lookback_months * 30.44))
                result: dict[str, Any] = {"bank": bank, "kind": kind}
                if bank == "FED":
                    result["fed_pdfs"] = await fetch_fed_documents(since)
                records = await scrape_bank(bank, lookback_months=lookback_months)
                async with session_scope() as session:
                    result["reports"] = await run_full_pipeline(session, records)
                async with session_scope() as session:
                    result["documents"] = await ingest_documents(session, bank=bank)
                return result

            result = await asyncio.wait_for(_work(), timeout=30 * 60)
            run.record_rows(result["documents"].get("new", 0))
            errors = result["documents"].get("errors", 0)
            if errors:
                run.errors.append(f"{bank}: {errors} documents not analysed (see logs; e.g. OpenAI credits)")
            return result
