"""Server-rendered page routes for the Step 5 frontend."""

from __future__ import annotations

import asyncio
import csv
import calendar
from datetime import date, timedelta, timezone
from io import BytesIO
from io import StringIO
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.public import (
    get_country,
    get_country_detail_payload,
    get_indicator_by_country_and_name,
    list_biggest_surprises,
    list_country_summaries,
)
from app.db.models import Country, Indicator, IndicatorRelease, IngestionRun
from app.db.session import get_session
from app.ingestion.eodhd_client import EODHDAuthError, EODHDClient, EODHDError
from app.processing.bank_research import load_bank_research_index

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(Path("app/web/templates")))

ALL_CATEGORY_TABS = (
    "Inflation",
    "Growth",
    "Labor",
    "Monetary Policy",
    "Trade",
    "Sentiment",
    "Housing",
    "Other",
)
TAB_SLUGS = {category.lower(): category for category in ALL_CATEGORY_TABS}
CALENDAR_WINDOWS = (1, 3, 7, 14)
CURRENCY_METER_ORDER = ("USD", "EUR", "JPY", "GBP", "AUD", "CAD", "CHF", "NZD")
CURRENCY_METER_LOOKBACKS = (
    ("Current", 0),
    ("1M Ago", 1),
    ("3M Ago", 3),
    ("6M Ago", 6),
)
YIELD_HISTORY_DAYS = 70
YIELD_BASE_CURRENCY = "USD"
YIELD_BENCHMARKS = (
    {"currency": "USD", "country_code": "US", "label": "US 10Y", "symbol": "US10Y.GBOND"},
    {"currency": "EUR", "country_code": "DE", "label": "Germany 10Y", "symbol": "DE10Y.GBOND"},
    {"currency": "GBP", "country_code": "UK", "label": "UK 10Y", "symbol": "UK10Y.GBOND"},
    {"currency": "JPY", "country_code": "JP", "label": "Japan 10Y", "symbol": "JP10Y.GBOND"},
    {"currency": "AUD", "country_code": "AU", "label": "Australia 10Y", "symbol": "AU10Y.GBOND"},
    {"currency": "CAD", "country_code": "CA", "label": "Canada 10Y", "symbol": "CA10Y.GBOND"},
    {"currency": "CHF", "country_code": "CH", "label": "Switzerland 10Y", "symbol": "SW10Y.GBOND"},
    {"currency": "NZD", "country_code": "NZ", "label": "New Zealand 10Y", "symbol": "NZ10Y.GBOND"},
)
YIELD_PAIR_DEFS = (
    ("EUR/USD", "EUR", "USD"),
    ("GBP/USD", "GBP", "USD"),
    ("USD/JPY", "USD", "JPY"),
    ("AUD/USD", "AUD", "USD"),
    ("USD/CAD", "USD", "CAD"),
    ("USD/CHF", "USD", "CHF"),
    ("NZD/USD", "NZD", "USD"),
)
COUNTRY_FLAGS = {
    "US": "🇺🇸",
    "EU": "🇪🇺",
    "UK": "🇬🇧",
    "JP": "🇯🇵",
    "AU": "🇦🇺",
    "NZ": "🇳🇿",
    "CA": "🇨🇦",
    "CH": "🇨🇭",
}


def _flag_for_country(country_code: str) -> str:
    return COUNTRY_FLAGS.get(country_code, "🏳️")


def _format_value(value: float | None, unit: str | None) -> str:
    if value is None:
        return "N/A"
    formatted = f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{formatted} {unit}".strip() if unit else formatted


def _trend_symbol(values: list[float | None]) -> str:
    comparable = [value for value in values if value is not None]
    if len(comparable) < 2:
        return "→"
    latest = comparable[-1]
    previous = comparable[-2]
    if latest > previous:
        return "▲"
    if latest < previous:
        return "▼"
    return "→"


def _meter_percent(value: float | None) -> int:
    if value is None:
        return 50
    clamped = max(-2.5, min(2.5, float(value)))
    return int(round(((clamped + 2.5) / 5) * 100))


def _meter_color_class(color: str | None) -> str:
    if color == "green":
        return "is-positive"
    if color == "red":
        return "is-negative"
    return "is-neutral"


def _format_score(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2f}"


def _score_color_class(value: float | None) -> str:
    if value is None:
        return "is-neutral"
    if value >= 0.25:
        return "is-positive"
    if value <= -0.25:
        return "is-negative"
    return "is-neutral"


def _spread_color_class(value: float | None) -> str:
    if value is None or abs(value) < 5:
        return "is-neutral"
    if value > 0:
        return "is-positive"
    return "is-negative"


def _format_bp(value: float | None, decimals: int = 0) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.{decimals}f} bp"


