"""/positioning: COT positioning page and lazy panels (Step 14 Part C)."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db.session import get_sessionmaker
from app.services import desks, freshness, positioning_redesign as page
from app.services import positioning as pos, positioning_values as values
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
    instrument = q.get("instrument",ccy).upper()
    try:
        if instrument not in CURRENCIES:
            a,b = pos.normalize_pair(instrument); instrument = f"{a}/{b}"
    except ValueError as exc:
        raise HTTPException(400,"Unknown positioning instrument") from exc
    group,units,range_key = q.get("group","leveraged_funds"),q.get("units","usd"),q.get("range_key","3Y")
    if group not in values.GROUPS or units not in {"usd","pct_oi"} or range_key not in values.RANGES:
        raise HTTPException(400,"Unknown positioning chart option")
    view = q.get("view","simple")
    if view not in {"simple","bars"}: raise HTTPException(400,"Unknown positioning view")
    return {"lookback":lookback,"ccy":ccy,"window":window,"instrument":instrument,"group":group,
            "units":units,"range_key":range_key,"view":view}


@router.get("/positioning", response_class=HTMLResponse)
async def positioning_page(request: Request) -> HTMLResponse:
    params = _params(request)
    return templates.TemplateResponse(request, "positioning/page.html", {
        "page_title": "Positioning · COT | ForexCompass", "p": params, "panels": ORDER, "titles": page.TITLES,
        "currencies": ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"),
        "lookbacks": {key: value for key, value in page.LOOKBACK_LABELS.items() if key != "1y"},
        "panel_query":urlencode(params),
    })


@router.get("/positioning/panels/{panel_id}", response_class=HTMLResponse)
async def positioning_panel(request: Request, panel_id: str) -> HTMLResponse:
    builder = page.PANELS.get(panel_id)
    if builder is None:
        raise HTTPException(status_code=404, detail="Unknown positioning panel")
    params = _params(request)
    cache_key = ("positioning", panel_id, tuple(sorted(params.items())))
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
    template = templates.get_template("positioning/panel_v2.html")
    def link(anchor="",**changes):
        return "/positioning?"+urlencode({**params,**changes})+("#pos-"+anchor if anchor else "")
    render = dict(panel_id=panel_id, p=params, titles=page.TITLES,
                  lookbacks={key: value for key, value in page.LOOKBACK_LABELS.items() if key != "1y"},
                  currency_items=[(c,c) for c in CURRENCIES], pair_items=[f"{a}/{b}" for a,b in pos.config_pairs()],
                  glossary=takeaways.glossary(),groups=values.GROUP_LABELS,link=link)
    try:
        ctx = await builder(**params)
        take_data = ctx.get("takeaway_data") or (await takeaway_inputs(panel_id, params) if ctx.get("state") in {"ok","partial","empty"} else {"state":ctx.get("state")})
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
