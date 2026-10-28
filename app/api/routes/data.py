"""Data library pages, lazy fragments and read-only CSV downloads."""
from __future__ import annotations

import logging
from datetime import date
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.db.session import get_sessionmaker
from app.services import data_library as library, data_quality as quality, desks, research_datasets as datasets

router = APIRouter(tags=["data"])
templates = Jinja2Templates(directory="app/web/templates")
logger = logging.getLogger(__name__)
COUNTRY_NAMES = dict(zip(library.COUNTRIES, ("United States", "Euro area", "United Kingdom", "Japan", "Canada",
                        "Switzerland", "Australia", "New Zealand", "Germany", "France", "China")))
CURRENCIES = dict(zip(library.COUNTRIES, ("USD", "EUR", "GBP", "JPY", "CAD", "CHF", "AUD", "NZD", "EUR", "EUR", "CNY")))


def _params(request: Request) -> dict:
    q = request.query_params
    try:
        params = {"date_from": date.fromisoformat(q["start"]) if q.get("start") else None,
                  "date_to": date.fromisoformat(q["end"]) if q.get("end") else None,
                  "offset": int(q.get("offset", 0)), "theme": q.get("theme") or None,
                  "importance": int(q["importance"]) if q.get("importance") else None,
                  "search": q.get("q", "")[:120], "series": [v for v in q.getlist("series") if v] or None,
                  "currencies": [v.upper() for v in q.getlist("currency") if v] or None}
        if params["offset"] < 0 or params["offset"] > 1_000_000:
            raise ValueError("Invalid page offset")
        if params["date_from"] and params["date_to"] and params["date_from"] > params["date_to"]:
            raise ValueError("Start date must not exceed end date")
        if params["theme"] and params["theme"] not in library.THEMES:
            raise ValueError("Unknown theme")
        if params["importance"] is not None and params["importance"] not in (1, 2, 3):
            raise ValueError("Unknown importance")
        return params
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _dates(p):
    return {key: p[key] for key in ("date_from", "date_to")}


def _dataset_filters(p):
    return {**_dates(p), "series": p["series"], "currencies": p["currencies"]}


def _sparkline(points):
    values = [float(point[1]) for point in points]
    low, high = min(values, default=0), max(values, default=0)
    return " ".join(f"{i * 100 / max(1, len(values)-1):.1f},{22-(value-low)*20/(high-low or 1):.1f}"
                    for i, value in enumerate(values))


async def _content(request: Request, view: str, country: str | None, canonical: str | None,
                   name: str | None, p: dict) -> dict:
    async with get_sessionmaker()() as session:
        if view == "landing":
            countries = []
            for code in library.COUNTRIES:
                result = await library.catalogue(session, code)
                scored = [r for group in result["themes"].values() for r in group if r["surprise_sigma"] is not None]
                notable = max(scored, key=lambda r: (r["latest_date"], abs(r["surprise_sigma"])), default=None)
                countries.append({**result, "name": COUNTRY_NAMES[code], "notable": notable})
            health = await quality.source_health(session, scheduler=getattr(request.app.state, "scheduler", None))
            return {"countries": countries, "health": health, "datasets": await datasets.catalogue(session)}
        if view == "country":
            result = await library.catalogue(session, country, theme=p["theme"], importance=p["importance"], search=p["search"])
            for group in result["themes"].values():
                for row in group:
                    row["spark_points"] = _sparkline(row["sparkline"])
            return {"catalogue": result}
        if view == "indicator":
            result = await library.history(session, country, canonical, offset=p["offset"], **_dates(p))
            # One latest version per reference period for charts; table retains every print.
            seen, points = set(), []
            for row in result["rows"]:
                key = row["period_start_date"] or row["period"] or row["released_at"].date()
                if key in seen:
                    continue
                seen.add(key)
                points.append({"date": str(row["period_start_date"] or row["released_at"].date()),
                               "actual": float(row["latest_actual"]), "first": float(row["first_actual"]),
                               "consensus": float(row["consensus"]) if row["consensus"] is not None else None,
                               "sigma": float(row["surprise_sigma"]) if row["surprise_sigma"] is not None else None})
            return {**result, "chart": sorted(points, key=lambda point: point["date"]),
                    "desk": "/desks/" + CURRENCIES[country] if country != "CN" else None,
                    "currency": CURRENCIES[country]}
        if view == "sources":
            return {"health": await quality.source_health(session, scheduler=getattr(request.app.state, "scheduler", None)),
                    "quality": await quality.quality_log(session)}
        result = await datasets.browse(session, name, offset=p["offset"], **_dataset_filters(p))
        options = await datasets.describe(session, name) if any(_dataset_filters(p).values()) else result["dataset"]
        result["series_options"] = sorted(set(options.get("expected_series", [])) | {r["series"] for r in options["series"]})
        result["chart"] = {"type": "bar" if name == "cot" else "line", "rows": [
            {"date": str(r["date"]), "series": r["series"], "value": float(r["value"]) if r["value"] is not None else None}
            for r in result["rows"]]}
        # The chart displays the current table page; all rows remain available via pagination/export.
        return result


