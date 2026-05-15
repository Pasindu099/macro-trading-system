"""Server-rendered page routes for the Step 5 frontend."""

from __future__ import annotations

import asyncio
import csv
import calendar
import json
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.public import (
    fetch_investinglive_articles,
    get_country,
    get_country_detail_payload,
    get_indicator_by_country_and_name,
    list_biggest_surprises,
    list_country_summaries,
)
from app.db.models import Country, Indicator, IndicatorRelease, IngestionRun
from app.services.meeting_calendar import SUPPORTED_BANKS, normalize_bank, get_upcoming_meetings
from app.services.rate_probability import (
    DATA_STATE_LIVE,
    DATA_STATE_NO_CURVE,
    _bank_config,
    compute_meeting_probabilities,
    get_next_meeting_summary,
    get_twelve_month_outlook,
)
from app.db.session import get_session
from app.ingestion.eodhd_client import EODHDAuthError, EODHDClient, EODHDError
from app.processing.bank_research import (
    BANK_RESEARCH_DIR,
    BankResearchConfig,
    build_bank_research_cache,
    load_bank_research_index,
    parse_drive_folder_id,
)
from app.settings import get_settings

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
FX_PAIR_DEFS = (
    {"label": "EUR/USD", "symbol": "EURUSD.FOREX", "left_currency": "EUR", "right_currency": "USD"},
    {"label": "GBP/USD", "symbol": "GBPUSD.FOREX", "left_currency": "GBP", "right_currency": "USD"},
    {"label": "USD/JPY", "symbol": "USDJPY.FOREX", "left_currency": "USD", "right_currency": "JPY"},
    {"label": "AUD/USD", "symbol": "AUDUSD.FOREX", "left_currency": "AUD", "right_currency": "USD"},
    {"label": "USD/CAD", "symbol": "USDCAD.FOREX", "left_currency": "USD", "right_currency": "CAD"},
    {"label": "USD/CHF", "symbol": "USDCHF.FOREX", "left_currency": "USD", "right_currency": "CHF"},
    {"label": "NZD/USD", "symbol": "NZDUSD.FOREX", "left_currency": "NZD", "right_currency": "USD"},
)
YIELD_MATURITIES = (
    {"key": "1m", "label": "1M", "name": "1 month"},
    {"key": "3m", "label": "3M", "name": "3 months"},
    {"key": "6m", "label": "6M", "name": "6 months"},
    {"key": "1y", "label": "1Y", "name": "1 year"},
    {"key": "3y", "label": "3Y", "name": "3 years"},
    {"key": "5y", "label": "5Y", "name": "5 years"},
    {"key": "10y", "label": "10Y", "name": "10 years"},
)
COUNTRY_INDICATOR_HISTORY_LIMIT = 500
YIELD_MATURITY_SUFFIXES = {
    "1m": "1M",
    "3m": "3M",
    "6m": "6M",
    "1y": "1Y",
    "3y": "3Y",
    "5y": "5Y",
    "10y": "10Y",
}
BANK_RESEARCH_ADMIN_STATE_PATH = BANK_RESEARCH_DIR / "admin_state.json"
COUNTRY_FLAGS = {
    "US": "\U0001F1FA\U0001F1F8",
    "EU": "\U0001F1EA\U0001F1FA",
    "DE": "\U0001F1E9\U0001F1EA",
    "FR": "\U0001F1EB\U0001F1F7",
    "UK": "\U0001F1EC\U0001F1E7",
    "JP": "\U0001F1EF\U0001F1F5",
    "AU": "\U0001F1E6\U0001F1FA",
    "NZ": "\U0001F1F3\U0001F1FF",
    "CA": "\U0001F1E8\U0001F1E6",
    "CH": "\U0001F1E8\U0001F1ED",
}


def _flag_for_country(country_code: str) -> str:
    return COUNTRY_FLAGS.get(country_code, "\U0001F3F3\ufe0f")


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

    maintenance_indicators_q = await session.execute(
        select(
            Indicator.country_code,
            Country.currency_code,
            Indicator.canonical_name,
            Indicator.display_name,
        )
        .join(Country, Country.code == Indicator.country_code)
        .order_by(
            Country.currency_code.asc(),
            Indicator.display_name.asc(),
            Indicator.canonical_name.asc(),
        )
    )
    maintenance_indicators = [
        {
            "country_code": row.country_code,
            "currency_code": row.currency_code,
            "canonical_name": row.canonical_name,
            "display_name": row.display_name,
            "label": (
                f"{row.currency_code} · {row.display_name} "
                f"({row.canonical_name})"
            ),
        }
        for row in maintenance_indicators_q.all()
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
        "maintenance_indicators": maintenance_indicators,
        "total_releases": total_releases,
        "ingestion_run_total": int(ingestion_run_total or 0),
        "first_release_at": totals.first_release_at,
        "latest_observed_release_at": totals.latest_observed_release_at,
    }


