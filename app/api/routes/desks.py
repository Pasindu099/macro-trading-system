"""Currency desk page and its lazily loaded panel partials (Jinja + HTMX)."""

import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services import desk_panels, desks

logger = logging.getLogger(__name__)
router = APIRouter(tags=["desks"])
templates = Jinja2Templates(directory=str(Path("app/web/templates")))
PANEL_PARAMS = ("range", "window", "type")


def _desk_or_404(currency: str) -> dict:
    desk = desks.get_desk(currency)
    if desk is None:
        raise HTTPException(status_code=404, detail=f"No desk for {currency.upper()}")
    return desk


def _label(panel: desk_panels.Panel, desk: dict) -> str:
    return panel.label.format(cb=desk["cb_short"])


@router.get("/desks/{currency}", response_class=HTMLResponse)
async def desk_page(request: Request, currency: str) -> HTMLResponse:
    desk = _desk_or_404(currency)
    ccy = desk["currency"]
    chain = [{"n": p.n, "label": _label(p, desk), "href": f"#{p.anchor}"} for p in desk_panels.PANELS if p.n]
    return templates.TemplateResponse(request, "desk/page.html", {
        "page_title": f"{ccy} Desk | ForexCompass", "desk": desk, "nav": desks.desk_nav(ccy),
        "chain": chain, "panels": desk_panels.PANELS, "now": datetime.now(UTC),
    })


@router.get("/desks/{currency}/panels/{panel_id}", response_class=HTMLResponse)
async def desk_panel(request: Request, currency: str, panel_id: str) -> HTMLResponse:
    desk = _desk_or_404(currency)
    panel = desk_panels.PANELS_BY_ID.get(panel_id)
    if panel is None:
        raise HTTPException(status_code=404, detail="Unknown panel")
    params = {k: request.query_params[k] for k in PANEL_PARAMS if k in request.query_params}
    key = (desk["currency"], panel_id, tuple(sorted(params.items())))
    cached = desks.cache_get(key)
    if cached is not None:
        return HTMLResponse(cached)
    render = dict(request=request, desk=desk, panel=panel, label=_label(panel, desk),
                  url=f"/desks/{desk['currency']}/panels/{panel_id}")
    try:
        ctx = await panel.builder(desk, params)
        html = templates.get_template(f"desk/panels/{panel_id}.html").render(ctx=ctx, **render)
    except Exception:
        # One failing source must never break the page: log it and show a short message.
        logger.exception("Desk panel %s/%s failed", desk["currency"], panel_id)
        html = templates.get_template("desk/_error.html").render(
            ctx={"state": "error", "message": "This panel could not load. Try again shortly."}, **render)
        return HTMLResponse(html)
    desks.cache_set(key, html)
    return HTMLResponse(html)
