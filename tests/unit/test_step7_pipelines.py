"""Step 7 Part C: pipeline restarts and switches."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.processing.cb_document_ingester import _parse_date_and_type
from app.processing.cb_fed_documents import fed_document_links, target_path
from app.services import desk_panels as dp
from app.services.cb_documents_jobs import document_run_times
from app.services.rate_fetchers import STALE_AFTER, apply_freshness
from app.settings import Settings


def test_llm_and_scraper_switches_default_off_and_single_model():
    fields = Settings.model_fields
    assert fields["news_ai_enabled"].default is False
    assert fields["rateprobability_scraper_enabled"].default is False
    assert fields["openai_model"].default == "gpt-4o-mini"


def test_ois_cache_fallback_older_than_three_days_is_stale():
    today = date(2026, 10, 5)
    statuses = {"FED": "ok", "BOC": "ok", "RBNZ": "cached", "ECB": "failed", "SNB": "ok"}
    latest = {"FED": today, "BOC": date(2026, 7, 22), "RBNZ": today - STALE_AFTER, "ECB": None, "SNB": None}
    out = apply_freshness(statuses, latest, today)
    assert out == {"FED": "ok", "BOC": "stale", "RBNZ": "cached", "ECB": "failed", "SNB": "stale"}


def test_document_runs_follow_meeting_config():
    config = {"FED": {"minutes_offset_days": 21, "meetings": [
        {"dt": "2026-09-16T14:00:00-04:00"}, {"dt": "2026-10-28T14:00:00-04:00"}]},
        "ECB": {"meetings": [{"dt": "2026-10-29T14:15:00+01:00"}]}}
    runs = document_run_times(config, after=datetime(2026, 10, 3, tzinfo=UTC))
    assert [(b, k, at.isoformat()) for b, k, at in runs] == [
        ("FED", "minutes", "2026-10-07T14:30:00-04:00"),      # 3 weeks after 16 Sep, +30 min
        ("FED", "statement", "2026-10-28T14:30:00-04:00"),    # decision +30 min
        ("ECB", "statement", "2026-10-29T14:45:00+01:00"),    # no offset configured → no minutes run
        ("FED", "minutes", "2026-11-18T14:30:00-05:00"),      # same 14:00 ET clock time after DST ends
    ]


def test_fed_links_cover_statements_and_minutes_since_cutoff():
    html = ('<a href="/monetarypolicy/files/monetary20250129a1.pdf"></a>'
            '<a href="/monetarypolicy/files/monetary20260916a1.pdf"></a>'
            '<a href="/monetarypolicy/fomcminutes20260729.htm"></a>')
    links = fed_document_links(html, since=date(2025, 9, 1))
    assert links == [
        ("minutes", date(2026, 7, 29), "https://www.federalreserve.gov/monetarypolicy/files/fomcminutes20260729.pdf"),
        ("statement", date(2026, 9, 16), "https://www.federalreserve.gov/monetarypolicy/files/monetary20260916a1.pdf"),
    ]
    minutes = target_path("minutes", date(2026, 7, 29), Path("data/policy"))
    assert _parse_date_and_type("FED", minutes) == (date(2026, 7, 29), "minutes")
    statement = target_path("statement", date(2026, 9, 16), Path("data/policy"))
    assert _parse_date_and_type("FED", statement) == (date(2026, 9, 16), "statement")


@pytest.mark.asyncio
async def test_rateprobability_manual_scrape_refused_when_disabled(monkeypatch):
    from app.api.routes import rate_probability_scraped as route
    monkeypatch.setattr(route, "get_settings", lambda: Settings.model_construct(rateprobability_scraper_enabled=False))
    with pytest.raises(HTTPException) as exc:
        await route.trigger_scrape(background_tasks=None)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_desk_news_panel_marks_ai_paused(monkeypatch):
    async def news(_):
        return [{"headline": "h", "url": "", "source": "s", "detected_at": datetime.now(UTC) - timedelta(days=9),
                 "implied_tier": "ECONOMIC_DATA", "severity": "MEDIUM", "alert_text": ""}]
    monkeypatch.setattr(dp, "news_alerts", news)
    monkeypatch.setattr(dp, "get_settings", lambda: Settings.model_construct(news_ai_enabled=False))
    ctx = await dp.panel_news({"currency": "USD"}, {})
    assert ctx["state"] == "ok" and ctx["ai_paused"] is True
