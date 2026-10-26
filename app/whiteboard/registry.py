"""Live widget sources and the per-currency indicator library."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.processing.cb_preferred_score import CB_WATCHLIST
from app.services import cb_tracking, desk_panels, positioning, rate_probability
from app.services.event_innovation_feed import build_event_innovation_feed, resolve_feed_filters
from app.settings import SUPPORTED_CURRENCIES
from app.whiteboard.categories import CATEGORY_ORDER, library_category

DataFn = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

COUNTRY = dict(zip(SUPPORTED_CURRENCIES, ("US", "EU", "UK", "JP", "CH", "CA", "AU", "NZ")))
BANK = dict(zip(SUPPORTED_CURRENCIES, ("FED", "ECB", "BOE", "BOJ", "SNB", "BOC", "RBA", "RBNZ")))
YIELD_COUNTRY = {**COUNTRY, "EUR": "DE"}


@dataclass(frozen=True)
class WidgetSpec:
    widget_type: str
    category: str
    title: str
    default_w: int
    default_h: int
    data_fn: DataFn


def _payload(title: str, unit: str = "", latest: dict | None = None,
             series: list | None = None, status_chip: str | None = None,
             surprise_sigma: float | None = None) -> dict[str, Any]:
    return json.loads(json.dumps(
        {"title": title, "unit": unit, "latest": latest or {}, "series": series or [],
         "surprise_sigma": surprise_sigma, "status_chip": status_chip},
        default=str, allow_nan=False))


def _pending(title: str) -> dict[str, Any]:
    return _payload(title, status_chip="data source pending")


def _placeholder(title: str) -> DataFn:
    async def data_fn(_currency: str, _config: dict[str, Any]) -> dict[str, Any]:
        return _pending(title)
    return data_fn


async def _indicator(currency: str, config: dict[str, Any]) -> dict[str, Any]:
    code = config.get("indicator_code")
    country = config.get("country_code") or COUNTRY[currency]
    if not isinstance(code, str) or country not in await _countries(currency):
        return _pending("Indicator unavailable")
    since = date.today() - timedelta(days=730)
    points = (await desk_panels.indicator_history(country, [code], since)).get(code, [])
    sigma = None
    title = config.get("title") or code.replace("_", " ").title()
    async with get_sessionmaker()() as session:
        feed = await build_event_innovation_feed(
            session, resolve_feed_filters(days=90, country_code=country))
    for row in feed["rows"]:
        if row.get("country_code") == country:
            children = row.get("children") or []
            if row.get("name") == title or any(child.get("name") == title for child in children):
                sigma = row.get("initial")
                break
    latest = {"value": points[-1][1], "date": points[-1][0].isoformat()} if points else {}
    return _payload(title, latest=latest,
                    series=[[d.isoformat(), v] for d, v in points], surprise_sigma=sigma,
                    status_chip="live" if points else "unavailable")


async def _tracking(currency: str, _config: dict[str, Any]) -> dict[str, Any]:
    if currency != "USD":
        return _pending("SEP vs Actual")
    result = await cb_tracking.get_tracking()
    return _payload("SEP vs Actual", latest=result.get("variables", {}),
                    status_chip=result.get("status", "live"))


async def _rate_path(currency: str, _config: dict[str, Any]) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        result = await rate_probability.get_rate_probability_view(BANK[currency], session)
    return _payload("Rate Path", "%", {"value": result.get("current_rate"),
                    "date": str(result.get("current_rate_as_of") or "")},
                    [[str(m["meeting_at"]), m.get("implied_rate")] for m in result.get("meetings", [])],
                    "live" if result.get("market_data", {}).get("available") else "unavailable")


async def _yields(currency: str, _config: dict[str, Any], curve: bool = False) -> dict[str, Any]:
    data = await desk_panels.yield_series(YIELD_COUNTRY[currency], ["2Y", "10Y"],
                                          date.today() - timedelta(days=730))
    two, ten = data.get("2Y", {}), data.get("10Y", {})
    dates = sorted(set(two) | set(ten))
    if curve:
        series = [[d.isoformat(), ten[d] - two[d]] for d in dates if d in two and d in ten]
        return _payload("Curve 2s10s", "bp", {"value": series[-1][1] * 100,
                        "date": series[-1][0]} if series else {},
                        [[d, v * 100] for d, v in series], "live" if series else "unavailable")
    series = {"2Y": [[d.isoformat(), two[d]] for d in sorted(two)],
              "10Y": [[d.isoformat(), ten[d]] for d in sorted(ten)]}
    return _payload("2Y / 10Y Yields", "%", {"value": {k: v[-1][1] for k, v in series.items() if v}},
                    series, "live" if two or ten else "unavailable")


async def _yield_levels(currency: str, config: dict[str, Any]) -> dict[str, Any]:
    return await _yields(currency, config)


async def _yield_curve(currency: str, config: dict[str, Any]) -> dict[str, Any]:
    return await _yields(currency, config, curve=True)


async def _cot(currency: str, _config: dict[str, Any]) -> dict[str, Any]:
    position = await positioning.get_currency_positioning(currency)
    crowding = await positioning.get_crowding("3y")
    crowd = next((r for r in crowding.get("currencies", []) if r.get("currency") == currency), {})
    series = {name: [[r["report_date"], r["net"]] for r in item.get("history", [])]
              for name, item in position.get("categories", {}).items()}
    return _payload("COT Positioning", "contracts", crowd, series,
                    "live" if series else "unavailable")


WIDGET_REGISTRY: dict[str, WidgetSpec] = {
    spec.widget_type: spec for spec in (
        WidgetSpec("cb_statement", "central_bank", "CB Statement", 340, 240, _placeholder("CB Statement")),
        WidgetSpec("expected_vs_delivered", "central_bank", "Expected vs Delivered", 340, 240, _placeholder("Expected vs Delivered")),
        WidgetSpec("sep_vs_actual", "central_bank", "SEP vs Actual", 420, 280, _tracking),
        WidgetSpec("rate_path_zq", "central_bank", "Rate Path", 420, 280, _rate_path),
        WidgetSpec("speaker_tone", "central_bank", "Speaker Tone", 340, 240, _placeholder("Speaker Tone")),
        WidgetSpec("yields_2y10y", "rates", "2Y / 10Y Yields", 420, 280, _yield_levels),
        WidgetSpec("curve_2s10s", "rates", "Curve 2s10s", 420, 280, _yield_curve),
        WidgetSpec("real_yields_bei", "rates", "Real Yields / BEI", 420, 280, _placeholder("Real Yields / BEI")),
        WidgetSpec("cot_positioning", "flow", "COT Positioning", 420, 280, _cot),
        WidgetSpec("research_notes", "flow", "Research Notes", 340, 240, _placeholder("Research Notes")),
        WidgetSpec("indicator", "other", "Indicator", 420, 280, _indicator),
    )
}


async def _countries(currency: str) -> set[str]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("SELECT code FROM countries WHERE currency_code = :currency"),
                                     {"currency": currency})
        return {row.code for row in rows}


async def indicator_library(currency: str) -> list[dict[str, Any]]:
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("Unsupported currency")
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""
            SELECT i.country_code, i.canonical_name, i.display_name, i.primary_category,
                   i.secondary_categories
            FROM indicators i JOIN countries c ON c.code = i.country_code
            WHERE c.currency_code = :currency
              AND EXISTS (SELECT 1 FROM indicator_releases r WHERE r.indicator_id = i.id)
            ORDER BY i.country_code, i.canonical_name
        """), {"currency": currency})
        indicators = rows.mappings().all()
    preferred = {(i.source_country or country, i.canonical_name)
                 for country, entries in CB_WATCHLIST.items() for i in entries}
    return [{"widget_type": "indicator", "category": library_category(row["primary_category"]),
             "title": row["display_name"], "config": {"indicator_code": row["canonical_name"],
             "country_code": row["country_code"], "title": row["display_name"]},
             "secondary_categories": list(row["secondary_categories"] or []),
             "cb_preferred": (row["country_code"], row["canonical_name"]) in preferred}
            for row in indicators]


def search_library(items: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    term = query.strip().casefold()
    if not term:
        return items
    return [item for item in items if term in item["title"].casefold()
            or term in item["config"]["indicator_code"].casefold()
            or (item["config"]["indicator_code"].startswith("employment_cost_")
                and term in "earnings wages compensation")
            or any(term in category.casefold() for category in item["secondary_categories"])]


async def widget_data(widget_type: str, currency: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("Unsupported currency")
    result = await WIDGET_REGISTRY[widget_type].data_fn(currency, config or {})
    json.dumps(result, allow_nan=False)
    return result