async def _build_currency_stance_dashboard(
    session: AsyncSession,
) -> dict[int, list[dict[str, Any]]]:
    use_legacy_stance = False
    try:
        result = await session.execute(
            text(
                """
                WITH latest AS (
                    SELECT max(date) AS date FROM processed.cb_preferred_score
                )
                SELECT
                    s.date,
                    s.country_code,
                    s.currency,
                    s.inflation_score,
                    s.labor_score,
                    s.growth_score,
                    s.cb_strength_score,
                    s.strength_label,
                    s.trend_label,
                    s.confidence,
                    r.rank_strongest
                FROM processed.cb_preferred_score s
                JOIN processed.cb_preferred_rankings r
                    ON r.date = s.date AND r.currency = s.currency
                JOIN latest l ON l.date = s.date
                ORDER BY r.rank_strongest
                """
            )
        )
    except Exception:
        await session.rollback()
        use_legacy_stance = True
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

    if use_legacy_stance:
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
            rows_by_window.setdefault(window, []).append({
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
            })
        return rows_by_window

    rows: list[dict[str, Any]] = []
    for row in result.mappings().all():
        infl  = row["inflation_score"]
        labor = row["labor_score"]
        growth = row["growth_score"]
        score = row["cb_strength_score"]
        category_meters = [
            {
                "label": "Inflation",
                "short_label": "I",
                "score": _format_score(infl),
                "percent": _meter_percent(infl),
                "color_class": _score_color_class(infl),
            },
            {
                "label": "Labor",
                "short_label": "L",
                "score": _format_score(labor),
                "percent": _meter_percent(labor),
                "color_class": _score_color_class(labor),
            },
            {
                "label": "Growth",
                "short_label": "G",
                "score": _format_score(growth),
                "percent": _meter_percent(growth),
                "color_class": _score_color_class(growth),
            },
        ]
        rows.append(
            {
                "date": row["date"],
                "country_code": row["country_code"],
                "currency": row["currency"],
                "rank": row["rank_strongest"],
                "score": _format_score(score),
                "score_percent": _meter_percent(score),
                "label": str(row["strength_label"]).replace("_", " ").title(),
                "trend": str(row["trend_label"]).title(),
                "confidence": str(row["confidence"]).title(),
                "color_class": _score_color_class(score),
                "category_meters": category_meters,
            }
        )
    # cb_preferred_score has no window dimension — expose under all three keys
    # so existing template tabs continue to work
    return {1: rows, 2: rows, 3: rows}


