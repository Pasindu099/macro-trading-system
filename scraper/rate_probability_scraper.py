#!/usr/bin/env python3
"""Scraper for rateprobability.com — rate probability JSON API for G8 central banks.

Strategy: fetch the HTML page first (sets Cloudflare session cookies), then hit
the bank's JSON API endpoint with those cookies.

Available banks (6 free + 2 Pro):
  Free: FED, ECB, BOJ, BOE, BOC, RBA
  Pro:  RBNZ, SNB (stored as no-data; dashboard shows them grayed out)

Runnable standalone:
    python scraper/rate_probability_scraper.py [FED ECB ...]

Or called from APScheduler via run_scraper_async().
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from datetime import date, datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────

BANK_PAGE_URLS: dict[str, str] = {
    "FED":  "https://rateprobability.com/fed",
    "ECB":  "https://rateprobability.com/ecb",
    "BOJ":  "https://rateprobability.com/boj",
    "BOE":  "https://rateprobability.com/boe",
    "BOC":  "https://rateprobability.com/boc",
    "RBA":  "https://rateprobability.com/rba",
    "RBNZ": "https://rateprobability.com/rbnz",
    "SNB":  "https://rateprobability.com/snb",
}

# FED uses the root /api/latest; other banks use /api/{slug}/latest
BANK_API_URLS: dict[str, str] = {
    "FED":  "https://rateprobability.com/api/latest",
    "ECB":  "https://rateprobability.com/api/ecb/latest",
    "BOJ":  "https://rateprobability.com/api/boj/latest",
    "BOE":  "https://rateprobability.com/api/boe/latest",
    "BOC":  "https://rateprobability.com/api/boc/latest",
    "RBA":  "https://rateprobability.com/api/rba/latest",
    "RBNZ": "https://rateprobability.com/api/rbnz/latest",  # Pro only
    "SNB":  "https://rateprobability.com/api/snb/latest",   # Pro only
}

# Field name for the current policy rate in each bank's API response
CURRENT_RATE_FIELD: dict[str, str] = {
    "FED":  "midpoint",
    "ECB":  "ecb_deposit_facility",
    "BOJ":  "current_target",
    "BOE":  "current_target",
    "BOC":  "Overnight Rate Target",
    "RBA":  "cash_rate_target",
    "RBNZ": "current_target",
    "SNB":  "current_target",
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 5
INTER_BANK_DELAY = (2.5, 4.5)


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
    __slots__ = ("bank", "current_rate", "meetings")

    def __init__(self, bank: str, current_rate: float | None, meetings: list[ScrapedMeeting]) -> None:
        self.bank = bank
        self.current_rate = current_rate
        self.meetings = meetings


# ── HTTP fetch ──────────────────────────────────────────────────────────────

def _ua() -> str:
    return random.choice(USER_AGENTS)


def _fetch_bank(bank: str) -> ScrapedBankData | None:
    """
    Fetch one bank: load the HTML page first (sets CF cookies), then hit the API.
    Returns None on unrecoverable failure; returns data with empty meetings on Pro-only.
    """
    page_url = BANK_PAGE_URLS[bank]
    api_url  = BANK_API_URLS[bank]
    ua = _ua()
    session = requests.Session()

    # Step 1: load the HTML page to get Cloudflare session cookies
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            session.get(page_url, headers={"User-Agent": ua}, timeout=REQUEST_TIMEOUT)
            break
        except requests.RequestException as exc:
            logger.warning("[%s] HTML fetch attempt %d/%d failed: %s", bank, attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
    else:
        logger.error("[%s] All HTML fetch attempts failed", bank)
        return None

    # Step 2: hit the JSON API with the session
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(
                api_url,
                headers={"User-Agent": ua, "Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 401:
                logger.info("[%s] Pro subscription required — skipping", bank)
                return ScrapedBankData(bank=bank, current_rate=None, meetings=[])
            resp.raise_for_status()
            data = resp.json()
            return _parse_api_response(bank, data)
        except requests.RequestException as exc:
            logger.warning("[%s] API fetch attempt %d/%d failed: %s", bank, attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    logger.error("[%s] All API fetch attempts failed", bank)
    return None


# ── API response parser ─────────────────────────────────────────────────────

def _parse_api_response(bank: str, data: dict[str, Any]) -> ScrapedBankData:
    today_block = data.get("today", data)  # some responses have "today" wrapper, some don't
    today = date.today()

    # Current rate
    rate_field = CURRENT_RATE_FIELD.get(bank, "current_target")
    current_rate: float | None = None
    raw_rate = today_block.get(rate_field)
    if raw_rate is not None:
        try:
            current_rate = float(raw_rate)
        except (TypeError, ValueError):
            pass

    # Meeting rows
    rows = today_block.get("rows", [])
    meetings: list[ScrapedMeeting] = []

    for row in rows:
        iso = row.get("meeting_iso") or row.get("meeting")
        if not iso:
            continue
        try:
            meeting_date = date.fromisoformat(str(iso)[:10])
        except ValueError:
            continue
        if meeting_date < today:
            continue

        implied_rate = _safe_float(row.get("implied_rate_post_meeting"))
        prob_move    = _safe_float(row.get("prob_move_pct"))
        prob_is_cut  = bool(row.get("prob_is_cut", False))
        delta_bps    = _safe_float(row.get("change_bps"))
        num_moves    = _safe_float(row.get("num_moves"))

        # Derive cut/hold/hike probabilities from the single prob_move_pct + direction flag.
        # rateprobability.com shows the dominant move probability; hold = remainder.
        if prob_move is not None:
            cut_prob  = prob_move if prob_is_cut  else 0.0
            hike_prob = prob_move if not prob_is_cut else 0.0
            hold_prob = max(0.0, 100.0 - prob_move)
        else:
            cut_prob = hold_prob = hike_prob = None

        meetings.append(ScrapedMeeting(
            meeting_date=meeting_date,
            implied_rate=implied_rate,
            cut_prob=cut_prob,
            hold_prob=hold_prob,
            hike_prob=hike_prob,
            delta_bps=delta_bps,
            cumulative_moves=num_moves,
        ))

    logger.info("[%s] current_rate=%.3f meetings=%d", bank, current_rate or 0.0, len(meetings))
    return ScrapedBankData(bank=bank, current_rate=current_rate, meetings=meetings)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── Async DB write (uses app's existing SQLAlchemy session) ─────────────────

async def _upsert_bank_async(session: Any, data: ScrapedBankData) -> None:
    from sqlalchemy import text as sa_text

    now = datetime.utcnow()
    next_meeting = data.meetings[0] if data.meetings else None

    await session.execute(
        sa_text("""
            INSERT INTO rp_scraped_summary
                (bank, current_rate, next_meeting_date,
                 next_cut_prob, next_hold_prob, next_hike_prob, updated_at)
            VALUES (:bank, :current_rate, :next_meeting_date,
                    :next_cut_prob, :next_hold_prob, :next_hike_prob, :updated_at)
            ON CONFLICT (bank) DO UPDATE SET
                current_rate      = EXCLUDED.current_rate,
                next_meeting_date = EXCLUDED.next_meeting_date,
                next_cut_prob     = EXCLUDED.next_cut_prob,
                next_hold_prob    = EXCLUDED.next_hold_prob,
                next_hike_prob    = EXCLUDED.next_hike_prob,
                updated_at        = EXCLUDED.updated_at
        """),
        {
            "bank":              data.bank,
            "current_rate":      data.current_rate,
            "next_meeting_date": next_meeting.meeting_date if next_meeting else None,
            "next_cut_prob":     next_meeting.cut_prob     if next_meeting else None,
            "next_hold_prob":    next_meeting.hold_prob    if next_meeting else None,
            "next_hike_prob":    next_meeting.hike_prob    if next_meeting else None,
            "updated_at":        now,
        },
    )

    await session.execute(
        sa_text("DELETE FROM rp_scraped_meetings WHERE bank = :bank AND meeting_date >= :today"),
        {"bank": data.bank, "today": date.today()},
    )

    for m in data.meetings:
        await session.execute(
            sa_text("""
                INSERT INTO rp_scraped_meetings
                    (bank, meeting_date, implied_rate,
                     cut_prob, hold_prob, hike_prob,
                     delta_bps, cumulative_moves, scraped_at)
                VALUES (:bank, :meeting_date, :implied_rate,
                        :cut_prob, :hold_prob, :hike_prob,
                        :delta_bps, :cumulative_moves, :scraped_at)
                ON CONFLICT (bank, meeting_date) DO UPDATE SET
                    implied_rate     = EXCLUDED.implied_rate,
                    cut_prob         = EXCLUDED.cut_prob,
                    hold_prob        = EXCLUDED.hold_prob,
                    hike_prob        = EXCLUDED.hike_prob,
                    delta_bps        = EXCLUDED.delta_bps,
                    cumulative_moves = EXCLUDED.cumulative_moves,
                    scraped_at       = EXCLUDED.scraped_at
            """),
            {
                "bank":             data.bank,
                "meeting_date":     m.meeting_date,
                "implied_rate":     m.implied_rate,
                "cut_prob":         m.cut_prob,
                "hold_prob":        m.hold_prob,
                "hike_prob":        m.hike_prob,
                "delta_bps":        m.delta_bps,
                "cumulative_moves": m.cumulative_moves,
                "scraped_at":       now,
            },
        )

    logger.info("[%s] DB upsert complete (%d meetings)", data.bank, len(data.meetings))


# ── Main entry points ───────────────────────────────────────────────────────

async def run_scraper_async(banks: list[str] | None = None) -> dict[str, str]:
    """Scrape all (or selected) banks and write to DB via async SQLAlchemy."""
    from app.db.session import session_scope

    target_banks = banks or list(BANK_PAGE_URLS.keys())
    statuses: dict[str, str] = {}

    for i, bank in enumerate(target_banks):
        if i > 0:
            await asyncio.sleep(random.uniform(*INTER_BANK_DELAY))

        try:
            data = await asyncio.to_thread(_fetch_bank, bank)
            if data is None:
                statuses[bank] = "fetch_failed"
                continue

            async with session_scope() as session:
                await _upsert_bank_async(session, data)

            statuses[bank] = "ok" if data.meetings else "no_data"
        except Exception as exc:
            logger.error("[%s] Unexpected error: %s", bank, exc, exc_info=True)
            statuses[bank] = "error"

    return statuses


# ── CLI entry point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    sys.path.insert(0, str(Path(__file__).parent.parent))

    parser = argparse.ArgumentParser(description="Scrape rateprobability.com for G8 banks")
    parser.add_argument("banks", nargs="*", help="Bank codes (default: all)")
    args = parser.parse_args()

    statuses = asyncio.run(run_scraper_async(args.banks or None))
    for bank, status in statuses.items():
        symbol = "✓" if status in ("ok", "no_data") else "✗"
        print(f"  {symbol} {bank}: {status}")
