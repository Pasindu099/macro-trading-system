#!/usr/bin/env python3
"""Scraper for rateprobability.com — rate probability tables for G8 central banks.

Runnable standalone:
    python scraper/rate_probability_scraper.py

Or via APScheduler (called from app/ingestion/scheduler.py).

Tables written:
  rp_scraped_meetings  — per-meeting probabilities for each bank
  rp_scraped_summary   — current rate + next-meeting summary per bank
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from datetime import date, datetime
from typing import Any

import psycopg2
import psycopg2.extras
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────

BANK_URLS: dict[str, str] = {
    "FED":  "https://rateprobability.com/fed",
    "ECB":  "https://rateprobability.com/ecb",
    "BOJ":  "https://rateprobability.com/boj",
    "BOE":  "https://rateprobability.com/boe",
    "BOC":  "https://rateprobability.com/boc",
    "RBA":  "https://rateprobability.com/rba",
    "RBNZ": "https://rateprobability.com/rbnz",
    "SNB":  "https://rateprobability.com/snb",
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 5
INTER_BANK_DELAY = (2.0, 4.0)  # seconds, random between these

# ── Data classes ────────────────────────────────────────────────────────────

class ScrapedMeeting:
    __slots__ = (
        "meeting_date", "implied_rate", "cut_prob", "hold_prob",
        "hike_prob", "delta_bps", "cumulative_moves",
    )

    def __init__(
        self,
        meeting_date: date,
        implied_rate: float | None,
        cut_prob: float | None,
        hold_prob: float | None,
        hike_prob: float | None,
        delta_bps: float | None,
        cumulative_moves: float | None,
    ) -> None:
        self.meeting_date = meeting_date
        self.implied_rate = implied_rate
        self.cut_prob = cut_prob
        self.hold_prob = hold_prob
        self.hike_prob = hike_prob
        self.delta_bps = delta_bps
        self.cumulative_moves = cumulative_moves


class ScrapedBankData:
    __slots__ = ("bank", "current_rate", "bank_name", "meetings")

    def __init__(
        self,
        bank: str,
        current_rate: float | None,
        bank_name: str,
        meetings: list[ScrapedMeeting],
    ) -> None:
        self.bank = bank
        self.current_rate = current_rate
        self.bank_name = bank_name
        self.meetings = meetings


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _fetch_html(url: str) -> str:
    """Fetch a URL with retry logic and rotating user agents."""
    for attempt in range(1, MAX_RETRIES + 1):
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            logger.warning("Attempt %d/%d for %s failed: %s", attempt, MAX_RETRIES, url, exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
    raise RuntimeError(f"All {MAX_RETRIES} attempts failed for {url}")


# ── Parsing helpers ─────────────────────────────────────────────────────────

_DATE_PATTERNS = [
    r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b",
    r"\b(\w{3,9})\s+(\d{1,2}),?\s+(\d{4})\b",
    r"\b(\d{4})-(\d{2})-(\d{2})\b",
]
_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4,
    "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
}


def _parse_date(text: str) -> date | None:
    text = text.strip()
    # ISO format: 2026-07-29
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    # Month name: Jul 29, 2026 or July 29 2026
    m = re.search(r"(\w{3,9})\s+(\d{1,2}),?\s+(\d{4})", text, re.IGNORECASE)
    if m:
        month = _MONTH_MAP.get(m.group(1).lower())
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(2)))
            except ValueError:
                pass
    # Numeric: 07/29/2026 or 29/07/2026
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
    if m:
        a, b, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        for mo, da in ((a, b), (b, a)):
            if 1 <= mo <= 12 and 1 <= da <= 31:
                try:
                    return date(yr, mo, da)
                except ValueError:
                    pass
    return None


def _parse_pct(text: str) -> float | None:
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*%?", text.replace(",", "."))
    if m:
        return float(m.group(1))
    return None


def _parse_rate(text: str) -> float | None:
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*%?", text.replace(",", "."))
    if m:
        return float(m.group(1))
    return None


def _parse_bps(text: str) -> float | None:
    text = text.strip()
    m = re.search(r"([+-]?\d+(?:\.\d+)?)", text.replace(",", "."))
    if m:
        return float(m.group(1))
    return None


# ── Column identification ──────────────────────────────────────────────────

_COL_DATE   = ("date", "meeting", "meeting date", "mtg", "decision")
_COL_IMPL   = ("implied", "rate", "implied rate", "post-meeting rate", "post meeting")
_COL_CUT    = ("cut", "cut prob", "cut probability", "cut %")
_COL_HOLD   = ("hold", "hold prob", "hold probability", "hold %", "no change")
_COL_HIKE   = ("hike", "hike prob", "hike probability", "hike %", "raise")
_COL_DELTA  = ("delta", "bps", "delta bps", "change", "chg", "±bps", "+/-")
_COL_CUMUL  = ("cumulative", "cum", "cum moves", "total moves", "total")
_COL_MOVE   = ("move", "probability", "prob", "% change", "implied move")


def _identify_column(header: str) -> str | None:
    h = header.lower().strip()
    for kw in _COL_DATE:
        if kw in h:
            return "date"
    for kw in _COL_CUT:
        if kw in h:
            return "cut"
    for kw in _COL_HOLD:
        if kw in h:
            return "hold"
    for kw in _COL_HIKE:
        if kw in h:
            return "hike"
    for kw in _COL_DELTA:
        if kw in h:
            return "delta"
    for kw in _COL_CUMUL:
        if kw in h:
            return "cumulative"
    for kw in _COL_IMPL:
        if kw in h:
            return "implied"
    for kw in _COL_MOVE:
        if kw in h:
            return "move"
    return None


# ── Page parser ────────────────────────────────────────────────────────────

def _parse_current_rate(soup: BeautifulSoup) -> float | None:
    # Try common selectors for current rate display
    for selector in (
        "[class*='current-rate']",
        "[class*='current_rate']",
        "[class*='policy-rate']",
        "[class*='rate-value']",
        "[class*='rate_value']",
        "[data-rate]",
    ):
        el = soup.select_one(selector)
        if el:
            val = _parse_rate(el.get_text())
            if val is not None:
                return val

    # Look for text patterns like "Current Rate: 3.75%" near the top of the page
    text_blocks = soup.find_all(["p", "span", "div", "h1", "h2", "h3", "h4"], limit=60)
    for el in text_blocks:
        raw = el.get_text()
        if re.search(r"current.{1,20}rate", raw, re.IGNORECASE):
            val = _parse_rate(raw)
            if val is not None and 0.0 <= val <= 25.0:
                return val

    return None


def _parse_table(soup: BeautifulSoup, bank: str) -> list[ScrapedMeeting]:
    """Extract meeting rows from the page's probability table."""
    tables = soup.find_all("table")
    if not tables:
        logger.warning("[%s] No <table> elements found on page", bank)
        return []

    best_table = None
    best_score = -1

    for table in tables:
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if not headers:
            # Some tables use first row as headers
            first_row = table.find("tr")
            if first_row:
                headers = [td.get_text(strip=True) for td in first_row.find_all("td")]

        score = sum(
            1 for h in headers
            if any(kw in h.lower() for kw in ("date", "meeting", "implied", "prob", "cut", "hike", "hold", "bps"))
        )
        if score > best_score:
            best_score = score
            best_table = table

    if best_table is None or best_score < 2:
        logger.warning("[%s] Could not identify a suitable table (best score=%d)", bank, best_score)
        return []

    # Map column positions
    headers = [th.get_text(strip=True) for th in best_table.find_all("th")]
    if not headers:
        first_row = best_table.find("tr")
        if first_row:
            headers = [td.get_text(strip=True) for td in first_row.find_all("td")]

    col_map: dict[str, int] = {}
    for i, h in enumerate(headers):
        role = _identify_column(h)
        if role and role not in col_map:
            col_map[role] = i

    logger.debug("[%s] Column map: %s (headers=%s)", bank, col_map, headers)

    # Determine which rows are data rows (skip header row)
    rows = best_table.find_all("tr")
    has_thead = bool(best_table.find("thead"))
    data_rows = rows[1:] if not has_thead else rows

    meetings: list[ScrapedMeeting] = []
    today = date.today()

    for row in data_rows:
        cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
        if not cells:
            continue

        # Skip header-like rows
        if any(re.search(r"[a-zA-Z]{4,}", c) for c in cells[:2]) and "date" not in (cells[0] or "").lower():
            if not any(re.search(r"\d{4}", c) for c in cells):
                continue

        def get(role: str) -> str:
            idx = col_map.get(role)
            if idx is not None and idx < len(cells):
                return cells[idx]
            return ""

        meeting_date = _parse_date(get("date") or (cells[0] if cells else ""))
        if meeting_date is None or meeting_date < today:
            continue

        implied_rate = _parse_rate(get("implied")) if col_map.get("implied") is not None else None
        cut_prob     = _parse_pct(get("cut"))   if col_map.get("cut") is not None else None
        hold_prob    = _parse_pct(get("hold"))  if col_map.get("hold") is not None else None
        hike_prob    = _parse_pct(get("hike"))  if col_map.get("hike") is not None else None
        delta_bps    = _parse_bps(get("delta")) if col_map.get("delta") is not None else None
        cumul        = _parse_pct(get("cumulative")) if col_map.get("cumulative") is not None else None

        # Fallback: single move/probability column — treat as dominant direction
        if cut_prob is None and hold_prob is None and hike_prob is None:
            move_text = get("move")
            if move_text:
                val = _parse_pct(move_text)
                if val is not None:
                    if val < 0:
                        cut_prob = abs(val)
                    elif val > 0:
                        hike_prob = val
                    else:
                        hold_prob = 100.0

        meetings.append(ScrapedMeeting(
            meeting_date=meeting_date,
            implied_rate=implied_rate,
            cut_prob=cut_prob,
            hold_prob=hold_prob,
            hike_prob=hike_prob,
            delta_bps=delta_bps,
            cumulative_moves=cumul,
        ))

    logger.info("[%s] Parsed %d upcoming meetings", bank, len(meetings))
    return meetings