async def _build_fundamental_currency_meter(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    currency_list = ", ".join(f"'{currency}'" for currency in CURRENCY_METER_ORDER)
    use_legacy_stance = False
    try:
        result = await session.execute(
            text(
                f"""
                WITH latest AS (
                    SELECT date
                    FROM processed.cb_preferred_score
                    WHERE currency IN ({currency_list})
                    GROUP BY date
                    HAVING count(DISTINCT currency) = {len(CURRENCY_METER_ORDER)}
                    ORDER BY date DESC
                    LIMIT 1
                )
                SELECT
                    date,
                    country_code,
                    currency,
                    inflation_score,
                    labor_score,
                    growth_score,
                    cb_strength_score,
                    strength_label
                FROM processed.cb_preferred_score
                WHERE currency IN ({currency_list})
                    AND date <= (SELECT date FROM latest)
                ORDER BY currency, date
                """
            )
        )
    except Exception:
        await session.rollback()
        use_legacy_stance = True
        try:
            result = await session.execute(
                text(
                    f"""
                    WITH latest AS (
                        SELECT date
                        FROM processed.currency_stance
                        WHERE window_months = 3
                            AND currency IN ({currency_list})
                        GROUP BY date
                        HAVING count(DISTINCT currency) = {len(CURRENCY_METER_ORDER)}
                        ORDER BY date DESC
                        LIMIT 1
                    )
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
                        AND date <= (SELECT date FROM latest)
                    ORDER BY currency, date
                    """
                )
            )
        except Exception:
            return []

    raw_rows = [dict(row) for row in result.mappings().all()]
    if not raw_rows and not use_legacy_stance:
        use_legacy_stance = True
        try:
            result = await session.execute(
                text(
                    f"""
                    WITH latest AS (
                        SELECT date
                        FROM processed.currency_stance
                        WHERE window_months = 3
                            AND currency IN ({currency_list})
                        GROUP BY date
                        HAVING count(DISTINCT currency) = {len(CURRENCY_METER_ORDER)}
                        ORDER BY date DESC
                        LIMIT 1
                    )
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
                        AND date <= (SELECT date FROM latest)
                    ORDER BY currency, date
                    """
                )
            )
            raw_rows = [dict(row) for row in result.mappings().all()]
        except Exception:
            await session.rollback()
            return []

    rows_by_currency: dict[str, list[dict[str, Any]]] = {}
    for row in raw_rows:
        rows_by_currency.setdefault(str(row["currency"]), []).append(dict(row))

    metrics = (
        ("Inflation", "inflation_score", "inflation_direction" if use_legacy_stance else None),
        ("Growth", "growth_score", "growth_direction" if use_legacy_stance else None),
        ("Labor", "labor_score", "labor_direction" if use_legacy_stance else None),
        ("Overall", "overall_stance_score" if use_legacy_stance else "cb_strength_score", None),
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

        overall_score = latest_row["overall_stance_score" if use_legacy_stance else "cb_strength_score"]
        meter_rows.append({
            "currency": currency,
            "country_code": latest_row["country_code"],
            "latest_date": latest_date,
            "label": str(latest_row["overall_stance_label" if use_legacy_stance else "strength_label"]).replace("_", " ").title(),
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


def _parse_yield_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        parsed_date, value = _yield_history_point([row], 0)
        if parsed_date is None or value is None:
            continue
        points.append({
            "date": parsed_date,
            "date_key": parsed_date.isoformat(),
            "yield": value,
        })
    points.sort(key=lambda point: point["date"])
    return points


def _build_yield_chart_data(
    histories_by_currency: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    yield_series: list[dict[str, Any]] = []
    history_maps: dict[str, dict[str, float]] = {}

    for benchmark in YIELD_BENCHMARKS:
        currency = str(benchmark["currency"])
        points = histories_by_currency.get(currency, [])
        history_maps[currency] = {
            str(point["date_key"]): float(point["yield"])
            for point in points
        }
        yield_series.append({
            "name": f"{currency} 10Y",
            "currency": currency,
            "label": str(benchmark["label"]),
            "symbol": str(benchmark["symbol"]),
            "data": [
                [str(point["date_key"]), round(float(point["yield"]), 4)]
                for point in points
            ],
        })

    spread_series: list[dict[str, Any]] = []
    latest_spreads: list[dict[str, Any]] = []
    for label, left_currency, right_currency in YIELD_PAIR_DEFS:
        left_history = history_maps.get(left_currency, {})
        right_history = history_maps.get(right_currency, {})
        dates = sorted(set(left_history).intersection(right_history))
        data = [
            [
                point_date,
                round((left_history[point_date] - right_history[point_date]) * 100, 1),
            ]
            for point_date in dates
        ]
        spread_series.append({
            "name": label,
            "left_currency": left_currency,
            "right_currency": right_currency,
            "data": data,
        })
        if data:
            latest_spreads.append({
                "label": label,
                "value": data[-1][1],
                "date": data[-1][0],
            })

    latest_spreads.sort(key=lambda point: float(point["value"]), reverse=True)
    return {
        "yield_series": yield_series,
        "spread_series": spread_series,
        "latest_spreads": latest_spreads,
        "base_currency": YIELD_BASE_CURRENCY,
        "unit": "%",
        "spread_unit": "bp",
    }


def _gbond_symbol_code(row: dict[str, Any]) -> str | None:
    for key in ("Code", "code", "Symbol", "symbol"):
        value = row.get(key)
        if value:
            return str(value).split(".")[0].upper()
    return None


async def _fetch_gbond_symbol_set() -> set[str]:
    try:
        async with EODHDClient() as client:
            symbols = await client.fetch_exchange_symbols("GBOND")
    except (EODHDError, ValueError):
        return set()
    return {
        code
        for row in symbols
        if isinstance(row, dict)
        if (code := _gbond_symbol_code(row))
    }


def _yield_symbol_prefix(benchmark: dict[str, Any]) -> str:
    symbol = str(benchmark["symbol"]).split(".", 1)[0]
    return symbol.removesuffix("10Y")


def _build_maturity_benchmarks(
    maturity_key: str,
    available_symbols: set[str],
) -> list[dict[str, Any]]:
    suffix = YIELD_MATURITY_SUFFIXES[maturity_key]
    benchmarks: list[dict[str, Any]] = []
    for benchmark in YIELD_BENCHMARKS:
        symbol_code = f"{_yield_symbol_prefix(benchmark)}{suffix}"
        if available_symbols and symbol_code not in available_symbols:
            continue
        benchmarks.append({
            **benchmark,
            "label": str(benchmark["label"]).replace("10Y", suffix),
            "symbol": f"{symbol_code}.GBOND",
            "maturity_key": maturity_key,
            "maturity_label": suffix,
        })
    return benchmarks


def _build_fx_chart_data(
    histories_by_pair: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    for pair in FX_PAIR_DEFS:
        label = str(pair["label"])
        points = _parse_yield_history(histories_by_pair.get(label, []))
        if not points:
            series.append({
                "name": label,
                "symbol": pair["symbol"],
                "data": [],
            })
            continue
        base = float(points[0]["yield"])
        data = []
        for point in points:
            value = float(point["yield"])
            performance = ((value - base) / base) * 100 if base else 0
            data.append([str(point["date_key"]), round(performance, 3)])
        series.append({
            "name": label,
            "symbol": pair["symbol"],
            "data": data,
        })
    return series


def _build_maturity_panels(
    yield_by_maturity: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    def available_for(key: str) -> bool:
        return bool(yield_by_maturity.get(key, {}).get("rows"))

    return [
        {
            "key": maturity["key"],
            "label": maturity["label"],
            "name": maturity["name"],
            "available": available_for(str(maturity["key"])),
            "summary": (
                "Live government-bond feed"
                if available_for(str(maturity["key"]))
                else "Awaiting feed"
            ),
        }
        for maturity in YIELD_MATURITIES
    ]


async def _build_rates_research_context() -> dict[str, Any]:
    available_symbols = await _fetch_gbond_symbol_set()
    yield_by_maturity: dict[str, dict[str, Any]] = {}
    for maturity in YIELD_MATURITIES:
        maturity_key = str(maturity["key"])
        benchmarks = _build_maturity_benchmarks(maturity_key, available_symbols)
        if not benchmarks:
            yield_by_maturity[maturity_key] = {
                "rows": [],
                "pairs": [],
                "stats": [],
                "base_currency": YIELD_BASE_CURRENCY,
                "symbols": [],
                "errors": [],
                "chart_data": _build_yield_chart_data({}),
                "message": "No EODHD GBOND symbols were found for this maturity.",
            }
            continue
        yield_by_maturity[maturity_key] = await _build_yield_differentials(
            benchmarks,
            maturity_label=str(maturity["label"]),
        )

    active_maturity = next(
        (
            str(maturity["key"])
            for maturity in YIELD_MATURITIES
            if yield_by_maturity.get(str(maturity["key"]), {}).get("rows")
        ),
        "10y",
    )
    yield_differentials = yield_by_maturity.get(active_maturity, yield_by_maturity["10y"])
    today = _now().date()
    from_date = today - timedelta(days=YIELD_HISTORY_DAYS)
    histories_by_pair: dict[str, list[dict[str, Any]]] = {}
    fx_errors: list[dict[str, Any]] = []

    try:
        async with EODHDClient() as client:
            histories = await asyncio.gather(
                *(
                    client.fetch_eod_history(
                        str(pair["symbol"]),
                        from_date=from_date,
                        to_date=today,
                    )
                    for pair in FX_PAIR_DEFS
                ),
                return_exceptions=True,
            )
    except (EODHDError, ValueError):
        histories = []
        fx_errors.append({
            "symbol": "FOREX",
            "label": "FX comparison",
            "error": "FX pair history is unavailable right now.",
        })

    for pair, history in zip(FX_PAIR_DEFS, histories, strict=False):
        if isinstance(history, Exception):
            fx_errors.append({
                "symbol": pair["symbol"],
                "label": pair["label"],
                "error": str(history),
            })
            continue
        histories_by_pair[str(pair["label"])] = [
            row for row in history
            if isinstance(row, dict) and row.get("date") and row.get("close") is not None
        ]

    fx_series = _build_fx_chart_data(histories_by_pair)
    pair_research = []
    spread_lookup = {
        str(pair["label"]): pair
        for pair in yield_differentials.get("pairs", [])
    }
    for pair in FX_PAIR_DEFS:
        fx_points = next(
            (series["data"] for series in fx_series if series["name"] == pair["label"]),
            [],
        )
        spread = spread_lookup.get(str(pair["label"]))
        performance = fx_points[-1][1] if fx_points else None
        pair_research.append({
            "label": pair["label"],
            "symbol": pair["symbol"],
            "spread_display": spread["spread_display"] if spread else "N/A",
            "spread_class": spread["spread_class"] if spread else "is-neutral",
            "fx_performance": f"{performance:+.2f}%" if performance is not None else "N/A",
            "fx_class": _spread_color_class(performance) if performance is not None else "is-neutral",
        })

    return {
        "yield_differentials": yield_differentials,
        "yield_by_maturity": yield_by_maturity,
        "yield_chart_by_maturity": {
            key: value.get("chart_data", _build_yield_chart_data({}))
            for key, value in yield_by_maturity.items()
        },
        "active_maturity": active_maturity,
        "maturity_panels": _build_maturity_panels(yield_by_maturity),
        "fx_series": fx_series,
        "pair_research": pair_research,
        "fx_errors": fx_errors,
    }


async def _build_yield_differentials(
    benchmarks: list[dict[str, Any]] | None = None,
    maturity_label: str = "10Y",
) -> dict[str, Any]:
    benchmarks = benchmarks or list(YIELD_BENCHMARKS)
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
                    for benchmark in benchmarks
                ),
                return_exceptions=True,
            )
    except (EODHDError, ValueError):
        return {
            "rows": [],
            "pairs": [],
            "stats": [],
            "base_currency": YIELD_BASE_CURRENCY,
            "symbols": [benchmark["symbol"] for benchmark in benchmarks],
            "errors": [],
            "chart_data": {
                "yield_series": [],
                "spread_series": [],
                "latest_spreads": [],
                "base_currency": YIELD_BASE_CURRENCY,
                "unit": "%",
                "spread_unit": "bp",
            },
            "message": "Bond yield data is unavailable from EODHD right now.",
        }

    auth_blocked = any(isinstance(history, EODHDAuthError) for history in histories)
    failed_symbols = [
        {
            "symbol": str(benchmark["symbol"]),
            "label": str(benchmark["label"]),
            "error": str(history),
            "is_access_error": isinstance(history, EODHDAuthError),
        }
        for benchmark, history in zip(benchmarks, histories, strict=True)
        if isinstance(history, Exception)
    ]

    rows: list[dict[str, Any]] = []
    histories_by_currency: dict[str, list[dict[str, Any]]] = {}
    for benchmark, history in zip(benchmarks, histories, strict=True):
        if isinstance(history, Exception):
            continue
        clean_history = [
            row for row in history
            if isinstance(row, dict) and row.get("date") and row.get("close") is not None
        ]
        clean_history.sort(key=lambda row: str(row.get("date")))
        histories_by_currency[str(benchmark["currency"])] = _parse_yield_history(clean_history)
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
            "EODHD rejected the GBOND yield requests for the current subscription. "
            "The symbols are valid, but this API key needs GBOND/government-bond access."
            if auth_blocked
            else "Bond yield data is unavailable from EODHD right now."
        )
        return {
            "rows": [],
            "pairs": [],
            "stats": [],
            "base_currency": YIELD_BASE_CURRENCY,
            "symbols": [benchmark["symbol"] for benchmark in benchmarks],
            "errors": failed_symbols,
            "chart_data": _build_yield_chart_data(histories_by_currency),
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
            "label": f"US {maturity_label}",
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
        "symbols": [benchmark["symbol"] for benchmark in benchmarks],
        "errors": failed_symbols,
        "chart_data": _build_yield_chart_data(histories_by_currency),
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
            .limit(COUNTRY_INDICATOR_HISTORY_LIMIT)
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
        previous_db_value = (
            float(latest_release.previous)
            if latest_release is not None
            and getattr(latest_release, "previous", None) is not None
            else None
        )
        previous_history_value = (
            float(previous_release.actual)
            if previous_release is not None and previous_release.actual is not None
            else None
        )
        previous_value = (
            previous_db_value
            if previous_db_value is not None
            else previous_history_value
        )
        if (
            latest_value is not None
            and previous_db_value is not None
            and len(sparkline_values) < 2
        ):
            sparkline_values = [previous_db_value, latest_value]
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


def _row_trend_state(row: dict[str, Any]) -> str:
    values = [value for value in row.get("sparkline_values", []) if value is not None]
    if len(values) < 2:
        return "is-neutral"
    if values[-1] > values[-2]:
        return "is-positive"
    if values[-1] < values[-2]:
        return "is-negative"
    return "is-neutral"


def _profile_card_from_row(label: str, row: dict[str, Any]) -> dict[str, Any]:
    previous_value = row.get("previous_value") or "N/A"
    detail = "No prior print" if previous_value == "N/A" else f"{previous_value} prior"
    card_label = row.get("display_name") if label == "Policy Rate" else label
    if label == "Policy Rate" and "fed interest rate" in str(card_label or "").lower():
        card_label = "Fed Funds Rate"
    return {
        "label": card_label or label,
        "value": row.get("latest_value") or "N/A",
        "detail": detail,
        "state": _row_trend_state(row),
    }


def _pick_profile_indicator(
    rows_by_category: dict[str, list[dict[str, Any]]],
    category: str,
    label: str,
    terms: tuple[str, ...],
    allow_fallback: bool = False,
) -> dict[str, Any]:
    rows = rows_by_category.get(category, [])
    for term in terms:
        for row in rows:
            haystack = " ".join(
                str(row.get(key) or "")
                for key in ("display_name", "display_label", "canonical_name")
            ).lower()
            if term in haystack:
                return _profile_card_from_row(label, row)
    if allow_fallback and rows:
        return _profile_card_from_row(label, rows[0])
    return {
        "label": label,
        "value": "N/A",
        "detail": "Awaiting data",
        "state": "is-neutral",
    }


def _build_country_profile_cards(
    rows_by_category: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    cards = [
        _pick_profile_indicator(
            rows_by_category,
            "Monetary Policy",
            "Policy Rate",
            (
                "fed funds",
                "policy rate",
                "target rate",
                "interest rate",
                "cash rate",
                "bank rate",
                "deposit rate",
                "overnight rate",
                "main refinancing",
            ),
            allow_fallback=True,
        ),
        _pick_profile_indicator(
            rows_by_category,
            "Inflation",
            "CPI YoY",
            (
                "cpi yoy",
                "cpi (yoy)",
                "headline cpi",
                "consumer price index",
                "hicp (yoy)",
                "hicp yoy",
            ),
        ),
        _pick_profile_indicator(
            rows_by_category,
            "Labor",
            "Unemployment",
            ("unemployment", "jobless"),
        ),
        _pick_profile_indicator(
            rows_by_category,
            "Growth",
            "GDP QoQ",
            (
                "gdp qoq",
                "gdp (qoq)",
                "gross domestic product",
                "gdp mom",
                "gdp (mom)",
                "gdp yoy",
                "gdp (yoy)",
            ),
        ),
    ]

    tracked_rows = [
        row
        for rows in rows_by_category.values()
        for row in rows[:3]
        if row.get("latest_value") and row.get("latest_value") != "N/A"
    ]
    moved_count = sum(
        1 for row in tracked_rows if _row_trend_state(row) != "is-neutral"
    )
    cards.append({
        "label": "Macro Surprise",
        "value": f"+{moved_count}" if moved_count else "0",
        "detail": "Recent moved indicators",
        "state": "is-positive" if moved_count else "is-neutral",
    })
    return cards


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
    profile_categories = ("Monetary Policy", "Inflation", "Labor", "Growth")
    rows_by_category = {
        category: await _build_country_rows(session, country.code, category)
        for category in profile_categories
    }
    rows = rows_by_category.get(active_category)
    if rows is None:
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
            "country_profile_cards": _build_country_profile_cards(rows_by_category),
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

    category = str(item.get("category") or "Macro").strip()
    tags = item.get("tags") or item.get("symbols") or []
    if isinstance(tags, str):
        tags = [part.strip() for part in tags.split(",") if part.strip()]
    if not tags and category:
        tags = [category]

    return {
        "title": str(title).strip(),
        "link": str(link).strip(),
        "source": str(source).strip(),
        "published_at": published_at,
        "summary": summary_text,
        "category": category,
        "tags": list(tags)[:3] if isinstance(tags, list) else [],
    }


def _read_bank_research_admin_state() -> dict[str, Any]:
    if not BANK_RESEARCH_ADMIN_STATE_PATH.exists():
        return {}
    try:
        with BANK_RESEARCH_ADMIN_STATE_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_bank_research_admin_state(state: dict[str, Any]) -> None:
    BANK_RESEARCH_ADMIN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with BANK_RESEARCH_ADMIN_STATE_PATH.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2, ensure_ascii=False)
        file.write("\n")


def _bank_research_admin_context(message: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    research = load_bank_research_index()
    state = _read_bank_research_admin_state()
    folder_url = (
        state.get("folder_url")
        or research.get("folder_url")
        or settings.bank_research_drive_folder_url
        or ""
    )
    return {
        "folder_url": folder_url,
        "state": state,
        "research": research,
        "message": message,
        "has_google_drive_key": bool(settings.google_drive_api_key),
        "has_openai_key": bool(settings.openai_api_key),
        "openai_model": settings.openai_model,
        "retention_days": settings.bank_research_retention_days,
    }


async def _refresh_bank_research_from_admin(folder_url: str) -> None:
    settings = get_settings()
    state = {
        **_read_bank_research_admin_state(),
        "folder_url": folder_url,
        "status": "running",
        "message": "Refreshing bank research reports from Google Drive.",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "errors": [],
    }
    _write_bank_research_admin_state(state)

    try:
        if not settings.google_drive_api_key:
            raise ValueError("GOOGLE_DRIVE_API_KEY is not configured.")

        config = BankResearchConfig(
            folder_url=folder_url,
            google_drive_api_key=settings.google_drive_api_key,
            openai_api_key=settings.openai_api_key,
            openai_model=settings.openai_model,
            retention_days=settings.bank_research_retention_days,
        )
        result = await build_bank_research_cache(config)
        state.update({
            "status": "success",
            "message": f"Refresh complete: {len(result.get('reports', []))} reports cached.",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "reports_count": len(result.get("reports", [])),
            "errors": result.get("errors", []),
        })
    except Exception as exc:  # noqa: BLE001 - surface operational failure on admin page
        state.update({
            "status": "failed",
            "message": f"Refresh failed: {exc}",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "errors": [str(exc)],
        })

    _write_bank_research_admin_state(state)


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
        raw_news = await fetch_investinglive_articles(limit=12)
        news_items = [
            normalized
            for item in raw_news
            if (normalized := _normalize_news_item(item)) is not None
        ][:12]
    except (HTTPException, ValueError):
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


@router.get("/rates", response_class=HTMLResponse)
async def rates_page(request: Request) -> HTMLResponse:
    """Render the rates and FX/yield differential research page."""
    context = await _build_rates_research_context()
    return templates.TemplateResponse(
        request,
        "rates.html",
        {
            "request": request,
            "page_title": "Yields | Macro Dashboard",
            **context,
        },
    )


@router.get("/cot", response_class=HTMLResponse)
async def cot_page(request: Request) -> HTMLResponse:
    """Render the CFTC COT visualization workspace."""
    return templates.TemplateResponse(
        request,
        "cot.html",
        {
            "request": request,
            "page_title": "COT Positioning | Macro Dashboard",
        },
    )


@router.get("/news-feed", response_class=HTMLResponse)
async def news_feed_page(request: Request) -> HTMLResponse:
    """Render the live macro news feed workspace."""
    return templates.TemplateResponse(
        request,
        "news_feed.html",
        {
            "request": request,
            "page_title": "Live News Feed | Macro Dashboard",
        },
    )


@router.get("/cb-news", response_class=HTMLResponse)
async def cb_news_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Render the CB news terminal with live feeds, scoring, and AI analysis."""
    currency_meter = await _build_fundamental_currency_meter(session)
    surprises = await list_biggest_surprises(session, days=14, limit=12)
    yield_differentials = await _build_yield_differentials()
    return templates.TemplateResponse(
        request,
        "cb_news.html",
        {
            "request": request,
            "page_title": "CB News | Macro Dashboard",
            "currency_meter": currency_meter,
            "surprises": surprises,
            "yield_differentials": yield_differentials,
        },
    )


@router.get("/trade-planner", response_class=HTMLResponse)
async def trade_planner_page(request: Request) -> HTMLResponse:
    """Render the swing-trade planning workflow."""
    yield_differentials = await _build_yield_differentials()
    planner_pairs = [
        pair.get("label")
        for pair in yield_differentials.get("pairs", [])
        if pair.get("label")
    ]
    return templates.TemplateResponse(
        request,
        "trade_planner.html",
        {
            "request": request,
            "page_title": "Trade Planner | Macro Dashboard",
            "planner_pairs": planner_pairs,
        },
    )


@router.get("/fx-outlook", response_class=HTMLResponse)
async def fx_outlook_page(request: Request) -> HTMLResponse:
    """Render the Myfxbook retail FX outlook widget."""
    return templates.TemplateResponse(
        request,
        "fx_outlook.html",
        {
            "request": request,
            "page_title": "FX Outlook | Macro Dashboard",
        },
    )


@router.get("/countries", response_class=HTMLResponse)
async def countries_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Render a country directory with links to country profiles."""
    countries = await list_country_summaries(session)
    country_cards = [
        {
            "code": country.code,
            "flag": _flag_for_country(country.code),
            "name": country.name,
            "currency_code": country.currency_code,
            "central_bank": country.central_bank,
            "cb_mandate_type": country.cb_mandate_type,
            "cb_inflation_target": country.cb_inflation_target,
            "indicator_count": country.indicator_count,
            "latest_release_at": country.latest_release_at,
        }
        for country in countries
    ]
    total_indicators = sum(country["indicator_count"] for country in country_cards)
    latest_release = max(
        (
            country["latest_release_at"]
            for country in country_cards
            if country["latest_release_at"] is not None
        ),
        default=None,
    )

    return templates.TemplateResponse(
        request,
        "countries.html",
        {
            "request": request,
            "page_title": "Countries | Macro Dashboard",
            "country_cards": country_cards,
            "country_total": len(country_cards),
            "total_indicators": total_indicators,
            "latest_release": latest_release,
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
    admin_ctx = _bank_research_admin_context()
    return templates.TemplateResponse(
        request,
        "bank_research.html",
        {
            "request": request,
            "page_title": "Bank Research | Macro Dashboard",
            "research": research,
            "folder_url": admin_ctx.get("folder_url", ""),
            "admin_state": admin_ctx.get("state", {}),
            "refresh_message": request.query_params.get("msg", ""),
        },
    )


@router.post("/bank-research/refresh", response_class=HTMLResponse)
async def refresh_bank_research(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Trigger a Drive refresh and redirect back to the research page."""
    state = _read_bank_research_admin_state()
    folder_url = state.get("folder_url", "")
    if not folder_url:
        return RedirectResponse("/bank-research/admin", status_code=303)
    updated = {
        **state,
        "status": "queued",
        "message": "Refresh queued from research page.",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_bank_research_admin_state(updated)
    background_tasks.add_task(_refresh_bank_research_from_admin, folder_url)
    return RedirectResponse("/bank-research?msg=refresh_queued", status_code=303)


@router.get("/bank-research/admin", response_class=HTMLResponse)
async def bank_research_admin_page(request: Request) -> HTMLResponse:
    """Internal page for updating the bank research Drive source."""
    return templates.TemplateResponse(
        request,
        "bank_research_admin.html",
        {
            "request": request,
            "page_title": "Manage Bank Research | Macro Dashboard",
            **_bank_research_admin_context(),
        },
    )


@router.post("/bank-research/admin", response_class=HTMLResponse)
async def update_bank_research_admin_page(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Save the research Drive folder link and optionally rebuild the cache."""
    form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    folder_url = (form.get("folder_url") or [""])[0].strip()
    action = (form.get("action") or ["save"])[0]

    if not folder_url:
        return templates.TemplateResponse(
            request,
            "bank_research_admin.html",
            {
                "request": request,
                "page_title": "Manage Bank Research | Macro Dashboard",
                **_bank_research_admin_context("Paste a Google Drive folder URL before saving."),
            },
            status_code=400,
        )

    try:
        parse_drive_folder_id(folder_url)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "bank_research_admin.html",
            {
                "request": request,
                "page_title": "Manage Bank Research | Macro Dashboard",
                **_bank_research_admin_context(str(exc)),
            },
            status_code=400,
        )

    state = {
        **_read_bank_research_admin_state(),
        "folder_url": folder_url,
        "status": "queued" if action == "rebuild" else "saved",
        "message": (
            "Refresh queued. Reload this page in a moment to check progress."
            if action == "rebuild"
            else "Drive folder link saved."
        ),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_bank_research_admin_state(state)

    if action == "rebuild":
        background_tasks.add_task(_refresh_bank_research_from_admin, folder_url)

    return RedirectResponse("/bank-research/admin", status_code=303)


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


@router.get("/research-lab", response_class=HTMLResponse)
async def research_lab_page(request: Request) -> HTMLResponse:
    """Render the currency research lab workspace."""
    return templates.TemplateResponse(
        request,
        "research_lab.html",
        {
            "request": request,
            "page_title": "Currency Research Lab | Macro Dashboard",
        },
    )


@router.get("/rate-prob/{bank}", response_class=HTMLResponse)
async def rate_probability_page(
    bank: str,
    request: Request,
    db=Depends(get_session),
) -> HTMLResponse:
    bank = bank.upper()
    if bank not in SUPPORTED_BANKS:
        raise HTTPException(404)

    _bank_names = {
        "FED":  "Federal Reserve",
        "ECB":  "European Central Bank",
        "BOE":  "Bank of England",
        "BOJ":  "Bank of Japan",
        "RBA":  "Reserve Bank of Australia",
        "BOC":  "Bank of Canada",
        "RBNZ": "Reserve Bank of New Zealand",
        "SNB":  "Swiss National Bank",
    }
    _rate_labels = {
        "FED":  "Fed Funds Rate",
        "ECB":  "Deposit Facility Rate",
        "BOE":  "Bank Rate",
        "BOJ":  "Policy Rate",
        "RBA":  "Cash Rate",
        "BOC":  "Overnight Rate",
        "RBNZ": "OCR",
        "SNB":  "SNB Policy Rate",
    }

    config = _bank_config(bank)
    current_rate: float = config["current_rate"]
    step_bps: int = int(config.get("step_bps") or 25)

    # Probability data — may be unavailable if OIS cache is empty.
    summary_raw: dict = {}
    outlook_raw: dict = {}
    next_meeting_dt: datetime | None = None
    no_data = False

    try:
        summary_raw = await get_next_meeting_summary(bank, db_session=db)
        next_meeting_dt = summary_raw.get("meeting_dt")
    except ValueError:
        no_data = True

    try:
        outlook_raw = await get_twelve_month_outlook(bank, db_session=db)
    except Exception:
        pass

    meetings_meta = await get_upcoming_meetings(bank, n=12, db_session=db)

    probabilities_list = []
    if not no_data:
        try:
            probabilities_list = await compute_meeting_probabilities(
                bank, step_bps=float(step_bps), n_meetings=12, db_session=db
            )
        except Exception:
            pass

    # Fall back to first calendar entry if we have no OIS summary.
    if next_meeting_dt is None and meetings_meta:
        try:
            next_meeting_dt = datetime.fromisoformat(str(meetings_meta[0]["meeting_dt"]))
        except (ValueError, TypeError):
            pass

    # Format next meeting strings for the template.
    next_meeting_datetime_str = "—"
    next_meeting_dt_iso = ""
    if next_meeting_dt is not None:
        try:
            from datetime import UTC as _UTC
            if next_meeting_dt.tzinfo is None:
                next_meeting_dt = next_meeting_dt.replace(tzinfo=_UTC)
            next_meeting_datetime_str = next_meeting_dt.astimezone(_UTC).strftime(
                "%B %d, %Y at %H:%M UTC"
            )
            next_meeting_dt_iso = next_meeting_dt.isoformat()
        except Exception:
            pass

    # as_of_date from OIS cache.
    market_data = {
        "available": False,
        "label": "Calendar only",
        "source": None,
        "as_of_date": None,
        "is_proxy": False,
    }
    try:
        _aod = await db.execute(
            text(
                """
                SELECT source, MAX(curve_date) AS curve_date, COUNT(*) AS rows
                FROM ois_cache
                WHERE bank = :bank
                GROUP BY source
                ORDER BY curve_date DESC, rows DESC
                LIMIT 1
                """
            ),
            {"bank": bank},
        )
        _aod_row = _aod.first()
        if _aod_row:
            _source = str(_aod_row.source)
            _labels = {
                "yfinance_ZQ": "Live futures",
                "ecb_estr_ois": "Live OIS",
                "ecb_yc_proxy": "ECB yield-curve proxy",
                "boe_sonia": "Live OIS",
                "rba_f17": "Live OIS",
                "tradingview_IB": "ASX cash-rate futures",
                "tradingview_CORRA_proxy": "CORRA futures proxy",
                "tfx_TONA_proxy": "TFX TONA futures proxy",
                "rbnz_wholesale_proxy": "RBNZ wholesale proxy",
                "tradingview_SARON_proxy": "SARON futures proxy",
            }
            market_data = {
                "available": True,
                "label": _labels.get(_source, _source),
                "source": _source,
                "as_of_date": _aod_row.curve_date.strftime("%Y-%m-%d"),
                "is_proxy": _source.endswith("_proxy"),
            }
        as_of_date = market_data["as_of_date"] or date.today().strftime("%Y-%m-%d")
    except Exception:
        as_of_date = date.today().strftime("%Y-%m-%d")

    # Outlook formatting.
    total_bps: float = float(outlook_raw.get("total_bps") or 0.0)
    outlook_direction = "up" if total_bps > 3 else "down" if total_bps < -3 else "hold"
    outlook_delta_bps = f"{total_bps:+.1f} bps" if total_bps != 0 else "±0 bps"
    outlook_description: str = outlook_raw.get("description") or "Hold"

    # Build combined meetings list.
    meetings: list[dict] = []
    if probabilities_list:
        for prob, meta in zip(probabilities_list, meetings_meta, strict=False):
            if prob.data_state == DATA_STATE_LIVE:
                outcomes = {"HIKE": prob.hike_prob or 0.0, "HOLD": prob.hold_prob or 0.0, "CUT": prob.cut_prob or 0.0}
                dom = max(outcomes, key=lambda k: outcomes[k])
                dominant_prob = outcomes[dom]
                market_data_available = True
            else:
                dom = "N/A"
                dominant_prob = None
                market_data_available = False
            meetings.append(
                {
                    "meeting_date": prob.meeting_dt.strftime("%b %d, %Y"),
                    "implied_rate": prob.implied_rate,
                    "cut_prob": prob.cut_prob,
                    "hold_prob": prob.hold_prob,
                    "hike_prob": prob.hike_prob,
                    "outcome_distribution": prob.outcome_distribution,
                    "dominant_outcome": dom,
                    "dominant_prob": dominant_prob,
                    "num_moves": prob.num_moves,
                    "cumulative_num_moves": (
                        round(prob.cumulative_delta_bps / float(step_bps), 4)
                        if market_data_available and step_bps
                        else None
                    ),
                    "delta_bps": prob.delta_bps,
                    "cumulative_delta_bps": prob.cumulative_delta_bps if market_data_available else None,
                    "is_official": bool(meta.get("is_official", True)),
                    "market_data_available": market_data_available,
                    "proximity_lock": prob.proximity_lock,
                    "proximity_warning": prob.proximity_warning,
                    "low_liquidity_curve": prob.low_liquidity_curve,
                    "curve_confidence_note": prob.curve_confidence_note,
                    "data_state": prob.data_state,
                    "data_state_message": prob.data_state_message,
                }
            )
    else:
        for meta in meetings_meta:
            meeting_dt = datetime.fromisoformat(str(meta["meeting_dt"]))
            meetings.append(
                {
                    "meeting_date": meeting_dt.strftime("%b %d, %Y"),
                    "implied_rate": current_rate,
                    "cut_prob": None,
                    "hold_prob": None,
                    "hike_prob": None,
                    "outcome_distribution": None,
                    "dominant_outcome": "N/A",
                    "dominant_prob": None,
                    "num_moves": None,
                    "cumulative_num_moves": None,
                    "delta_bps": None,
                    "cumulative_delta_bps": None,
                    "is_official": bool(meta.get("is_official", True)),
                    "market_data_available": False,
                    "proximity_lock": False,
                    "proximity_warning": "",
                    "low_liquidity_curve": bool(config["low_liquidity_curve"]),
                    "curve_confidence_note": (
                        "Indicative only — OIS market for this currency has limited liquidity. "
                        "Probabilities may be less reliable than G3 currencies."
                    ) if config["low_liquidity_curve"] else "",
                    "data_state": DATA_STATE_NO_CURVE,
                    "data_state_message": f"No OIS/futures curve available for {bank}.",
                }
            )

    # Calculate rate components based on bank
    if bank == "ECB":
        deposit_rate = current_rate - 0.50
        main_rate = current_rate
        lending_rate = current_rate + 0.50
    else:
        deposit_rate = current_rate
        main_rate = current_rate
        lending_rate = current_rate

    summary_data = {
        "bank": bank,
        "bank_full_name": _bank_names.get(bank, bank),
        "rate_label": _rate_labels.get(bank, "Policy Rate"),
        "current_rate": current_rate,
        "deposit_rate": deposit_rate,
        "main_rate": main_rate,
        "lending_rate": lending_rate,
        "last_ois_rate": float(summary_raw.get("last_ois_rate") or current_rate),
        "as_of_date": as_of_date,
        "step_bps": step_bps,
        "next_meeting_dt_iso": next_meeting_dt_iso,
        "next_meeting_datetime": next_meeting_datetime_str,
        "dominant_outcome": summary_raw.get("dominant_outcome", "HOLD"),
        "dominant_prob": float(summary_raw.get("dominant_prob_pct") or 0.0),
        "implied_delta_bps": float(summary_raw.get("implied_delta_bps") or 0.0),
        "outlook_direction": outlook_direction,
        "outlook_delta_bps": outlook_delta_bps,
        "outlook_description": outlook_description,
        "no_data": no_data,
        "market_data": market_data,
    }

    return templates.TemplateResponse(
        request,
        "rate_probability.html",
        {
            "request": request,
            "page_title": f"Rate Probability — {bank} | Macro Dashboard",
            "bank": bank,
            "summary": summary_data,
            "meetings": meetings,
            "valid_banks": SUPPORTED_BANKS,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    """Render local dashboard preferences and notification settings."""
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "request": request,
            "page_title": "Settings | Macro Dashboard",
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
