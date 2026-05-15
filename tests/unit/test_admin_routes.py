from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.api.routes.admin import _build_unmapped_where, admin_unmapped_events
from app.api.routes.pages import _build_country_rows
from app.api.routes.public import (
    EconomicCalendarEvent,
    _dedupe_calendar_events,
    _normalize_category_filter,
    _pick_revision_row,
    _parse_rss_articles,
)


class _ScalarResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _RowsResult:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def all(self) -> list[SimpleNamespace]:
        return self._rows


class _ScalarsResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _ExecuteResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows

    def scalars(self) -> _ScalarsResult:
        return _ScalarsResult(self._rows)


class _FakeSession:
    def __init__(self, results: list[object]) -> None:
        self._results = results
        self.statements: list[str] = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        return self._results.pop(0)


def test_build_unmapped_where_without_country() -> None:
    where_sql, params = _build_unmapped_where(None)

    assert where_sql == "indicator_id IS NULL"
    assert params == {}


def test_build_unmapped_where_normalizes_country() -> None:
    where_sql, params = _build_unmapped_where("us")

    assert where_sql == "indicator_id IS NULL AND raw_payload->>'country' = :country"
    assert params == {"country": "US"}


@pytest.mark.asyncio
async def test_admin_unmapped_events_totals_respect_country_filter() -> None:
    session = _FakeSession([
        _ScalarResult(4),
        _ScalarResult(2),
        _RowsResult([
            SimpleNamespace(
                country="US",
                event_type="Some Indicator",
                comparison="yoy",
                release_count=3,
                latest_release=datetime(2026, 4, 18, tzinfo=timezone.utc),
            )
        ]),
    ])

    response = await admin_unmapped_events(
        session=session,
        country="us",
        limit=10,
        min_count=2,
    )

    assert response.data.total_unmapped_releases == 4
    assert response.data.total_distinct_types == 2
    assert len(response.data.groups) == 1
    assert response.data.groups[0].country == "US"
    assert all(":country" in statement for statement in session.statements)