def scrape_bank(bank: str) -> ScrapedBankData | None:
    """Scrape one bank's rate probability page. Returns None on failure."""
    url = BANK_URLS.get(bank)
    if not url:
        logger.error("Unknown bank: %s", bank)
        return None

    try:
        html = _fetch_html(url)
    except RuntimeError as exc:
        logger.error("[%s] Fetch failed: %s", bank, exc)
        return None

    soup = BeautifulSoup(html, "html.parser")
    current_rate = _parse_current_rate(soup)
    meetings = _parse_table(soup, bank)

    # Infer bank name from title or h1
    bank_name = bank
    for el in soup.find_all(["title", "h1", "h2"], limit=5):
        t = el.get_text(strip=True)
        if len(t) > 3:
            bank_name = t[:80]
            break

    return ScrapedBankData(
        bank=bank,
        current_rate=current_rate,
        bank_name=bank_name,
        meetings=meetings,
    )


# ── Database ────────────────────────────────────────────────────────────────

def _sync_db_url() -> str:
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return raw.replace("postgresql+asyncpg://", "postgresql://")


def _get_conn() -> "psycopg2.extensions.connection":
    return psycopg2.connect(_sync_db_url())


def _upsert_bank(conn: "psycopg2.extensions.connection", data: ScrapedBankData) -> None:
    now = datetime.utcnow()
    next_meeting: ScrapedMeeting | None = data.meetings[0] if data.meetings else None

    with conn.cursor() as cur:
        # Upsert summary row
        cur.execute(
            """
            INSERT INTO rp_scraped_summary
                (bank, current_rate, next_meeting_date,
                 next_cut_prob, next_hold_prob, next_hike_prob, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (bank) DO UPDATE SET
                current_rate      = EXCLUDED.current_rate,
                next_meeting_date = EXCLUDED.next_meeting_date,
                next_cut_prob     = EXCLUDED.next_cut_prob,
                next_hold_prob    = EXCLUDED.next_hold_prob,
                next_hike_prob    = EXCLUDED.next_hike_prob,
                updated_at        = EXCLUDED.updated_at
            """,
            (
                data.bank,
                data.current_rate,
                next_meeting.meeting_date if next_meeting else None,
                next_meeting.cut_prob     if next_meeting else None,
                next_meeting.hold_prob    if next_meeting else None,
                next_meeting.hike_prob    if next_meeting else None,
                now,
            ),
        )

        # Delete stale meeting rows for this bank so future meetings not on
        # the page (past meetings) don't persist forever.
        cur.execute(
            "DELETE FROM rp_scraped_meetings WHERE bank = %s AND meeting_date >= %s",
            (data.bank, date.today()),
        )

        # Insert fresh meeting rows
        for m in data.meetings:
            cur.execute(
                """
                INSERT INTO rp_scraped_meetings
                    (bank, meeting_date, implied_rate,
                     cut_prob, hold_prob, hike_prob,
                     delta_bps, cumulative_moves, scraped_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (bank, meeting_date) DO UPDATE SET
                    implied_rate     = EXCLUDED.implied_rate,
                    cut_prob         = EXCLUDED.cut_prob,
                    hold_prob        = EXCLUDED.hold_prob,
                    hike_prob        = EXCLUDED.hike_prob,
                    delta_bps        = EXCLUDED.delta_bps,
                    cumulative_moves = EXCLUDED.cumulative_moves,
                    scraped_at       = EXCLUDED.scraped_at
                """,
                (
                    data.bank,
                    m.meeting_date,
                    m.implied_rate,
                    m.cut_prob,
                    m.hold_prob,
                    m.hike_prob,
                    m.delta_bps,
                    m.cumulative_moves,
                    now,
                ),
            )

    conn.commit()
    logger.info("[%s] Saved %d meetings to DB", data.bank, len(data.meetings))


