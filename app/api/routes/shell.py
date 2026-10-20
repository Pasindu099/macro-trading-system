"""Workspace navigation and Overview panel routes."""

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.db.session import get_sessionmaker
from app.services.macro_state import get_macro_state_board
from app.services import desks, freshness, overview, news, pairs_service

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


@router.get("/pairs", response_class=HTMLResponse)
async def pairs_page(request: Request) -> HTMLResponse:
    params = request.query_params
    group = params.get("group", "all")
    if group not in {"all", "usd", "jpy", "neighbours", "commodity"}:
        raise HTTPException(400, "Unknown pair group")
    sort = params.get("sort", pairs_service.DEFAULT_SORT)
    if sort not in {"pair", "last", "week_change", "month_change", "overall_score", "policy_gap",
                    "data_implied", "priced", "spread_2y", "spread_change_20d", "driver",
                    "cot_implied", "retail_long_pct", "warnings"}:
        raise HTTPException(400, "Unknown sort column")
    descending = params.get("direction", "desc") != "asc"
    search = params.get("q", "")[:40]
    data = desks.cache_get(("pairs", "screener"))
    if data is None:
        try:
            data = await pairs_service.screener()
            desks.cache_set(("pairs", "screener"), data, ttl=60)
        except Exception:
            logger.exception("Pair screener failed")
            data = {"rows": [], "freshness": {}, "state": "error"}
    rows = pairs_service.sort_rows(pairs_service.filter_rows(data["rows"], group, search), sort, descending)
    all_rows = data["rows"]
    gaps = [row for row in all_rows if row["policy_gap"] is not None]
    diverging = [row for row in all_rows if row["driver"] == "diverging"]
    cot = [row for row in all_rows if row["cot_implied"] is not None]
    tiles = {"gap": max(gaps, key=lambda row: abs(row["policy_gap"])) if gaps else None,
             "driver": max(diverging, key=lambda row: row["driver_as_of"] or date.min) if diverging else None,
             "cot": max(cot, key=lambda row: abs(row["cot_implied"])) if cot else None}
    return templates.TemplateResponse(request, "pairs/page.html", {
        "page_title": "Pairs | ForexCompass", "rows": rows, "tiles": tiles,
        "group": group, "sort": sort, "descending": descending, "search": search,
        "state": data.get("state", "ok"), "freshness": data["freshness"],
    })


@router.get("/pairs/{pair_code}", response_class=HTMLResponse)
async def pair_pending_page(request: Request, pair_code: str) -> HTMLResponse:
    match = next((item for item in pairs_service.pair_config()
                  if item["pair"].replace("/", "") == pair_code.upper()), None)
    if match is None:
        raise HTTPException(404, "Unknown pair")
    return templates.TemplateResponse(request, "pairs/pending.html", {
        "page_title": f"{match['pair']} | ForexCompass", "pair": match["pair"],
        "desks": [currency for currency in (match["base"], match["quote"]) if currency in ("USD", "EUR")],
    })


@router.get("/desks", response_class=HTMLResponse)
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


@router.get("/news", response_class=HTMLResponse)
async def news_page(request: Request) -> HTMLResponse:
    params = request.query_params
    show_no_fx_link = params.get("show_geopolitics_without_fx_link") == "1"
    query = params.get("q", "").strip()[:100]
    currency = params.get("currency", "").upper()
    news_type = params.get("type", "")
    impact = params.get("impact", "")
    active_only = params.get("active") == "1"
    if currency not in {"", "USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "CNY"}:
        raise HTTPException(400, "Unknown currency")
    if news_type not in {"", "Data", "CB speaker", "Politics & fiscal", "Geopolitics",
                         "Flows & market", "Commodities", "Analysis"} or impact not in {"", "High", "Medium", "Low"}:
        raise HTTPException(400, "Unknown news filter")
    try:
        start = date.fromisoformat(params["start"]) if params.get("start") else None
        end = date.fromisoformat(params["end"]) if params.get("end") else None
    except ValueError as exc:
        raise HTTPException(400, "Dates must use YYYY-MM-DD") from exc
    if start and end and start > end:
        raise HTTPException(400, "Start date must be before end date")
    now = datetime.now(UTC)
    state = "ok"
    try:
        options = {"show_no_fx_link": show_no_fx_link}
        if any((query, currency, news_type, impact, active_only, start, end)):
            options.update(query=query, currency=currency, news_type=news_type, impact=impact,
                           active_only=active_only, start=start, end=end)
        items = await news.tagged_feed(**options)
        pin_items = (await news.tagged_feed() if len(options) > 1 else items)
    except Exception:
        logger.exception("News feed unavailable")
        items, pin_items, state = [], [], "error"
    try:
        sources = await news.source_health(now.date())
    except Exception:
        logger.exception("News source health unavailable")
        sources = []
    try:
        context = await news.today_context(now.date())
    except Exception:
        logger.exception("News sidebar unavailable")
        context = {"releases": [], "speakers": [], "blackouts": [], "mentions": []}
    response = templates.TemplateResponse(request, "news/page.html", {
        "page_title": "News | ForexCompass", "items": items, "state": state,
        "pinned": [item for item in pin_items if item["impact"] == "High" and
                   now - timedelta(hours=6) <= item["published_at"] <= now][:6],
        "sources": sources, "context": context, "filters": {
            "q": query, "currency": currency, "type": news_type, "impact": impact,
            "active": active_only, "start": start, "end": end,
            "show_no_fx_link": show_no_fx_link,
        },
    })
    response.headers["Cache-Control"] = "private, max-age=60"
    return response


@router.get("/event-log", include_in_schema=False)
async def event_log_redirect() -> RedirectResponse:
    """The Event Log page moved under the calendar."""
    return RedirectResponse("/calendar/event-log", status_code=307)
