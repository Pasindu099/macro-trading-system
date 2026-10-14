"""Policy decision statements for all 8 banks into cb_policy_documents (Step 12 Part C; no LLM).

Stored as doc_type='policy_statement' (raw_text only; no analysis), so the older
PDF/LLM document ingester, which rewrites doc_type='statement', never overwrites them.
Statement dates are the banks' decision dates from the stored rate-decision history.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import text

from app.db.session import session_scope
from app.ingestion.run_logger import run_logger

logger = logging.getLogger(__name__)

JOB_NAME = "job:cb_statements"
DOC_TYPE = "policy_statement"
BANKS = ("FED", "ECB", "BOE", "BOJ", "RBA", "BOC", "RBNZ", "SNB")
LOOKBACK_DAYS = 396                  # 13 months
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/126.0 Safari/537.36", "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
BLOCKED = {"RBA": "rba.gov.au returns HTTP 403 to scripted clients"}
SELECTORS = {"FED": "#article", "ECB": "main .section", "BOC": "article, main", "SNB": "main",
             "BOE": "main, #main-content", "BOJ": "#contents, main"}
ECB_END = "We are now ready to take your questions"
BOE_START, BOE_END = "Monetary Policy Summary", "Minutes of the Monetary Policy Committee"


def clean_text(raw: str) -> str:
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in raw.splitlines()]
    return "\n".join(line for line in lines if len(line) > 2)


def html_text(html: str, bank: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "form", "noscript", "aside"]):
        tag.decompose()
    if bank == "RBNZ":                                    # article text sits in rich-text blocks
        body = clean_text("\n".join(node.get_text("\n") for node in soup.select(".rich-text")))
    else:
        node = soup.select_one(SELECTORS.get(bank, "main")) or soup.body or soup
        body = clean_text(node.get_text("\n"))
    if bank == "BOE":
        start, end = body.find(BOE_START), body.find(BOE_END)
        if start >= 0:
            body = body[start:end if end > start else None]
    if bank == "ECB" and ECB_END in body:                 # drop the press-conference Q&A transcript
        body = body[:body.find(ECB_END) + len(ECB_END)]
    return body


def pdf_text(content: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(content))
    return clean_text("\n".join(page.extract_text() or "" for page in reader.pages[:10]))


# ── Discovery: (decision date, url, kind) ─────────────────────────────

async def decision_dates(bank: str, since: date) -> list[date]:
    """Stored decision dates plus past meetings in the verified config (the history has gaps)."""
    import yaml
    from app.services.cb_projection_loaders import decision_dates as all_dates
    config = yaml.safe_load(Path("config/cb_meetings.yaml").read_text(encoding="utf-8")).get(bank, {})
    configured = {date.fromisoformat(str(m["dt"])[:10]) for m in config.get("meetings", []) if m.get("official", True)}
    return sorted(d for d in set(await all_dates(bank)) | configured if since <= d <= date.today())


async def discover(client: httpx.AsyncClient, bank: str, since: date) -> list[tuple[date, str, str]]:
    if bank == "ECB":
        found = []
        for year in range(since.year, date.today().year + 1):
            url = f"https://www.ecb.europa.eu/press/press_conference/monetary-policy-statement/{year}/html/index_include.en.html"
            page = await client.get(url)
            for href, ymd in set(re.findall(r'href="(/press/press_conference/monetary-policy-statement/\d{4}/html/'
                                            r'ecb\.is(\d{6})~[0-9a-f]+\.en\.html)"', page.text)):
                day = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:]))
                if day >= since:
                    found.append((day, "https://www.ecb.europa.eu" + href, "html"))
        return sorted(found)
    days = await decision_dates(bank, since)
    if bank == "FED":
        return [(d, f"https://www.federalreserve.gov/newsevents/pressreleases/monetary{d:%Y%m%d}a.htm", "html") for d in days]
    if bank == "BOE":
        return [(d, f"https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/{d.year}/"
                    f"{MONTHS[d.month - 1]}-{d.year}", "html") for d in days]
    if bank == "BOC":
        return [(d, f"https://www.bankofcanada.ca/{d:%Y/%m}/fad-press-release-{d:%Y-%m-%d}/", "html") for d in days]
    if bank == "SNB":
        return [(d, f"https://www.snb.ch/en/publications/communication/press-releases-restricted/pre_{d:%Y%m%d}", "html")
                for d in days]
    if bank == "BOJ":
        found = []
        for year in range(since.year, date.today().year + 1):
            index = (await client.get(f"https://www.boj.or.jp/en/mopo/mpmdeci/mpr_{year}/index.htm")).text
            by_day: dict[date, tuple[str, str]] = {}
            # HTML statements (state_YYYY/…a.htm) are preferred over the PDF (mpr_YYYY/…a.pdf).
            for href, ymd, ext in re.findall(r'href="(/en/mopo/mpmdeci/(?:state|mpr)_\d{4}/k(\d{6})a\.(htm|pdf))"', index):
                day = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:]))
                if day in days and (day not in by_day or ext == "htm"):
                    by_day[day] = ("https://www.boj.or.jp" + href, "html" if ext == "htm" else "pdf")
            found += [(day, url, kind) for day, (url, kind) in by_day.items()]
        return sorted(found)
    if bank == "RBNZ":
        sitemap = await client.get("https://www.rbnz.govt.nz/sitemap.xml")
        sitemap.raise_for_status()
        return rbnz_links(sitemap.text, days)
    return []


def rbnz_links(sitemap: str, days: list[date]) -> list[tuple[date, str, str]]:
    """OCR decision media releases: /news-and-events/news/YYYY/MM/ocr-… matched to that month's decision."""
    found = {}
    for url, year, month in re.findall(r"<loc>(https://www\.rbnz\.govt\.nz/news-and-events/news/(\d{4})/(\d{2})/"
                                       r"(?:ocr|official-cash-rate)[^<]*)</loc>", sitemap):
        day = next((d for d in days if d.year == int(year) and d.month == int(month)), None)
        if day and day not in found:
            found[day] = url
    return sorted((d, u, "html") for d, u in found.items())