def _shell(request, view, title, *, country=None, canonical=None, name=None):
    _params(request)
    query = {**dict(request.query_params), "view": view}
    query.update({key: value for key, value in (("country", country), ("canonical", canonical), ("name", name)) if value})
    pairs = [(key, value) for key, value in query.items() if key not in {"series", "currency"}]
    pairs += [(key, value) for key in ("series", "currency") for value in request.query_params.getlist(key)]
    return templates.TemplateResponse(request, "data/page.html", {
        "page_title": title + " | ForexCompass", "title": title, "view": view,
        "content_url": "/data/content?" + urlencode(pairs)})


@router.get("/data", response_class=HTMLResponse)
async def data_page(request: Request):
    return _shell(request, "landing", "Data library")


@router.get("/data/country/{code}", response_class=HTMLResponse)
async def country_page(request: Request, code: str):
    try:
        code = library.country_code(code)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _shell(request, "country", COUNTRY_NAMES[code] + " indicators", country=code)


@router.get("/data/indicator/{country}/{canonical}", response_class=HTMLResponse)
async def indicator_page(request: Request, country: str, canonical: str):
    try:
        async with get_sessionmaker()() as session:
            meta = await library.indicator(session, country, canonical)
    except (ValueError, LookupError) as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception:
        logger.exception("Indicator metadata unavailable")
        return _shell(request, "indicator", canonical.replace("_", " ").title(), country=country.upper(), canonical=canonical)
    return _shell(request, "indicator", meta["display_name"], country=meta["country_code"], canonical=canonical)


@router.get("/data/sources", response_class=HTMLResponse)
async def sources_page(request: Request):
    return _shell(request, "sources", "Source health & data quality")


@router.get("/data/datasets/{name}", response_class=HTMLResponse)
async def dataset_page(request: Request, name: str):
    try:
        meta = datasets.dataset_spec(name)
        datasets.query(name, **_dataset_filters(_params(request)))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _shell(request, "dataset", meta["title"], name=name)


@router.get("/data/content", response_class=HTMLResponse)
async def content(request: Request, view: str, country: str | None = None, canonical: str | None = None,
                  name: str | None = None):
    if view not in {"landing", "country", "indicator", "sources", "dataset"}:
        raise HTTPException(404, "Unknown data view")
    p = _params(request)
    try:
        if view in {"country", "indicator"}:
            country = library.country_code(country or "")
        if view == "dataset":
            datasets.query(name, **_dataset_filters(p))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    # Include policy state so turning off personal-use datasets cannot reuse a prior cached page.
    policy = datasets.load_config()
    key = ("data", view, country, canonical, name, tuple(request.query_params.multi_items()), str(policy))
    cached = desks.cache_get(key)
    if cached is not None:
        return HTMLResponse(cached, headers={"Cache-Control": "private, max-age=60"})
    state = "available"
    try:
        ctx = await _content(request, view, country, canonical, name, p)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        logger.exception("Data view %s unavailable", view)
        ctx, state = {}, "unavailable"
    args = list(request.query_params.multi_items())
    path = (f"/data/indicator/{country}/{quote(canonical or '', safe='')}" if view == "indicator"
            else f"/data/datasets/{name}" if view == "dataset" else "/data")
    def page_url(offset):
        return path + "?" + urlencode(
            [(k, v) for k, v in args if k not in {"view", "country", "canonical", "name", "offset"}] + [("offset", offset)])
    html = templates.get_template("data/content.html").render(
        view=view, ctx=ctx, state=state, p=p, country=country, canonical=canonical, name=name,
        countries=COUNTRY_NAMES, themes=library.THEMES, currencies=(*dict.fromkeys(CURRENCIES.values()), "SEK"),
        query=urlencode([(k, v) for k, v in args if k in {"start", "end", "series", "currency"}]),
        next_url=page_url(p["offset"] + 500), previous_url=page_url(max(0, p["offset"] - 500)))
    if state == "available":
        desks.cache_set(key, html, ttl=60)
    return HTMLResponse(html, headers={"Cache-Control": "private, max-age=60" if state == "available" else "no-store"})


@router.get("/data/indicator/{country}/{canonical}/csv")
async def history_csv(request: Request, country: str, canonical: str):
    p = _params(request)
    try:
        async with get_sessionmaker()() as session:
            return await library.export_history(session, country, canonical, **_dates(p))
    except (ValueError, LookupError) as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/data/datasets/{name}/csv")
async def dataset_csv(request: Request, name: str):
    try:
        return datasets.export_dataset(name, **_dataset_filters(_params(request)))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/country/{code}", include_in_schema=False)
async def legacy_country(request: Request, code: str):
    try:
        code = library.country_code(code)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    suffix = "?" + request.url.query if request.url.query else ""
    return RedirectResponse(f"/data/country/{code}" + suffix, status_code=301)


@router.get("/country/{country}/indicator/{canonical}", include_in_schema=False)
async def legacy_indicator(request: Request, country: str, canonical: str):
    try:
        country = library.country_code(country)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    suffix = "?" + request.url.query if request.url.query else ""
    return RedirectResponse(f"/data/indicator/{country}/{quote(canonical, safe='')}" + suffix, status_code=301)
