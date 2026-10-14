"""Workspace navigation and Overview panel routes."""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.db.session import get_sessionmaker
from app.services.macro_state import get_macro_state_board
from app.services import desks, freshness, overview

router = APIRouter(tags=["shell"])
templates = Jinja2Templates(directory=str(Path("app/web/templates")))
logger = logging.getLogger(__name__)

SECTIONS = {
    "/": ("Overview", "A clear view of the macro backdrop across currencies."),
    "/desks": ("Desks", "Country research and data for each currency desk."),
    "/pairs": ("Pairs", "Cross-market context for currency pairs."),
    "/calendar": ("Calendar", "Upcoming releases and central bank decisions."),
    "/event-log": ("Event Log", "Research notes and event interpretations."),
    "/news": ("News", "Live headlines and macro context."),
    "/positioning": ("Positioning", "Market positioning and sentiment."),
    "/central-banks": ("Central Banks", "Policy, projections, and rate expectations."),
    "/data": ("Data", "Source health and research datasets."),
}


@router.get("/", response_class=HTMLResponse)
async def overview_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "overview/page.html", {
        "page_title": "Overview | ForexCompass", "panels": overview.PANELS,
    })


@router.get("/data-freshness", response_class=HTMLResponse)
async def data_freshness() -> HTMLResponse:
    try:
        async with get_sessionmaker()() as session:
            statuses = await freshness.get_data_freshness(session)
    except Exception:
        logger.exception("Data freshness lookup failed")
        statuses = {source: freshness.status_for(source, None) for source in freshness.QUERIES}
    return HTMLResponse(templates.get_template("_freshness.html").render(rows=list(statuses.values())))


@router.get("/overview/panels/{panel_id}", response_class=HTMLResponse)
async def overview_panel(request: Request, panel_id: str) -> HTMLResponse:
    builder = overview.PANELS.get(panel_id)
    if builder is None:
        raise HTTPException(status_code=404, detail="Unknown Overview panel")
    pair_options = {key: request.query_params.get(key, default) for key, default in
                    (("lens", "overall"), ("path", "data"), ("group", "all"))} if panel_id == "pairs" else {}
    cache_key = ("overview", panel_id, *pair_options.values()) if pair_options else ("overview", panel_id)
    cached = desks.cache_get(cache_key)
    if cached is not None:
        return HTMLResponse(cached)
    try:
        async with get_sessionmaker()() as session:
            statuses = await freshness.get_data_freshness(session)
    except Exception:
        logger.exception("Overview freshness lookup failed")
        statuses = {source: freshness.status_for(source, None) for source in freshness.QUERIES}
    strip = templates.get_template("_freshness.html").render(
        rows=freshness.panel_rows(statuses, freshness.OVERVIEW_PANEL_SOURCES[panel_id]))
    try:
        ctx = await builder(**pair_options)
    except Exception:
        logger.exception("Overview panel %s failed", panel_id)
        ctx = {"state": "error", "message": "This panel could not load. Try again shortly."}
        return HTMLResponse(strip + templates.get_template("overview/panel.html").render(panel_id=panel_id, ctx=ctx))
    html = strip + templates.get_template("overview/panel.html").render(panel_id=panel_id, ctx=ctx)
    desks.cache_set(cache_key, html, ttl=60)
    return HTMLResponse(html)


@router.get("/desks", response_class=HTMLResponse)
@router.get("/pairs", response_class=HTMLResponse)
@router.get("/news", response_class=HTMLResponse)
@router.get("/positioning", response_class=HTMLResponse)
@router.get("/data", response_class=HTMLResponse)
async def section_page(request: Request) -> HTMLResponse:
    title, description = SECTIONS[request.url.path]
    desks = []
    if request.url.path == "/desks":
        try:
            async with get_sessionmaker()() as session:
                board = await get_macro_state_board(session)
            rows = {row["currency"]: row for row in board["rows"] if row["currency"] in ("USD", "EUR")}
            desks = [{"currency": currency, "macro": rows.get(currency)} for currency in ("USD", "EUR")]
        except Exception:
            desks = [{"currency": currency, "macro": None} for currency in ("USD", "EUR")]
    return templates.TemplateResponse(request, "section.html", {
        "page_title": f"{title} | ForexCompass", "section_title": title,
        "description": description, "desks": desks,
    })


@router.get("/event-log", include_in_schema=False)
async def event_log_redirect() -> RedirectResponse:
    """The Event Log page moved under the calendar."""
    return RedirectResponse("/calendar/event-log", status_code=307)
