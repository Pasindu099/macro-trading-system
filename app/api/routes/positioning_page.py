"""/positioning: COT positioning page and lazy panels (Step 14 Part C)."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db.session import get_sessionmaker
from app.services import desks, freshness, positioning_page as page
from app.services import takeaways
from app.services.takeaways.positioning import inputs as takeaway_inputs
from app.services.positioning import CURRENCIES, LOOKBACK_YEARS

router = APIRouter(tags=["positioning"])
templates = Jinja2Templates(directory=str(Path("app/web/templates")))
logger = logging.getLogger(__name__)
CACHE_TTL = 60
ORDER = ("summary", "crowding", "flows", "squeeze", "price", "extremes", "retail")


def _params(request: Request) -> dict[str, str]:
    q = request.query_params
    lookback = q.get("lookback", "3y")
    ccy = q.get("ccy", "JPY").upper()
    window = q.get("window", "1W").upper()
    if lookback not in LOOKBACK_YEARS or ccy not in CURRENCIES or window not in {"1W", "4W"}:
        raise HTTPException(status_code=400, detail="Unknown positioning filter")
    if lookback == "1y":
        lookback = "52w"
    return {"lookback": lookback, "ccy": ccy, "window": window}


@router.get("/positioning", response_class=HTMLResponse)
async def positioning_page(request: Request) -> HTMLResponse:
    params = _params(request)
    return templates.TemplateResponse(request, "positioning/page.html", {
        "page_title": "Positioning · COT | ForexCompass", "p": params, "panels": ORDER, "titles": page.TITLES,
        "currencies": ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"),
        "lookbacks": {key: value for key, value in page.LOOKBACK_LABELS.items() if key != "1y"},
    })


@router.get("/positioning/panels/{panel_id}", response_class=HTMLResponse)
async def positioning_panel(request: Request, panel_id: str) -> HTMLResponse:
    builder = page.PANELS.get(panel_id)
    if builder is None:
        raise HTTPException(status_code=404, detail="Unknown positioning panel")
    params = _params(request)
    cache_key = ("positioning", panel_id, params["lookback"], params["ccy"], params["window"])
    cached = desks.cache_get(cache_key)
    if cached is not None:
        return HTMLResponse(cached)
    try:
        async with get_sessionmaker()() as session:
            statuses = await freshness.get_data_freshness(session)
        rows = freshness.panel_rows(statuses, page.SOURCES[panel_id])
    except Exception:
        logger.exception("Positioning freshness lookup failed")
        rows = []
    strip = templates.get_template("_freshness.html").render(rows=rows) if rows else ""
    template = templates.get_template("positioning/panel.html")
    render = dict(panel_id=panel_id, p=params, titles=page.TITLES,
                  lookbacks={key: value for key, value in page.LOOKBACK_LABELS.items() if key != "1y"},
                  currency_items=[(c, c) for c in ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF")])
    try:
        ctx = await builder(**params)
        take_data = await takeaway_inputs(panel_id, params) if ctx.get("state") in {"ok","partial","empty"} else {"state":ctx.get("state")}
        if any(row.get("stale") for row in rows) and take_data.get("state") not in {"unavailable","pending"}:
            take_data = {**take_data,"stale":True}
        ctx["takeaway"] = takeaways.REGISTRY[panel_id](take_data)
        html = strip + template.render(ctx=ctx, **render)
    except Exception:
        logger.exception("Positioning panel %s failed", panel_id)
        ctx = {"state": "error", "message": "This panel could not load. Try again shortly."}
        ctx["takeaway"] = takeaways.REGISTRY[panel_id](ctx)
        return HTMLResponse(strip + template.render(ctx=ctx, **render))
    desks.cache_set(cache_key, html, ttl=CACHE_TTL)
    return HTMLResponse(html)
