"""Upcoming meetings, blackout windows, minutes/projection dates and scheduled speeches (Step 12 Part D).

Scheduled speeches come only from structured official sources: the Fed Board's
calendar.json and the ECB's weekly schedule of public speaking engagements.
Other banks report "speech calendar pending".
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import yaml
from bs4 import BeautifulSoup

from app.services.cb_summary import bank_config, blackout_window
from app.services.meeting_calendar import SUPPORTED_BANKS, get_upcoming_meetings

MEETINGS_PATH = Path("config/cb_meetings.yaml")
FED_CALENDAR = "https://www.federalreserve.gov/json/calendar.json"
ECB_WEEKLY = "https://www.ecb.europa.eu/press/calendars/weekly/html/index.en.html"
FED_SPEECH_TYPES = {"Speeches", "Testimony"}
SPEECH_DAYS = 14
CACHE_TTL = 60 * 60
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/126.0 Safari/537.36"}
# Minutes-type releases whose lag is exact by rule (same day / three weeks); others are approximate.
EXACT_MINUTES = {"FED", "BOE", "RBNZ"}
_cache: dict[str, tuple[float, Any]] = {}


def _meetings_config(bank: str) -> dict[str, Any]:
    return (yaml.safe_load(MEETINGS_PATH.read_text(encoding="utf-8")) or {}).get(bank, {})


def is_projection_meeting(bank: str, decision: date, meetings_cfg: dict[str, Any]) -> bool:
    if "projection_meetings" in meetings_cfg:
        return str(decision) in {str(d) for d in meetings_cfg["projection_meetings"]}
    return decision.month in bank_config()[bank]["projections"].get("months", [])


def minutes_release(bank: str, decision: date, meetings_cfg: dict[str, Any]) -> dict[str, Any] | None:
    cfg = bank_config()[bank]["minutes"]
    exact = (meetings_cfg.get("summary_of_opinions") or {}).get(str(decision))
    if exact:
        return {"name": cfg.get("name", "minutes"), "date": date.fromisoformat(exact), "exact": True}
    lag = cfg.get("lag_days")
    if lag is None:
        return None
    return {"name": cfg.get("name", "minutes"), "date": decision + timedelta(days=int(lag)),
            "exact": bank in EXACT_MINUTES}


# ── Speech calendars ──────────────────────────────────────────────────

def parse_fed_calendar(payload: str, today: date, days: int = SPEECH_DAYS) -> list[dict[str, Any]]:
    events = json.loads(payload.lstrip("﻿")).get("events", [])
    out = []
    for event in events:
        if event.get("type") not in FED_SPEECH_TYPES or not event.get("month") or not event.get("days"):
            continue
        try:
            day = date.fromisoformat(f"{event['month']}-{int(str(event['days']).split(',')[0].split('-')[0]):02d}")
        except ValueError:
            continue
        if today <= day <= today + timedelta(days=days):
            kind, _, speaker = event.get("title", "").partition(" - ")
            out.append({"date": day, "time": event.get("time"), "tz": "ET", "speaker": speaker or None,
                        "kind": kind or event.get("type"), "event": event.get("location") or event.get("description"),
                        "source": "federalreserve.gov calendar.json"})
    return sorted(out, key=lambda e: e["date"])


def parse_ecb_weekly(html: str, today: date, days: int = SPEECH_DAYS) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for dl in soup.select("main .definition-list dl"):
        day = None
        for node in dl.find_all(["dt", "dd"], recursive=False):
            if node.name == "dt":
                try:
                    day = datetime.strptime(re.sub(r"\s+", " ", node.get_text(" ", strip=True)), "%A, %d %B %Y").date()
                except ValueError:
                    day = None
                continue
            if day is None or not today <= day <= today + timedelta(days=days):
                continue
            fields: dict[str, str] = {}
            label = None
            for child in node.children:
                if getattr(child, "name", None) == "strong":
                    label = child.get_text(strip=True).rstrip(":").lower()
                    fields[label] = ""
                elif label:
                    fields[label] += child.get_text(" ", strip=True) if hasattr(child, "get_text") else str(child)
            fields = {k: re.sub(r"\s+", " ", v).strip() for k, v in fields.items()}
            speaker = fields.get("board member")
            if not speaker:
                match = re.search(r"\b(?:Ms|Mr)\s+([A-Z][\w-]+)", fields.get("event", ""))
                speaker = match.group(1) if match else None
            out.append({"date": day, "time": fields.get("time"), "tz": None, "speaker": speaker,
                        "kind": "engagement", "event": fields.get("event"), "venue": fields.get("venue"),
                        "source": "ECB weekly schedule"})
    return sorted(out, key=lambda e: e["date"])


async def scheduled_speeches(bank: str, today: date) -> dict[str, Any]:
    if bank not in ("FED", "ECB"):
        return {"status": "pending", "reason": "speech calendar pending (no structured official source)"}
    cached = _cache.get(bank)
    if cached and time.monotonic() - cached[0] < CACHE_TTL:
        return cached[1]
    url = FED_CALENDAR if bank == "FED" else ECB_WEEKLY
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=HEADERS) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        return {"status": "unavailable", "reason": f"{url}: {exc}"}
    events = parse_fed_calendar(response.text, today) if bank == "FED" else parse_ecb_weekly(response.text, today)
    result = {"status": "available" if events else "empty", "source": url, "events": events,
              "coverage": "Board members only" if bank == "FED" else "Executive Board, weekly window"}
    _cache[bank] = (time.monotonic(), result)
    return result


async def get_upcoming(bank: str, n: int = 4, today: date | None = None) -> dict[str, Any]:
    bank = bank.upper()
    if bank not in SUPPORTED_BANKS:
        raise ValueError(f"Unknown bank {bank}")
    today = today or date.today()
    cfg = _meetings_config(bank)
    blackout_rule = bank_config()[bank].get("blackout") or {}
    meetings = []
    for meeting in await get_upcoming_meetings(bank, n):
        decision = datetime.fromisoformat(str(meeting["meeting_dt"])).date()
        window = blackout_window(bank, decision)
        meetings.append({"date": decision, "official": meeting.get("is_official", True),
                         "blackout": window or {"status": "unknown", "reason": "No published blackout rule"},
                         "projections": is_projection_meeting(bank, decision, cfg),
                         "projection_name": bank_config()[bank]["projections"]["name"],
                         "minutes": minutes_release(bank, decision, cfg)})
    return {"bank": bank, "as_of": today, "meetings": meetings,
            "blackout_rule_verified": bool(blackout_rule.get("verified")),
            "speeches": await scheduled_speeches(bank, today)}