# ── Store ─────────────────────────────────────────────────────────────

async def store(bank: str, day: date, url: str, body: str) -> bool:
    """Upsert one statement; returns True when the text is new or changed."""
    async with session_scope() as session:
        result = await session.execute(text("""
            INSERT INTO cb_policy_documents (bank, doc_type, doc_date, filename, file_path, raw_text)
            VALUES (:b, :t, :d, :f, :u, :x)
            ON CONFLICT (bank, doc_date, doc_type) DO UPDATE
               SET raw_text = EXCLUDED.raw_text, file_path = EXCLUDED.file_path, filename = EXCLUDED.filename
             WHERE cb_policy_documents.raw_text IS DISTINCT FROM EXCLUDED.raw_text
        """), {"b": bank, "t": DOC_TYPE, "d": day, "f": url.rstrip("/").rsplit("/", 1)[-1], "u": url, "x": body})
        return bool(result.rowcount)


async def ingest_statements(banks: tuple[str, ...] = BANKS, since: date | None = None,
                            local_dir: Path | None = None) -> dict[str, Any]:
    """Fetch and store statements; local_dir/<BANK>/<YYYY-MM-DD>.html is used instead of fetching when present."""
    since = since or date.today() - timedelta(days=LOOKBACK_DAYS)
    result: dict[str, Any] = {}
    async with run_logger(JOB_NAME) as run, httpx.AsyncClient(timeout=60, follow_redirects=True,
                                                                headers=HEADERS) as client:
        for bank in banks:
            summary: dict[str, Any] = {"listed": 0, "stored": 0, "unchanged": 0, "failed": {}}
            if bank in BLOCKED:
                result[bank] = {"status": "unavailable", "reason": BLOCKED[bank]}
                continue
            local = local_dir / bank if local_dir else None
            try:
                if local and local.exists():
                    days = await decision_dates(bank, since)
                    links = [(d, f"file:{local / f'{d}.html'}", "file") for d in days if (local / f"{d}.html").exists()]
                else:
                    links = await discover(client, bank, since)
            except httpx.HTTPError as exc:
                summary["failed"]["discovery"] = str(exc)
                run.errors.append(f"{bank}: {exc}")
                links = []
            for day, url, kind in links:
                summary["listed"] += 1
                try:
                    if kind == "file":
                        body = html_text(Path(url[5:]).read_text(encoding="utf-8", errors="ignore"), bank)
                    else:
                        response = await client.get(url)
                        response.raise_for_status()
                        body = pdf_text(response.content) if kind == "pdf" else html_text(response.text, bank)
                    if len(body) < 300:
                        raise ValueError(f"statement text too short ({len(body)} chars)")
                except (httpx.HTTPError, ValueError, OSError) as exc:
                    summary["failed"][str(day)] = str(exc)[:160]
                    continue
                if await store(bank, day, url, body):
                    summary["stored"] += 1
                else:
                    summary["unchanged"] += 1
            run.record_rows(summary["stored"])
            result[bank] = summary
    return result