def _format_yield(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def _subtract_months(value: date, months: int) -> date:
    month_index = value.month - 1 - months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _category_meter_score(value: float | None, direction: float | None) -> float | None:
    if value is None and direction is None:
        return None
    if direction is None:
        return value
    if value is None:
        return direction
    return (float(value) * 0.30) + (float(direction) * 0.70)


def _format_int(value: int | None) -> str:
    return f"{int(value or 0):,}"


def _percent(value: int | None, total: int | None) -> int:
    if not value or not total:
        return 0
    return int(round((value / total) * 100))


def _report_datetime(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _build_analytics_csv(analytics: dict[str, Any]) -> str:
    output = StringIO()
    writer = csv.writer(output)

    writer.writerow(["Macro Dashboard Data Summary"])
    writer.writerow(["Generated At", _report_datetime(_now())])
    writer.writerow(["First Release", _report_datetime(analytics["first_release_at"])])
    writer.writerow([
        "Latest Observed Release",
        _report_datetime(analytics["latest_observed_release_at"]),
    ])
    writer.writerow([])

    writer.writerow(["Overview"])
    writer.writerow(["Metric", "Value", "Detail"])
    for stat in analytics["headline_stats"]:
        writer.writerow([stat["label"], stat["value"], stat["detail"]])
    writer.writerow([])

    writer.writerow(["Completeness"])
    writer.writerow(["Metric", "Value", "Share"])
    for item in analytics["data_quality"]:
        writer.writerow([item["label"], item["value"], f"{item['share']}%"])
    writer.writerow([])

    writer.writerow(["Frequency Mix"])
    writer.writerow(["Frequency", "Indicators", "Share"])
    for item in analytics["frequency_rows"]:
        writer.writerow([item["label"], item["indicator_count"], f"{item['share']}%"])
    writer.writerow([])

    writer.writerow(["Importance Levels"])
    writer.writerow(["Importance", "Indicators", "Share"])
    for item in analytics["importance_rows"]:
        writer.writerow([item["label"], item["indicator_count"], f"{item['share']}%"])
    writer.writerow([])

    writer.writerow(["Category Coverage"])
    writer.writerow(["Category", "Indicators", "Releases", "Share", "Latest Observed"])
    for row in analytics["category_rows"]:
        writer.writerow([
            row["category"],
            row["indicator_count"],
            row["release_count"],
            f"{row['share']}%",
            _report_datetime(row["latest_release_at"]),
        ])
    writer.writerow([])

    writer.writerow(["Country Depth"])
    writer.writerow([
        "Country Code",
        "Country",
        "Currency",
        "Indicators",
        "Releases",
        "Share",
        "Latest Observed",
    ])
    for row in analytics["country_rows"]:
        writer.writerow([
            row["code"],
            row["name"],
            row["currency_code"],
            row["indicator_count"],
            row["release_count"],
            f"{row['share']}%",
            _report_datetime(row["latest_release_at"]),
        ])
    writer.writerow([])

    writer.writerow(["Recent Ingestion Runs"])
    writer.writerow([
        "Run ID",
        "Run Type",
        "Status",
        "Started At",
        "Finished At",
        "Inserted",
        "Updated",
        "API Calls",
    ])
    for run in analytics["recent_runs"]:
        writer.writerow([
            run["id"],
            run["run_type"],
            run["status"],
            _report_datetime(run["started_at"]),
            _report_datetime(run["finished_at"]),
            run["events_inserted"],
            run["events_updated"],
            run["api_calls_used"],
        ])

    return output.getvalue()


def _build_analytics_pdf(analytics: dict[str, Any]) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.utils import simpleSplit
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise RuntimeError("PDF export requires reportlab to be installed.") from exc

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    bg = colors.HexColor("#071018")
    panel = colors.HexColor("#12202b")
    panel_2 = colors.HexColor("#162733")
    border = colors.HexColor("#355063")
    text_color = colors.HexColor("#dce8f2")
    muted = colors.HexColor("#8fa5b5")
    accent = colors.HexColor("#50b5ff")
    positive = colors.HexColor("#23c483")
    warning = colors.HexColor("#f3ba63")

    def start_page(title: str | None = None) -> None:
        pdf.setFillColor(bg)
        pdf.rect(0, 0, width, height, stroke=0, fill=1)
        pdf.setFillColor(text_color)
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawString(42, height - 46, "Macro Dashboard Data Summary")
        pdf.setFillColor(muted)
        pdf.setFont("Helvetica", 8)
        pdf.drawRightString(width - 42, height - 42, f"Generated {_now().strftime('%Y-%m-%d %H:%M UTC')}")
        if title:
            pdf.setFillColor(accent)
            pdf.setFont("Helvetica-Bold", 8)
            pdf.drawString(42, height - 70, title.upper())

    def round_rect(x: float, y: float, w: float, h: float, fill: Any = panel) -> None:
        pdf.setStrokeColor(border)
        pdf.setFillColor(fill)
        pdf.roundRect(x, y, w, h, 8, stroke=1, fill=1)

    def small_label(x: float, y: float, label: str) -> None:
        pdf.setFillColor(accent)
        pdf.setFont("Helvetica-Bold", 7)
        pdf.drawString(x, y, label.upper())

    def draw_wrapped(text: str, x: float, y: float, max_width: float, size: int = 9) -> float:
        pdf.setFillColor(muted)
        pdf.setFont("Helvetica", size)
        lines = simpleSplit(text, "Helvetica", size, max_width)
        for line in lines[:4]:
            pdf.drawString(x, y, line)
            y -= size + 3
        return y

    def draw_bar(x: float, y: float, w: float, h: float, pct: int, color: Any = positive) -> None:
        pdf.setFillColor(colors.Color(1, 1, 1, alpha=0.08))
        pdf.roundRect(x, y, w, h, h / 2, stroke=0, fill=1)
        pdf.setFillColor(color)
        pdf.roundRect(x, y, max(2, w * max(0, min(pct, 100)) / 100), h, h / 2, stroke=0, fill=1)

    def draw_kpi(x: float, y: float, w: float, h: float, stat: dict[str, str]) -> None:
        round_rect(x, y, w, h, panel)
        pdf.setFillColor(accent)
        pdf.rect(x, y, 4, h, stroke=0, fill=1)
        small_label(x + 14, y + h - 20, stat["label"])
        pdf.setFillColor(text_color)
        pdf.setFont("Helvetica-Bold", 23)
        pdf.drawString(x + 14, y + 38, stat["value"])
        pdf.setFillColor(muted)
        pdf.setFont("Helvetica", 8)
        pdf.drawString(x + 14, y + 20, stat["detail"])

    def draw_donut(x: float, y: float, size: float, pct: int) -> None:
        pdf.setFillColor(colors.Color(1, 1, 1, alpha=0.12))
        pdf.circle(x + size / 2, y + size / 2, size / 2, stroke=0, fill=1)
        pdf.setFillColor(positive)
        pdf.wedge(x, y, x + size, y + size, 90, -360 * pct / 100, stroke=0, fill=1)
        pdf.setFillColor(panel)
        pdf.circle(x + size / 2, y + size / 2, size * 0.31, stroke=0, fill=1)
        pdf.setFillColor(text_color)
        pdf.setFont("Helvetica-Bold", 20)
        pdf.drawCentredString(x + size / 2, y + size / 2 - 7, f"{pct}%")

    def draw_bar_list(x: float, y: float, w: float, rows: list[dict[str, Any]], value_key: str) -> float:
        for row in rows:
            pdf.setFillColor(text_color)
            pdf.setFont("Helvetica-Bold", 9)
            pdf.drawString(x, y, str(row["label"]))
            pdf.setFillColor(muted)
            pdf.setFont("Helvetica", 8)
            pdf.drawRightString(x + w, y, f"{row[value_key]} | {row['share']}%")
            draw_bar(x, y - 12, w, 6, int(row["share"]))
            y -= 28
        return y

    start_page()
    small_label(42, height - 86, "Data Analytics")
    pdf.setFillColor(text_color)
    pdf.setFont("Helvetica-Bold", 28)
    pdf.drawString(42, height - 122, "Visual inventory of the macro warehouse")
    latest = analytics["latest_observed_release_at"]
    latest_text = latest.strftime("%b %d, %Y") if latest else "No releases yet"
    draw_wrapped(
        f"Latest observed print: {latest_text}. This report mirrors the dashboard view with "
        "coverage, category mix, market depth, and ingestion health.",
        42,
        height - 146,
        390,
        10,
    )

    kpi_w = 124
    kpi_y = height - 266
    for index, stat in enumerate(analytics["headline_stats"]):
        draw_kpi(42 + index * (kpi_w + 10), kpi_y, kpi_w, 86, stat)

    mapped = analytics["data_quality"][0] if analytics["data_quality"] else {"share": 0, "value": "0"}
    round_rect(42, 408, 528, 120, panel)
    draw_donut(64, 429, 78, int(mapped["share"]))
    small_label(162, 496, "Mapped Coverage")
    pdf.setFillColor(text_color)
    pdf.setFont("Helvetica-Bold", 17)
    pdf.drawString(162, 474, f"{mapped['value']} mapped releases")
    draw_wrapped(
        f"{analytics['total_releases']:,} total stored releases. Unmapped coverage highlights "
        "where canonical mapping work can unlock more usable history.",
        162,
        456,
        360,
        9,
    )

    round_rect(42, 236, 252, 140, panel)
    small_label(60, 350, "Completeness")
    draw_bar_list(60, 326, 198, analytics["data_quality"], "value")

    round_rect(318, 236, 252, 140, panel)
    small_label(336, 350, "Frequency Mix")
    draw_bar_list(336, 326, 198, analytics["frequency_rows"], "indicator_count")

    round_rect(42, 88, 528, 110, panel)
    small_label(60, 170, "Importance Levels")
    priority_x = 60
    for item in analytics["importance_rows"]:
        round_rect(priority_x, 112, 150, 44, panel_2)
        pdf.setFillColor(text_color)
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(priority_x + 12, 136, str(item["label"]))
        pdf.setFillColor(muted)
        pdf.setFont("Helvetica", 9)
        pdf.drawString(priority_x + 12, 120, f"{item['indicator_count']} indicators | {item['share']}%")
        priority_x += 166

    pdf.showPage()
    start_page("Category Coverage")
    y = height - 104
    for idx, row in enumerate(analytics["category_rows"], start=1):
        x = 42 if idx % 2 == 1 else 314
        if idx % 2 == 1 and idx > 1:
            y -= 102
        round_rect(x, y - 74, 256, 78, panel)
        pdf.setFillColor(text_color)
        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(x + 14, y - 20, row["category"])
        pdf.setFillColor(accent)
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawRightString(x + 238, y - 20, f"{row['share']}%")
        draw_bar(x + 14, y - 38, 214, 7, int(row["share"]))
        pdf.setFillColor(muted)
        pdf.setFont("Helvetica", 8)
        pdf.drawString(
            x + 14,
            y - 58,
            f"{row['indicator_count']:,} indicators | {row['release_count']:,} releases",
        )
        if y < 140 and idx < len(analytics["category_rows"]):
            pdf.showPage()
            start_page("Category Coverage")
            y = height - 104

    pdf.showPage()
    start_page("Country Depth")
    y = height - 104
    for idx, row in enumerate(analytics["country_rows"], start=1):
        x = 42 if idx % 2 == 1 else 314
        if idx % 2 == 1 and idx > 1:
            y -= 82
        round_rect(x, y - 58, 256, 62, panel)
        pdf.setFillColor(text_color)
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(x + 14, y - 18, f"{row['code']} | {row['currency_code']} | {row['name']}")
        pdf.setFillColor(muted)
        pdf.setFont("Helvetica", 8)
        pdf.drawString(
            x + 14,
            y - 35,
            f"{row['release_count']:,} releases | {row['indicator_count']:,} indicators",
        )
        draw_bar(x + 14, y - 48, 214, 6, int(row["share"]), accent)
        if y < 120 and idx < len(analytics["country_rows"]):
            pdf.showPage()
            start_page("Country Depth")
            y = height - 104

    pdf.showPage()
    start_page("Recent Ingestion Runs")
    y = height - 110
    for run in analytics["recent_runs"]:
        round_rect(42, y - 44, 528, 52, panel)
        pdf.setFillColor(text_color)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(58, y - 14, f"{run['run_type']} #{run['id']}")
        pdf.setFillColor(muted)
        pdf.setFont("Helvetica", 8)
        started = run["started_at"].strftime("%b %d, %Y %H:%M") if run["started_at"] else "Unknown"
        pdf.drawString(58, y - 30, started)
        pdf.drawRightString(
            554,
            y - 22,
            (
                f"{run['status']} | {run['events_inserted']:,} inserted | "
                f"{run['events_updated']:,} updated | {run['api_calls_used']:,} calls"
            ),
        )
        y -= 64

    pdf.save()
    return buffer.getvalue()


async def _build_analytics_snapshot(session: AsyncSession) -> dict[str, Any]:
    now = _now()
    totals_q = await session.execute(
        select(
            func.count(IndicatorRelease.id).label("total_releases"),
            func.count(IndicatorRelease.indicator_id).label("mapped_releases"),
            func.count(IndicatorRelease.actual).label("actual_releases"),
            func.count(IndicatorRelease.estimate).label("estimated_releases"),
            func.min(IndicatorRelease.released_at).label("first_release_at"),
            func.max(IndicatorRelease.released_at)
            .filter(IndicatorRelease.released_at <= now)
            .label("latest_observed_release_at"),
        )
    )
    totals = totals_q.one()

    country_total = await session.scalar(select(func.count(Country.code)))
    indicator_total = await session.scalar(select(func.count(Indicator.id)))
    upcoming_release_total = await session.scalar(
        select(func.count(IndicatorRelease.id)).where(IndicatorRelease.released_at > now)
    )
    ingestion_run_total = await session.scalar(select(func.count(IngestionRun.id)))

    total_releases = int(totals.total_releases or 0)
    mapped_releases = int(totals.mapped_releases or 0)
    unmapped_releases = max(total_releases - mapped_releases, 0)
    actual_releases = int(totals.actual_releases or 0)
    estimated_releases = int(totals.estimated_releases or 0)

    category_q = await session.execute(
        select(
            Indicator.primary_category.label("category"),
            func.count(func.distinct(Indicator.id)).label("indicator_count"),
            func.count(IndicatorRelease.id).label("release_count"),
            func.max(IndicatorRelease.released_at)
            .filter(IndicatorRelease.released_at <= now)
            .label("latest_release_at"),
        )
        .outerjoin(IndicatorRelease, IndicatorRelease.indicator_id == Indicator.id)
        .group_by(Indicator.primary_category)
        .order_by(func.count(IndicatorRelease.id).desc(), Indicator.primary_category.asc())
    )
    category_rows = [
        {
            "category": row.category or "Uncategorized",
            "indicator_count": int(row.indicator_count or 0),
            "release_count": int(row.release_count or 0),
            "latest_release_at": row.latest_release_at,
            "share": _percent(int(row.release_count or 0), total_releases),
        }
        for row in category_q.all()
    ]

    country_q = await session.execute(
        select(
            Country.code,
            Country.name,
            Country.currency_code,
            func.count(func.distinct(Indicator.id)).label("indicator_count"),
            func.count(IndicatorRelease.id).label("release_count"),
            func.max(IndicatorRelease.released_at)
            .filter(IndicatorRelease.released_at <= now)
            .label("latest_release_at"),
        )
        .outerjoin(Indicator, Indicator.country_code == Country.code)
        .outerjoin(IndicatorRelease, IndicatorRelease.indicator_id == Indicator.id)
        .group_by(Country.code, Country.name, Country.currency_code)
        .order_by(func.count(IndicatorRelease.id).desc(), Country.code.asc())
    )
    country_rows = [
        {
            "code": row.code,
            "name": row.name,
            "currency_code": row.currency_code,
            "flag": _flag_for_country(row.code),
            "indicator_count": int(row.indicator_count or 0),
            "release_count": int(row.release_count or 0),
            "latest_release_at": row.latest_release_at,
            "share": _percent(int(row.release_count or 0), total_releases),
        }
        for row in country_q.all()
    ]

    frequency_q = await session.execute(
        select(Indicator.frequency, func.count(Indicator.id).label("indicator_count"))
        .group_by(Indicator.frequency)
        .order_by(func.count(Indicator.id).desc(), Indicator.frequency.asc())
    )
    frequency_rows = [
        {
            "label": (row.frequency or "unknown").title(),
            "indicator_count": int(row.indicator_count or 0),
            "share": _percent(int(row.indicator_count or 0), int(indicator_total or 0)),
        }
        for row in frequency_q.all()
    ]

    importance_labels = {1: "High", 2: "Medium", 3: "Low"}
    importance_q = await session.execute(
        select(Indicator.importance, func.count(Indicator.id).label("indicator_count"))
        .group_by(Indicator.importance)
        .order_by(Indicator.importance.asc())
    )
    importance_rows = [
        {
            "label": importance_labels.get(row.importance, f"Level {row.importance}"),
            "indicator_count": int(row.indicator_count or 0),
            "share": _percent(int(row.indicator_count or 0), int(indicator_total or 0)),
        }
        for row in importance_q.all()
    ]

    recent_runs_q = await session.execute(
        select(IngestionRun)
        .order_by(desc(IngestionRun.started_at), desc(IngestionRun.id))
        .limit(5)
    )
    recent_runs = [
        {
            "id": run.id,
            "run_type": run.run_type,
            "status": run.status,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "events_inserted": int(run.events_inserted or 0),
            "events_updated": int(run.events_updated or 0),
            "api_calls_used": int(run.api_calls_used or 0),
        }
        for run in recent_runs_q.scalars().all()
    ]

    headline_stats = [
        {"label": "Countries", "value": _format_int(country_total), "detail": "tracked markets"},
        {"label": "Indicators", "value": _format_int(indicator_total), "detail": "canonical series"},
        {"label": "Releases", "value": _format_int(total_releases), "detail": "stored prints"},
        {"label": "Upcoming", "value": _format_int(upcoming_release_total), "detail": "scheduled rows"},
    ]

    data_quality = [
        {
            "label": "Mapped Releases",
            "value": _format_int(mapped_releases),
            "share": _percent(mapped_releases, total_releases),
        },
        {
            "label": "Unmapped Releases",
            "value": _format_int(unmapped_releases),
            "share": _percent(unmapped_releases, total_releases),
        },
        {
            "label": "Actual Values",
            "value": _format_int(actual_releases),
            "share": _percent(actual_releases, total_releases),
        },
        {
            "label": "Estimate Values",
            "value": _format_int(estimated_releases),
            "share": _percent(estimated_releases, total_releases),
        },
    ]

    return {
        "headline_stats": headline_stats,
        "data_quality": data_quality,
        "category_rows": category_rows,
        "country_rows": country_rows,
        "frequency_rows": frequency_rows,
        "importance_rows": importance_rows,
        "recent_runs": recent_runs,
        "total_releases": total_releases,
        "ingestion_run_total": int(ingestion_run_total or 0),
        "first_release_at": totals.first_release_at,
        "latest_observed_release_at": totals.latest_observed_release_at,
    }


async def _build_currency_stance_dashboard(
    session: AsyncSession,
) -> dict[int, list[dict[str, Any]]]:
    try:
        result = await session.execute(
            text(
                """
                WITH latest AS (
                    SELECT window_months, max(date) AS date
                    FROM processed.currency_stance
                    GROUP BY window_months
                )
                SELECT
                    s.date,
                    s.country_code,
                    s.currency,
                    s.window_months,
                    s.inflation_score,
                    s.labor_score,
                    s.growth_score,
                    s.overall_stance_score,
                    s.overall_stance_label,
                    s.trend_label,
                    s.confidence,
                    s.meter_color,
                    s.inflation_meter_color,
                    s.labor_meter_color,
                    s.growth_meter_color,
                    r.rank_strongest
                FROM processed.currency_stance s
                JOIN processed.currency_stance_rankings r
                    ON r.date = s.date
                    AND r.currency = s.currency
                    AND r.window_months = s.window_months
                JOIN latest l
                    ON l.date = s.date
                    AND l.window_months = s.window_months
                ORDER BY s.window_months, r.rank_strongest
                """
            )
        )
    except Exception:
        return {}

    rows_by_window: dict[int, list[dict[str, Any]]] = {}
    for row in result.mappings().all():
        category_meters = [
            {
                "label": "Inflation",
                "short_label": "I",
                "score": _format_score(row["inflation_score"]),
                "percent": _meter_percent(row["inflation_score"]),
                "color_class": _meter_color_class(row["inflation_meter_color"]),
            },
            {
                "label": "Labor",
                "short_label": "L",
                "score": _format_score(row["labor_score"]),
                "percent": _meter_percent(row["labor_score"]),
                "color_class": _meter_color_class(row["labor_meter_color"]),
            },
            {
                "label": "Growth",
                "short_label": "G",
                "score": _format_score(row["growth_score"]),
                "percent": _meter_percent(row["growth_score"]),
                "color_class": _meter_color_class(row["growth_meter_color"]),
            },
        ]
        window = int(row["window_months"])
        rows_by_window.setdefault(window, []).append(
            {
                "date": row["date"],
                "country_code": row["country_code"],
                "currency": row["currency"],
                "rank": row["rank_strongest"],
                "score": _format_score(row["overall_stance_score"]),
                "score_percent": _meter_percent(row["overall_stance_score"]),
                "label": str(row["overall_stance_label"]).replace("_", " ").title(),
                "trend": str(row["trend_label"]).title(),
                "confidence": str(row["confidence"]).title(),
                "color_class": _meter_color_class(row["meter_color"]),
                "category_meters": category_meters,
            }
        )
    return rows_by_window


async def _build_fundamental_currency_meter(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    currency_list = ", ".join(f"'{currency}'" for currency in CURRENCY_METER_ORDER)
    try:
        result = await session.execute(
            text(
                f"""
                SELECT
                    date,
                    country_code,
                    currency,
                    inflation_score,
                    labor_score,
                    growth_score,
                    inflation_direction,
                    labor_direction,
                    growth_direction,
                    overall_stance_score,
                    overall_stance_label
                FROM processed.currency_stance
                WHERE window_months = 3
                    AND currency IN ({currency_list})
                ORDER BY currency, date
                """
            )
        )
    except Exception:
        return []

    rows_by_currency: dict[str, list[dict[str, Any]]] = {}
    for row in result.mappings().all():
        rows_by_currency.setdefault(str(row["currency"]), []).append(dict(row))

    metrics = (
        ("Inflation", "inflation_score", "inflation_direction"),
        ("Growth", "growth_score", "growth_direction"),
        ("Labor", "labor_score", "labor_direction"),
        ("Overall", "overall_stance_score", None),
    )

    meter_rows: list[dict[str, Any]] = []
    for currency in CURRENCY_METER_ORDER:
        history = rows_by_currency.get(currency, [])
        if not history:
            continue

        latest_row = history[-1]
        latest_date = latest_row["date"]
        if not isinstance(latest_date, date):
            continue

        period_rows: list[dict[str, Any]] = []
        for label, months in CURRENCY_METER_LOOKBACKS:
            target_date = _subtract_months(latest_date, months)
            selected = next(
                (
                    row
                    for row in reversed(history)
                    if isinstance(row["date"], date) and row["date"] <= target_date
                ),
                None,
            )
            period_rows.append({
                "label": label,
                "date": selected["date"] if selected else None,
                "row": selected,
            })

        metric_rows = []
        for label, key, direction_key in metrics:
            score_values = []
            for period in period_rows:
                score_value = _category_meter_score(
                    period["row"][key] if period["row"] is not None else None,
                    (
                        period["row"][direction_key]
                        if period["row"] is not None and direction_key is not None
                        else None
                    ),
                )
                score_values.append({
                    "period": period["label"],
                    "date": period["date"],
                    "score": _format_score(score_value),
                    "raw_score": score_value,
                    "bar_percent": _meter_percent(score_value),
                    "color_class": _score_color_class(score_value),
                })
            metric_rows.append({
                "label": label,
                "slug": label.lower(),
                "score_values": score_values,
            })

        overall_score = latest_row["overall_stance_score"]
        meter_rows.append({
            "currency": currency,
            "country_code": latest_row["country_code"],
            "latest_date": latest_date,
            "label": str(latest_row["overall_stance_label"]).replace("_", " ").title(),
            "score": _format_score(overall_score),
            "raw_score": overall_score,
            "score_percent": _meter_percent(overall_score),
            "color_class": _score_color_class(overall_score),
            "periods": period_rows,
            "metrics": metric_rows,
        })

    return meter_rows


def _yield_history_point(
    rows: list[dict[str, Any]],
    offset: int,
) -> tuple[date | None, float | None]:
    if not rows:
        return None, None
    index = max(0, len(rows) - 1 - offset)
    row = rows[index]
    row_date = row.get("date")
    parsed_date = None
    if isinstance(row_date, date):
        parsed_date = row_date
    elif row_date:
        try:
            parsed_date = date.fromisoformat(str(row_date)[:10])
        except ValueError:
            parsed_date = None

    for key in ("close", "adjusted_close", "value"):
        if row.get(key) is None:
            continue
        try:
            return parsed_date, float(row[key])
        except (TypeError, ValueError):
            continue
    return parsed_date, None


async def _build_yield_differentials() -> dict[str, Any]:
    today = _now().date()
    from_date = today - timedelta(days=YIELD_HISTORY_DAYS)

    try:
        async with EODHDClient() as client:
            histories = await asyncio.gather(
                *(
                    client.fetch_eod_history(
                        str(benchmark["symbol"]),
                        from_date=from_date,
                        to_date=today,
                    )
                    for benchmark in YIELD_BENCHMARKS
                ),
                return_exceptions=True,
            )
    except (EODHDError, ValueError):
        return {
            "rows": [],
            "pairs": [],
            "stats": [],
            "base_currency": YIELD_BASE_CURRENCY,
            "message": "Bond yield data is unavailable from EODHD right now.",
        }

    auth_blocked = any(isinstance(history, EODHDAuthError) for history in histories)

    rows: list[dict[str, Any]] = []
    for benchmark, history in zip(YIELD_BENCHMARKS, histories, strict=True):
        if isinstance(history, Exception):
            continue
        clean_history = [
            row for row in history
            if isinstance(row, dict) and row.get("date") and row.get("close") is not None
        ]
        clean_history.sort(key=lambda row: str(row.get("date")))
        latest_date, latest_yield = _yield_history_point(clean_history, 0)
        _, yield_5d = _yield_history_point(clean_history, 5)
        _, yield_1m = _yield_history_point(clean_history, 21)
        if latest_yield is None:
            continue

        change_5d_bp = (
            (latest_yield - yield_5d) * 100
            if yield_5d is not None
            else None
        )
        change_1m_bp = (
            (latest_yield - yield_1m) * 100
            if yield_1m is not None
            else None
        )
        rows.append({
            "currency": benchmark["currency"],
            "country_code": benchmark["country_code"],
            "flag": _flag_for_country(str(benchmark["country_code"])),
            "label": benchmark["label"],
            "symbol": benchmark["symbol"],
            "date": latest_date,
            "yield": latest_yield,
            "yield_display": _format_yield(latest_yield),
            "change_5d_bp": change_5d_bp,
            "change_5d_display": _format_bp(change_5d_bp),
            "change_5d_class": _spread_color_class(change_5d_bp),
            "change_1m_bp": change_1m_bp,
            "change_1m_display": _format_bp(change_1m_bp),
            "change_1m_class": _spread_color_class(change_1m_bp),
        })

    if not rows:
        message = (
            "The current EODHD key does not include GBOND yield access."
            if auth_blocked
            else "Bond yield data is unavailable from EODHD right now."
        )
        return {
            "rows": [],
            "pairs": [],
            "stats": [],
            "base_currency": YIELD_BASE_CURRENCY,
            "message": message,
        }

    rows_by_currency = {str(row["currency"]): row for row in rows}
    base_yield = rows_by_currency.get(YIELD_BASE_CURRENCY, {}).get("yield")
    for row in rows:
        spread = (
            (float(row["yield"]) - float(base_yield)) * 100
            if base_yield is not None
            else None
        )
        row["spread_vs_base_bp"] = spread
        row["spread_display"] = _format_bp(spread)
        row["spread_class"] = _spread_color_class(spread)
        row["spread_abs"] = abs(spread) if spread is not None else 0

    rows.sort(key=lambda row: float(row["spread_vs_base_bp"] or 0), reverse=True)

    pairs: list[dict[str, Any]] = []
    for label, left_currency, right_currency in YIELD_PAIR_DEFS:
        left = rows_by_currency.get(left_currency)
        right = rows_by_currency.get(right_currency)
        if not left or not right:
            continue
        spread = (float(left["yield"]) - float(right["yield"])) * 100
        pairs.append({
            "label": label,
            "left_currency": left_currency,
            "right_currency": right_currency,
            "spread_bp": spread,
            "spread_display": _format_bp(spread),
            "spread_class": _spread_color_class(spread),
        })

    dated_rows = [row for row in rows if row.get("date") is not None]
    latest_date = max((row["date"] for row in dated_rows), default=None)
    non_base_rows = [row for row in rows if row["currency"] != YIELD_BASE_CURRENCY]
    highest = max(non_base_rows, key=lambda row: float(row["spread_vs_base_bp"] or 0), default=None)
    lowest = min(non_base_rows, key=lambda row: float(row["spread_vs_base_bp"] or 0), default=None)
    base = rows_by_currency.get(YIELD_BASE_CURRENCY)
    stats = [
        {
            "label": "US 10Y",
            "value": base["yield_display"] if base else "N/A",
            "detail": "base benchmark",
            "class": "is-neutral",
        },
        {
            "label": "Highest vs USD",
            "value": (
                f"{highest['currency']} {highest['spread_display']}"
                if highest else "N/A"
            ),
            "detail": highest["label"] if highest else "no spread",
            "class": highest["spread_class"] if highest else "is-neutral",
        },
        {
            "label": "Lowest vs USD",
            "value": (
                f"{lowest['currency']} {lowest['spread_display']}"
                if lowest else "N/A"
            ),
            "detail": lowest["label"] if lowest else "no spread",
            "class": lowest["spread_class"] if lowest else "is-neutral",
        },
        {
            "label": "Updated",
            "value": latest_date.strftime("%b %d") if latest_date else "N/A",
            "detail": "latest EOD close",
            "class": "is-neutral",
        },
    ]

    return {
        "rows": rows,
        "pairs": pairs,
        "stats": stats,
        "base_currency": YIELD_BASE_CURRENCY,
        "message": "",
    }


async def _build_country_rows(
    session: AsyncSession,
    country_code: str,
    category: str,
) -> list[dict[str, Any]]:
    now = _now()
    rows_q = await session.execute(
        select(Indicator)
        .where(
            Indicator.country_code == country_code.upper(),
            or_(
                Indicator.primary_category == category,
                Indicator.secondary_categories.any(category),
            ),
        )
        .order_by(Indicator.importance.asc(), Indicator.display_name.asc())
    )

    rows: list[dict[str, Any]] = []
    for indicator in rows_q.scalars().all():
        history_q = await session.execute(
            select(IndicatorRelease)
            .where(
                IndicatorRelease.indicator_id == indicator.id,
            )
            .order_by(
                IndicatorRelease.period_start_date.desc().nullslast(),
                desc(IndicatorRelease.released_at),
                desc(IndicatorRelease.retrieved_at),
                desc(IndicatorRelease.id),
            )
            .limit(50)
        )
        history_rows_by_period: dict[tuple[Any, ...], IndicatorRelease] = {}
        for row in history_q.scalars().all():
            key = (
                getattr(row, "period", None),
                getattr(row, "period_start_date", None),
                getattr(row, "released_at", None),
            )
            history_rows_by_period.setdefault(key, row)

        history_rows = list(reversed(list(history_rows_by_period.values())[:12]))
        released_history_rows = [
            row for row in history_rows
            if row.actual is not None
            and getattr(row, "released_at", now) <= now
        ]
        sparkline_values = [float(row.actual) for row in released_history_rows]
        latest_release = released_history_rows[-1] if released_history_rows else None
        previous_release = (
            released_history_rows[-2]
            if len(released_history_rows) >= 2
            else None
        )

        latest_value = (
            float(latest_release.actual)
            if latest_release is not None and latest_release.actual is not None
            else None
        )
        previous_value = (
            float(previous_release.actual)
            if previous_release is not None and previous_release.actual is not None
            else None
        )
        rows.append({
            "id": indicator.id,
            "canonical_name": indicator.canonical_name,
            "display_name": indicator.display_name,
            "display_label": (
                f"{indicator.display_name}*"
                if indicator.secondary_categories else indicator.display_name
            ),
            "latest_value": _format_value(latest_value, indicator.unit),
            "previous_value": _format_value(previous_value, indicator.unit),
            "trend_symbol": _trend_symbol(sparkline_values),
            "sparkline_values": sparkline_values,
            "detail_href": (
                f"/country/{indicator.country_code.lower()}/indicator/"
                f"{indicator.canonical_name}"
            ),
            "is_multi_category": bool(indicator.secondary_categories),
        })
    return rows


async def _render_country_template(
    request: Request,
    session: AsyncSession,
    country_code: str,
    active_category: str,
) -> HTMLResponse:
    country_payload = await get_country_detail_payload(session, country_code)
    if country_payload is None:
        raise HTTPException(status_code=404, detail="Country not found")

    country = country_payload.country
    rows = await _build_country_rows(session, country.code, active_category)
    category_tabs = _category_tabs_for_country(country_payload.indicators)
    return templates.TemplateResponse(
        request,
        "country.html",
        {
            "request": request,
            "page_title": f"{country.name} | Macro Dashboard",
            "country": country,
            "country_flag": _flag_for_country(country.code),
            "active_category": active_category,
            "category_tabs": category_tabs,
            "rows": rows,
            "show_footnote": any(row["is_multi_category"] for row in rows),
        },
    )


def _category_tabs_for_country(indicators: list[Any]) -> tuple[str, ...]:
    present_categories: set[str] = set()
    for indicator in indicators:
        if indicator.primary_category:
            present_categories.add(indicator.primary_category)
        for category in indicator.secondary_categories or []:
            present_categories.add(category)

    ordered = [category for category in ALL_CATEGORY_TABS if category in present_categories]
    return tuple(ordered or ["Inflation", "Growth", "Labor"])


def _now():
    from datetime import datetime

    return datetime.now(timezone.utc)


def _normalize_news_item(item: dict[str, Any]) -> dict[str, Any] | None:
    title = (
        item.get("title")
        or item.get("headline")
        or item.get("name")
    )
    link = item.get("link") or item.get("url")
    if not title or not link:
        return None

    published_at = (
        item.get("date")
        or item.get("publishedAt")
        or item.get("published_at")
    )
    source = (
        item.get("source")
        or item.get("site")
        or item.get("source_name")
        or "EODHD"
    )
    summary = (
        item.get("content")
        or item.get("text")
        or item.get("description")
        or item.get("snippet")
        or ""
    )

    summary_text = str(summary).strip()
    if len(summary_text) > 180:
        summary_text = summary_text[:177].rstrip() + "..."

    tags = item.get("tags") or item.get("symbols") or []
    if isinstance(tags, str):
        tags = [part.strip() for part in tags.split(",") if part.strip()]

    return {
        "title": str(title).strip(),
        "link": str(link).strip(),
        "source": str(source).strip(),
        "published_at": published_at,
        "summary": summary_text,
        "tags": list(tags)[:3] if isinstance(tags, list) else [],
    }


@router.get("/", response_class=HTMLResponse)
async def landing_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Render the landing page with surprise strip and country cards."""
    countries = await list_country_summaries(session)
    surprises = await list_biggest_surprises(session, days=7, limit=5)
    currency_stance = await _build_currency_stance_dashboard(session)
    currency_meter = await _build_fundamental_currency_meter(session)
    yield_differentials = await _build_yield_differentials()
    news_items: list[dict[str, Any]] = []

    try:
        async with EODHDClient() as client:
            raw_news = await client.fetch_financial_news(topic="economy", limit=6)
        news_items = [
            normalized
            for item in raw_news
            if (normalized := _normalize_news_item(item)) is not None
        ][:6]
    except (EODHDError, ValueError):
        news_items = []

    country_cards = [
        {
            "code": country.code,
            "flag": _flag_for_country(country.code),
            "name": country.name,
            "currency_code": country.currency_code,
            "latest_release_at": country.latest_release_at,
        }
        for country in countries
    ]

    return templates.TemplateResponse(
        request,
        "landing.html",
        {
            "request": request,
            "page_title": "Macro Dashboard",
            "country_cards": country_cards,
            "surprises": surprises,
            "news_items": news_items,
            "currency_stance": currency_stance,
            "currency_meter": currency_meter,
            "yield_differentials": yield_differentials,
        },
    )


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Render an analytical inventory of the data currently stored."""
    analytics = await _build_analytics_snapshot(session)
    return templates.TemplateResponse(
        request,
        "analytics.html",
        {
            "request": request,
            "page_title": "Data Analytics | Macro Dashboard",
            "analytics": analytics,
        },
    )


@router.get("/bank-research", response_class=HTMLResponse)
async def bank_research_page(request: Request) -> HTMLResponse:
    """Render cached bank research summaries."""
    research = load_bank_research_index()
    return templates.TemplateResponse(
        request,
        "bank_research.html",
        {
            "request": request,
            "page_title": "Bank Research | Macro Dashboard",
            "research": research,
        },
    )


@router.get("/brief-builder", response_class=HTMLResponse)
async def brief_builder_page(request: Request) -> HTMLResponse:
    """Render the weekly and event prep brief builder."""
    return templates.TemplateResponse(
        request,
        "brief_builder.html",
        {
            "request": request,
            "page_title": "Brief Builder | Macro Dashboard",
        },
    )


@router.get("/analytics/report.csv", response_class=Response)
async def analytics_report_csv(
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Download a CSV summary report for the analytics inventory."""
    analytics = await _build_analytics_snapshot(session)
    csv_body = "\ufeff" + _build_analytics_csv(analytics)
    filename = f"macro-dashboard-summary-{_now().strftime('%Y%m%d')}.csv"
    return Response(
        content=csv_body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/analytics/report.pdf", response_class=Response)
async def analytics_report_pdf(
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Download a visual PDF summary report for the analytics inventory."""
    analytics = await _build_analytics_snapshot(session)
    try:
        pdf_body = _build_analytics_pdf(analytics)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    filename = f"macro-dashboard-summary-{_now().strftime('%Y%m%d')}.pdf"
    return Response(
        content=pdf_body,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/country/{code}", response_class=HTMLResponse)
async def country_page(
    code: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Render the country page with default Inflation tab."""
    return await _render_country_template(request, session, code, "Inflation")


@router.get("/country/{code}/tab/{category}", response_class=HTMLResponse)
async def country_tab_fragment(
    code: str,
    category: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Render HTMX fragment for a country/category indicator list."""
    normalized_category = TAB_SLUGS.get(category.lower())
    if normalized_category is None:
        raise HTTPException(status_code=404, detail="Category not found")

    country = await get_country(session, code)
    if country is None:
        raise HTTPException(status_code=404, detail="Country not found")

    rows = await _build_country_rows(session, country.code, normalized_category)
    return templates.TemplateResponse(
        request,
        "_country_tab.html",
        {
            "request": request,
            "country_code": country.code,
            "rows": rows,
            "show_footnote": any(row["is_multi_category"] for row in rows),
        },
    )


@router.get("/country/{code}/indicator/{canonical_name}", response_class=HTMLResponse)
async def indicator_detail_page(
    code: str,
    canonical_name: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Render the indicator detail workstation page."""
    country = await get_country(session, code)
    if country is None:
        raise HTTPException(status_code=404, detail="Country not found")

    indicator = await get_indicator_by_country_and_name(
        session, country.code, canonical_name
    )
    if indicator is None:
        raise HTTPException(status_code=404, detail="Indicator not found")

    return templates.TemplateResponse(
        request,
        "indicator.html",
        {
            "request": request,
            "page_title": f"{indicator.display_name} | Macro Dashboard",
            "country": country,
            "indicator": indicator,
        },
    )


@router.get("/calendar", response_class=HTMLResponse)
async def calendar_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Render the macro calendar page."""
    countries = await list_country_summaries(session)
    return templates.TemplateResponse(
        request,
        "calendar.html",
        {
            "request": request,
            "page_title": "Economic Calendar | Macro Dashboard",
            "countries": countries,
            "category_tabs": ALL_CATEGORY_TABS,
            "calendar_windows": CALENDAR_WINDOWS,
        },
    )
