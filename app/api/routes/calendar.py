"""Calendar list and progressive day fragments."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db.session import get_sessionmaker
from app.services.calendar_service import list_events

router = APIRouter(tags=["calendar"])
templates = Jinja2Templates(directory=str(Path("app/web/templates")))
CURRENCIES = ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "CNY")
PRESETS = ("yesterday", "today", "tomorrow", "this_week", "last_week",
           "next_week", "last_30_days", "custom")


def _zone(request: Request) -> tuple[str, ZoneInfo]:
    mode = request.cookies.get("calendar_tz_mode", "utc")
    if mode != "local":
        return "UTC", ZoneInfo("UTC")
    name = unquote(request.cookies.get("calendar_tz_name", "UTC"))
    try:
        return name, ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return "UTC", ZoneInfo("UTC")


def resolve_range(preset: str, today: date, start: str | None = None,
                  end: str | None = None) -> tuple[date, date]:
    if preset == "custom":
        if not start or not end:
            raise ValueError("Custom range requires start and end dates")
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    elif preset == "yesterday":
        first = last = today - timedelta(days=1)
    elif preset == "tomorrow":
        first = last = today + timedelta(days=1)
    elif preset in ("this_week", "last_week", "next_week"):
        offset = {"this_week": 0, "last_week": -7, "next_week": 7}[preset]
        first = today - timedelta(days=today.weekday()) + timedelta(days=offset)
        last = first + timedelta(days=6)
    elif preset == "last_30_days":
        first, last = today - timedelta(days=29), today
    else:
        first = last = today
    if last < first:
        raise ValueError("End date must be on or after start date")
    return first, last


def _filters(request: Request) -> dict:
    preset = request.query_params.get("preset", "today")
    if preset not in PRESETS:
        preset = "today"
    zone_name, zone = _zone(request)
    today = datetime.now(zone).date()
    first, last = resolve_range(preset, today, request.query_params.get("start"),
                                request.query_params.get("end"))
    currencies = {value.upper() for value in request.query_params.getlist("currency")
                  if value.upper() in CURRENCIES}
    impact = request.query_params.get("impact", "high")
    if impact not in ("high", "medium", "all"):
        impact = "high"
    return dict(preset=preset, start=first, end=last, currencies=currencies,
                impact=impact, min_impact={"high": 1, "medium": 2, "all": 3}[impact],
                surprise_only=request.query_params.get("surprise") == "1",
                zone_name=zone_name, zone=zone)


def _day_url(day: date, filters: dict) -> str:
    params = [("preset", "custom"), ("start", filters["start"].isoformat()), ("end", filters["end"].isoformat()),
              ("day", day.isoformat()), ("impact", filters["impact"])]
    params += [("currency", currency) for currency in sorted(filters["currencies"])]
    if filters["surprise_only"]:
        params.append(("surprise", "1"))
    return "/calendar/day?" + urlencode(params)


@router.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request) -> HTMLResponse:
    try:
        filters = _filters(request)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return templates.TemplateResponse(request, "calendar/page.html", {
        "page_title": "Calendar | ForexCompass", "filters": filters,
        "currencies": CURRENCIES, "presets": PRESETS,
        "first_url": _day_url(filters["start"], filters),
    })


@router.get("/calendar/day", response_class=HTMLResponse)
async def calendar_day(request: Request) -> HTMLResponse:
    try:
        filters = _filters(request)
        current = date.fromisoformat(request.query_params["day"])
        if not filters["start"] <= current <= filters["end"]:
            raise ValueError("Day outside selected range")
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    # A local day may straddle three UTC dates. Each query stays bounded to a day.
    async with get_sessionmaker()() as session:
        events = await list_events(session, current - timedelta(days=1),
                                   current + timedelta(days=1), filters["currencies"] or None,
                                   filters["min_impact"], surprise_only=filters["surprise_only"])
    zone = filters["zone"]
    events = [event for event in events if event["released_at"].astimezone(zone).date() == current]
    for event in events:
        event["local_time"] = event["released_at"].astimezone(zone).strftime("%H:%M")
    next_day = current + timedelta(days=1)
    return templates.TemplateResponse(request, "calendar/day.html", {
        "day": current, "events": events,
        "next_url": _day_url(next_day, filters) if next_day <= filters["end"] else None,
    })
