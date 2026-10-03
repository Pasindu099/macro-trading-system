"""Download FOMC statement and minutes PDFs into data/policy/FED for the document ingester.

File names follow the Fed's own (and the existing local files'):
``Statement/monetaryYYYYMMDDa1.pdf`` and ``Minutes/fomcminutesYYYYMMDD.pdf``,
where the date is the meeting date. Existing files are never re-downloaded.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

import httpx

from app.processing.cb_document_ingester import POLICY_DIR

logger = logging.getLogger(__name__)

FED_BASE = "https://www.federalreserve.gov"
CALENDAR_URL = f"{FED_BASE}/monetarypolicy/fomccalendars.htm"
HEADERS = {"User-Agent": "MacroDashboard/0.1 cb-documents"}


def fed_document_links(calendar_html: str, since: date) -> list[tuple[str, date, str]]:
    """(kind, meeting_date, pdf_url) for statements and minutes on or after `since`."""
    links: dict[tuple[str, date], str] = {}
    patterns = (
        ("statement", r"/monetarypolicy/files/monetary(\d{8})a1\.pdf", "/monetarypolicy/files/monetary{d}a1.pdf"),
        ("minutes", r"/monetarypolicy/fomcminutes(\d{8})\.htm", "/monetarypolicy/files/fomcminutes{d}.pdf"),
    )
    for kind, pattern, pdf in patterns:
        for d in set(re.findall(pattern, calendar_html)):
            day = date(int(d[:4]), int(d[4:6]), int(d[6:]))
            if day >= since:
                links[(kind, day)] = FED_BASE + pdf.format(d=d)
    return sorted((kind, day, url) for (kind, day), url in links.items())


def target_path(kind: str, day: date, root: Path = POLICY_DIR) -> Path:
    stamp = day.strftime("%Y%m%d")
    if kind == "statement":
        return root / "FED" / "Statement" / f"monetary{stamp}a1.pdf"
    return root / "FED" / "Minutes" / f"fomcminutes{stamp}.pdf"


async def fetch_fed_documents(since: date, root: Path = POLICY_DIR) -> dict[str, int]:
    counts = {"listed": 0, "downloaded": 0, "present": 0, "failed": 0}
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True, headers=HEADERS) as client:
        calendar = await client.get(CALENDAR_URL)
        calendar.raise_for_status()
        for kind, day, url in fed_document_links(calendar.text, since):
            counts["listed"] += 1
            path = target_path(kind, day, root)
            if path.exists():
                counts["present"] += 1
                continue
            try:
                response = await client.get(url)
                response.raise_for_status()
                if not response.content.startswith(b"%PDF"):
                    raise ValueError("not a PDF")
            except Exception as exc:  # noqa: BLE001 - one missing document must not stop the rest
                logger.warning("Fed %s %s download failed: %s", kind, day, exc)
                counts["failed"] += 1
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.content)
            counts["downloaded"] += 1
    return counts