@pytest.mark.asyncio
async def test_build_country_rows_returns_one_row_per_indicator() -> None:
    indicator = SimpleNamespace(
        id=101,
        canonical_name="avg_hourly_earnings_yoy",
        display_name="Average Hourly Earnings (YoY)",
        country_code="US",
        unit="%",
        secondary_categories=["Inflation"],
    )
    history_rows = [
        SimpleNamespace(
            actual=3.0,
            previous=None,
            released_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            actual=3.5,
            previous=None,
            released_at=datetime(2026, 4, 11, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            actual=3.8,
            previous=None,
            released_at=datetime(2026, 4, 12, tzinfo=timezone.utc),
        ),
    ]
    session = _FakeSession([
        _ExecuteResult([indicator]),
        _ExecuteResult(list(reversed(history_rows))),
    ])

    rows = await _build_country_rows(session, "US", "Inflation")

    assert len(rows) == 1
    assert rows[0]["canonical_name"] == "avg_hourly_earnings_yoy"
    assert rows[0]["latest_value"] == "3.8 %"
    assert rows[0]["sparkline_values"] == [3.0, 3.5, 3.8]


@pytest.mark.asyncio
async def test_build_country_rows_ignores_upcoming_na_release_for_latest_value() -> None:
    indicator = SimpleNamespace(
        id=202,
        canonical_name="cpi_headline_yoy",
        display_name="Headline CPI (YoY)",
        country_code="AU",
        unit="%",
        secondary_categories=[],
    )
    history_rows = [
        SimpleNamespace(
            actual=3.4,
            previous=None,
            released_at=datetime(2026, 1, 29, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            actual=3.7,
            previous=None,
            released_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            actual=None,
            previous=3.7,
            released_at=datetime(2026, 4, 29, tzinfo=timezone.utc),
        ),
    ]
    session = _FakeSession([
        _ExecuteResult([indicator]),
        _ExecuteResult(list(reversed(history_rows))),
    ])

    rows = await _build_country_rows(session, "AU", "Inflation")

    assert rows[0]["latest_value"] == "3.7 %"
    assert rows[0]["sparkline_values"] == [3.4, 3.7]


@pytest.mark.asyncio
async def test_build_country_rows_does_not_require_latest_flag() -> None:
    indicator = SimpleNamespace(
        id=303,
        canonical_name="unemployment_rate",
        display_name="Unemployment Rate",
        country_code="AU",
        unit="%",
        secondary_categories=[],
    )
    history_rows = [
        SimpleNamespace(
            actual=4.1,
            previous=None,
            is_latest=False,
            released_at=datetime(2026, 3, 20, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            actual=4.2,
            previous=None,
            is_latest=False,
            released_at=datetime(2026, 4, 17, tzinfo=timezone.utc),
        ),
    ]
    session = _FakeSession([
        _ExecuteResult([indicator]),
        _ExecuteResult(list(reversed(history_rows))),
    ])

    rows = await _build_country_rows(session, "AU", "Labor")

    assert rows[0]["latest_value"] == "4.2 %"
    assert rows[0]["sparkline_values"] == [4.1, 4.2]


@pytest.mark.asyncio
async def test_build_country_rows_uses_release_previous_from_database() -> None:
    indicator = SimpleNamespace(
        id=404,
        canonical_name="headline_cpi_mom",
        display_name="Headline CPI (MoM)",
        country_code="AU",
        unit="%",
        secondary_categories=[],
    )
    latest_release = SimpleNamespace(
        actual=1.1,
        previous=0.2,
        released_at=datetime(2026, 4, 29, tzinfo=timezone.utc),
    )
    session = _FakeSession([
        _ExecuteResult([indicator]),
        _ExecuteResult([latest_release]),
    ])

    rows = await _build_country_rows(session, "AU", "Inflation")

    assert rows[0]["latest_value"] == "1.1 %"
    assert rows[0]["previous_value"] == "0.2 %"
    assert rows[0]["sparkline_values"] == [0.2, 1.1]


@pytest.mark.asyncio
async def test_build_country_rows_finds_prior_period_after_duplicate_releases() -> None:
    indicator = SimpleNamespace(
        id=505,
        canonical_name="headline_cpi_mom",
        display_name="Headline CPI (MoM)",
        country_code="AU",
        unit="%",
        secondary_categories=[],
    )
    latest_period_duplicates = [
        SimpleNamespace(
            actual=1.1,
            previous=None,
            period="Mar",
            period_start_date=datetime(2026, 3, 1, tzinfo=timezone.utc).date(),
            released_at=datetime(2026, 4, 29, tzinfo=timezone.utc),
        )
        for _ in range(75)
    ]
    prior_period = SimpleNamespace(
        actual=0.2,
        previous=0.4,
        period="Feb",
        period_start_date=datetime(2026, 2, 1, tzinfo=timezone.utc).date(),
        released_at=datetime(2026, 3, 26, tzinfo=timezone.utc),
    )
    session = _FakeSession([
        _ExecuteResult([indicator]),
        _ExecuteResult([*latest_period_duplicates, prior_period]),
    ])

    rows = await _build_country_rows(session, "AU", "Inflation")

    assert rows[0]["latest_value"] == "1.1 %"
    assert rows[0]["previous_value"] == "0.2 %"
    assert rows[0]["sparkline_values"] == [0.2, 1.1]


def test_calendar_category_filter_accepts_ui_categories() -> None:
    assert _normalize_category_filter("Monetary Policy") == "Monetary Policy"
    assert _normalize_category_filter("trade") == "Trade"
    assert _normalize_category_filter("Sentiment") == "Sentiment"


def test_pick_revision_row_prefers_actual_latest_for_chart_series() -> None:
    retrieved_at = datetime(2026, 5, 9, 14, tzinfo=timezone.utc)
    null_latest = SimpleNamespace(
        id=17020,
        actual=None,
        is_latest=True,
        released_at=datetime(2026, 3, 25, tzinfo=timezone.utc),
        retrieved_at=retrieved_at,
    )
    actual_latest = SimpleNamespace(
        id=17018,
        actual=0.2,
        is_latest=True,
        released_at=datetime(2026, 3, 25, tzinfo=timezone.utc),
        retrieved_at=retrieved_at,
    )

    picked = _pick_revision_row(
        [null_latest, actual_latest],
        "latest",
        actual_only=True,
    )

    assert picked is actual_latest


def test_parse_rss_articles_normalizes_investinglive_items() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>EUR/USD rises as ECB officials discuss rates</title>
          <link>https://investinglive.com/news/eur-usd-test</link>
          <pubDate>Wed, 06 May 2026 09:00:00 +0000</pubDate>
          <description><![CDATA[<p>The euro pushed higher after fresh central bank remarks.</p>]]></description>
          <category>Forex</category>
        </item>
      </channel>
    </rss>
    """

    articles = _parse_rss_articles(xml)

    assert articles == [{
        "title": "EUR/USD rises as ECB officials discuss rates",
        "link": "https://investinglive.com/news/eur-usd-test",
        "pubDate": "Wed, 06 May 2026 09:00:00 +0000",
        "description": "The euro pushed higher after fresh central bank remarks.",
        "category": "Central banks",
        "source": "investinglive.com",
    }]


def test_dedupe_calendar_events_prefers_mapped_event() -> None:
    released_at = datetime(2026, 4, 23, 1, 0, tzinfo=timezone.utc)
    mapped = EconomicCalendarEvent(
        release_id=10,
        indicator_id=1,
        country_code="AU",
        country_name="Australia",
        currency_code="AUD",
        canonical_name="composite_pmi",
        display_name="Composite PMI",
        primary_category="Growth",
        importance=2,
        period="Apr",
        released_at=released_at,
        status="upcoming",
        actual=None,
        estimate=46.3,
        previous=46.6,
        surprise=None,
        unit="index",
    )
    duplicate_mapped = EconomicCalendarEvent(
        release_id=11,
        indicator_id=1,
        country_code="AU",
        country_name="Australia",
        currency_code="AUD",
        canonical_name="composite_pmi",
        display_name="Composite PMI",
        primary_category="Growth",
        importance=2,
        period="Apr",
        released_at=released_at,
        status="upcoming",
        actual=None,
        estimate=None,
        previous=46.6,
        surprise=None,
        unit="index",
    )

    deduped = _dedupe_calendar_events([duplicate_mapped, mapped])

    assert len(deduped) == 1
    assert deduped[0].estimate == 46.3


def test_dedupe_calendar_events_collapses_vendor_prefixed_unmapped_duplicate() -> None:
    released_at = datetime(2026, 4, 23, 1, 0, tzinfo=timezone.utc)
    mapped = EconomicCalendarEvent(
        release_id=20,
        indicator_id=1,
        country_code="AU",
        country_name="Australia",
        currency_code="AUD",
        canonical_name="composite_pmi",
        display_name="Composite PMI",
        primary_category="Growth",
        importance=2,
        period="Apr",
        released_at=released_at,
        status="upcoming",
        actual=None,
        estimate=46.3,
        previous=46.6,
        surprise=None,
        unit="index",
    )
    vendor_unmapped = EconomicCalendarEvent(
        release_id=21,
        indicator_id=None,
        country_code="AU",
        country_name="Australia",
        currency_code="AUD",
        canonical_name=None,
        display_name="S&P Global Composite PMI",
        primary_category="Growth",
        importance=3,
        period="Apr",
        released_at=released_at,
        status="upcoming",
        actual=None,
        estimate=46.3,
        previous=46.6,
        surprise=None,
        unit=None,
    )

    deduped = _dedupe_calendar_events([mapped, vendor_unmapped])

    assert len(deduped) == 1
    assert deduped[0].display_name == "Composite PMI"


def test_dedupe_calendar_events_ignores_malformed_duplicate_period_label() -> None:
    released_at = datetime(2026, 4, 21, 6, 0, tzinfo=timezone.utc)
    malformed = EconomicCalendarEvent(
        release_id=30,
        indicator_id=1,
        country_code="UK",
        country_name="United Kingdom",
        currency_code="GBP",
        canonical_name="avg_earnings_excl_bonus",
        display_name="Average Earnings (excl. Bonus)",
        primary_category="Labor",
        importance=1,
        period="3Mo/Yr) (Feb",
        released_at=released_at,
        status="upcoming",
        actual=None,
        estimate=3.5,
        previous=3.8,
        surprise=None,
        unit="%",
    )
    clean = EconomicCalendarEvent(
        release_id=31,
        indicator_id=1,
        country_code="UK",
        country_name="United Kingdom",
        currency_code="GBP",
        canonical_name="avg_earnings_excl_bonus",
        display_name="Average Earnings (excl. Bonus)",
        primary_category="Labor",
        importance=1,
        period="Feb",
        released_at=released_at,
        status="upcoming",
        actual=None,
        estimate=3.5,
        previous=3.8,
        surprise=None,
        unit="%",
    )

    deduped = _dedupe_calendar_events([malformed, clean])

    assert len(deduped) == 1
    assert deduped[0].period == "Feb"