# ── Main scrape loop ────────────────────────────────────────────────────────

def run_scraper(banks: list[str] | None = None) -> dict[str, str]:
    """Scrape all (or selected) banks and write to DB. Returns {bank: status}."""
    target_banks = banks or list(BANK_URLS.keys())
    statuses: dict[str, str] = {}

    try:
        conn = _get_conn()
    except Exception as exc:
        logger.error("DB connection failed: %s", exc)
        return {b: "db_error" for b in target_banks}

    try:
        for i, bank in enumerate(target_banks):
            if i > 0:
                delay = random.uniform(*INTER_BANK_DELAY)
                logger.debug("Sleeping %.1fs before %s", delay, bank)
                time.sleep(delay)

            try:
                data = scrape_bank(bank)
                if data is None:
                    statuses[bank] = "fetch_failed"
                    continue

                _upsert_bank(conn, data)
                statuses[bank] = "ok"

            except Exception as exc:
                logger.error("[%s] Unexpected error: %s", bank, exc, exc_info=True)
                try:
                    conn.rollback()
                except Exception:
                    pass
                statuses[bank] = "error"
    finally:
        conn.close()

    return statuses


async def run_scraper_async(banks: list[str] | None = None) -> dict[str, str]:
    """Async wrapper for use from APScheduler / FastAPI background tasks."""
    return await asyncio.to_thread(run_scraper, banks)


# ── CLI entry point ─────────────────────────────────────────────────────────

def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    _setup_logging()

    # Load .env if present (project root)
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    parser = argparse.ArgumentParser(description="Scrape rateprobability.com for G8 banks")
    parser.add_argument("banks", nargs="*", help="Bank codes to scrape (default: all)")
    args = parser.parse_args()

    statuses = run_scraper(args.banks or None)
    for bank, status in statuses.items():
        symbol = "✓" if status == "ok" else "✗"
        print(f"  {symbol} {bank}: {status}")
