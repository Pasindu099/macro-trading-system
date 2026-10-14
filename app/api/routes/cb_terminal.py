"""/central-banks: Central Bank Terminal page and lazy panels (Step 12 Part G)."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db.session import get_sessionmaker
from app.services import cb_terminal, desks, freshness
from app.services.cb_summary import bank_config

router = APIRouter(tags=["central-banks"])
templates = Jinja2Templates(directory=str(Path("app/web/templates")))
logger = logging.getLogger(__name__)
CACHE_TTL = 60


def _bank(value: str | None) -> str:
    code = (value or "FED").upper()
    if code not in cb_terminal.BANKS:
        raise HTTPException(status_code=404, detail=f"Unknown bank {code}")
    return code


@router.get("/central-banks", response_class=HTMLResponse)
async def central_banks_page(request: Request) -> HTMLResponse:
    bank = _bank(request.query_params.get("bank"))
    view = request.query_params.get("view", "overview")
    if view not in cb_terminal.VIEWS:
        view = "overview"
    return templates.TemplateResponse(request, "cb/page.html", {
        "page_title": f"{bank} · Central Bank Terminal | ForexCompass", "bank": bank, "view": view,
        "banks": [(code, bank_config()[code]["name"]) for code in cb_terminal.BANKS],
        "panels": [(panel, cb_terminal.TITLES[panel]) for panel in cb_terminal.VIEWS[view]],
    })


@router.get("/central-banks/panels/{panel_id}", response_class=HTMLResponse)
async def central_banks_panel(request: Request, panel_id: str) -> HTMLResponse:
    builder = cb_terminal.PANELS.get(panel_id)
    if builder is None:
        raise HTTPException(status_code=404, detail="Unknown Central Bank panel")
    bank = _bank(request.query_params.get("bank"))
    speaker = request.query_params.get("speaker") or None
    cache_key = ("cb", panel_id, bank, speaker)
    cached = desks.cache_get(cache_key)
    if cached is not None:
        return HTMLResponse(cached)
    try:
        async with get_sessionmaker()() as session:
            statuses = await freshness.get_data_freshness(session)
            manual = await freshness.manual_import_rows(session, bank)
        rows = freshness.panel_rows(statuses, freshness.CB_PANEL_SOURCES.get(panel_id, ()))
        rows += [row for row in manual if row["source"].endswith(freshness.CB_PANEL_MANUAL.get(panel_id, "-"))]
    except Exception:
        logger.exception("Central Bank freshness lookup failed")
        rows = []
    strip = templates.get_template("_freshness.html").render(rows=rows) if rows else ""
    template = templates.get_template("cb/panel.html")
    try:
        ctx = await builder(bank, speaker=speaker)
        html = strip + template.render(panel_id=panel_id, bank=bank, ctx=ctx, titles=cb_terminal.TITLES)
    except Exception:
        logger.exception("Central Bank panel %s (%s) failed", panel_id, bank)
        ctx = {"state": "error", "message": "This panel could not load. Try again shortly."}
        return HTMLResponse(strip + template.render(panel_id=panel_id, bank=bank, ctx=ctx, titles=cb_terminal.TITLES))
    desks.cache_set(cache_key, html, ttl=CACHE_TTL)
    return HTMLResponse(html)
