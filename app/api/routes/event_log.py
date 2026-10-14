"""Event Log pages: list, create/edit, delete and on-demand AI notes."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.db.session import get_sessionmaker
from app.services import event_reaction_log as log
from app.services.event_reactions import REACTION_PAIRS, pairs_for

router = APIRouter(tags=["event-log"])
templates = Jinja2Templates(directory=str(Path("app/web/templates")))
CURRENCIES = ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "CNY")
ALL_PAIRS = sorted({pair for pairs in REACTION_PAIRS.values() for pair in pairs})
_TEXT = ("event_title", "event_type", "expectation_before", "outcome_after", "manual_notes",
         "ai_note_final", "direction")
_NUMBERS = ("entry_price", "exit_price", "size", "pnl_pips", "pnl_amount",
            "forecast_value", "actual_value", "previous_value")


def _optional_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def parse_entry_form(form: Any) -> dict[str, Any]:
    """Blank optional fields become NULL; ai_note_raw is never read from the form."""
    data: dict[str, Any] = {}
    for field in _TEXT:
        value = (form.get(field) or "").strip()
        data[field] = value or None
    for field in _NUMBERS:
        value = (form.get(field) or "").strip()
        try:
            data[field] = Decimal(value) if value else None
        except InvalidOperation as exc:
            raise ValueError(f"{field.replace('_', ' ')} must be a number") from exc
    currency = (form.get("currency") or "").upper()
    data["currency"] = currency if currency in CURRENCIES else None
    data["event_date"] = _optional_date(form.get("event_date"))
    data["pairs"] = [pair for pair in form.getlist("pairs") if pair in ALL_PAIRS]
    data["traded"] = form.get("traded") == "yes"
    if not data["traded"]:
        data["direction"] = None
    return data


async def _form_context(session, note=None, release_id: int | None = None, **extra) -> dict[str, Any]:
    release_id = release_id or (note.indicator_release_id if note else None)
    release = await log._release_context(session, release_id) if release_id else None
    reactions = await log.linked_reactions(session, release_id)
    default_pairs = pairs_for(release["currency_code"]) if release and release["currency_code"] else []
    return {"note": note, "release": release, "reactions": reactions, "currencies": CURRENCIES,
            "all_pairs": ALL_PAIRS, "event_types": log.EVENT_TYPES,
            "selected_pairs": (note.pairs if note else None) or default_pairs, **extra}


@router.get("/calendar/event-log", response_class=HTMLResponse)
async def event_log_page(request: Request) -> HTMLResponse:
    params = request.query_params
    try:
        start, end = _optional_date(params.get("start")), _optional_date(params.get("end"))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    traded = {"yes": True, "no": False}.get(params.get("traded", ""))
    currency = params.get("currency") if params.get("currency") in CURRENCIES else None
    event_type = params.get("event_type") if params.get("event_type") in log.EVENT_TYPES else None
    async with get_sessionmaker()() as session:
        entries = await log.list_entries(session, currency=currency, start=start, end=end,
                                         event_type=event_type, traded=traded)
    return templates.TemplateResponse(request, "calendar/event_log.html", {
        "page_title": "Event Log | ForexCompass", "entries": entries, "currencies": CURRENCIES,
        "event_types": log.EVENT_TYPES,
        "filters": {"currency": currency, "start": start, "end": end, "event_type": event_type,
                    "traded": params.get("traded", "")},
    })


@router.get("/calendar/event-log/search", response_class=HTMLResponse)
async def event_log_search(request: Request) -> HTMLResponse:
    async with get_sessionmaker()() as session:
        results = await log.search_releases(session, request.query_params.get("q", ""))
    return templates.TemplateResponse(request, "calendar/event_log_search.html", {"results": results})


@router.get("/calendar/event-log/new", response_class=HTMLResponse)
async def event_log_new(request: Request) -> HTMLResponse:
    release_id = request.query_params.get("release_id")
    async with get_sessionmaker()() as session:
        context = await _form_context(session, release_id=int(release_id) if release_id and release_id.isdigit() else None)
    return templates.TemplateResponse(request, "calendar/event_log_form.html",
                                      {"page_title": "New Event Log entry | ForexCompass", **context})


@router.post("/calendar/event-log/new", response_class=HTMLResponse)
async def event_log_create(request: Request):
    form = await request.form()
    release_id = int(form["release_id"]) if (form.get("release_id") or "").isdigit() else None
    async with get_sessionmaker()() as session:
        try:
            note = await log.save_entry(session, None, parse_entry_form(form), release_id=release_id)
        except ValueError as exc:
            context = await _form_context(session, release_id=release_id, error=str(exc))
            return templates.TemplateResponse(request, "calendar/event_log_form.html",
                                              {"page_title": "New Event Log entry | ForexCompass", **context},
                                              status_code=422)
        await session.commit()
    return RedirectResponse(f"/calendar/event-log/{note.id}/edit?saved=1", status_code=303)


@router.get("/calendar/event-log/{note_id}/edit", response_class=HTMLResponse)
async def event_log_edit(request: Request, note_id: int) -> HTMLResponse:
    async with get_sessionmaker()() as session:
        note = await session.get(log.EventReactionNote, note_id)
        if note is None:
            raise HTTPException(404, "Event Log entry not found")
        context = await _form_context(session, note, saved=request.query_params.get("saved") == "1")
    return templates.TemplateResponse(request, "calendar/event_log_form.html",
                                      {"page_title": "Edit Event Log entry | ForexCompass", **context})


@router.post("/calendar/event-log/{note_id}", response_class=HTMLResponse)
async def event_log_update(request: Request, note_id: int):
    form = await request.form()
    async with get_sessionmaker()() as session:
        try:
            note = await log.save_entry(session, note_id, parse_entry_form(form))
        except ValueError as exc:
            await session.rollback()
            note = await session.get(log.EventReactionNote, note_id)
            if note is None:
                raise HTTPException(404, "Event Log entry not found") from exc
            context = await _form_context(session, note, error=str(exc))
            return templates.TemplateResponse(request, "calendar/event_log_form.html",
                                              {"page_title": "Edit Event Log entry | ForexCompass", **context},
                                              status_code=422)
        await session.commit()
    return RedirectResponse(f"/calendar/event-log/{note_id}/edit?saved=1", status_code=303)


@router.post("/calendar/event-log/{note_id}/delete")
async def event_log_delete(request: Request, note_id: int):
    form = await request.form()
    if form.get("confirm") != "yes":
        raise HTTPException(400, "Deletion requires confirmation")
    async with get_sessionmaker()() as session:
        if not await log.delete_entry(session, note_id):
            raise HTTPException(404, "Event Log entry not found")
        await session.commit()
    return RedirectResponse("/calendar/event-log", status_code=303)


@router.post("/calendar/event-log/{note_id}/ai", response_class=HTMLResponse)
async def event_log_ai(request: Request, note_id: int):
    async with get_sessionmaker()() as session:
        try:
            await log.generate_ai_interpretation(session, note_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        except log.AINoteError as exc:
            await session.rollback()
            note = await session.get(log.EventReactionNote, note_id)
            context = await _form_context(session, note, ai_error=str(exc))
            return templates.TemplateResponse(request, "calendar/event_log_form.html",
                                              {"page_title": "Edit Event Log entry | ForexCompass", **context})
        await session.commit()
    return RedirectResponse(f"/calendar/event-log/{note_id}/edit", status_code=303)
