"""Data library SQL against PostgreSQL, with rollback-only fixtures."""
import csv
import io
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.db.session import dispose_engine, get_sessionmaker
from app.services import data_exports, data_library as library


@pytest.fixture
async def sample():
    async with get_sessionmaker()() as session:
        canonical = "test_data_" + uuid4().hex
        indicator_id = (await session.execute(text("""
            INSERT INTO indicators (canonical_name, display_name, country_code, primary_category, frequency, importance)
            VALUES (:name, 'Test inflation', 'US', 'Inflation', 'monthly', 1) RETURNING id
        """), {"name": canonical})).scalar_one()
        ids = []
        for actual, retrieved in [(2, '2025-01-10'), (3, '2025-02-10'), (3, '2025-03-10')]:
            ids.append((await session.execute(text("""
                INSERT INTO indicator_releases (indicator_id, period, period_start_date, released_at,
                    retrieved_at, actual, estimate, previous, is_latest)
                VALUES (:id, '2024-12', '2024-12-01', '2025-01-10', CAST(:retrieved AS timestamptz),
                    :actual, 1.5, 1, false) RETURNING id
            """), {"id": indicator_id, "actual": actual,
                   "retrieved": datetime.fromisoformat(retrieved).replace(tzinfo=timezone.utc)})).scalar_one())
        await session.execute(text("""
            INSERT INTO event_innovation_scores (release_id, indicator_id, release_date, surprise_normalized, scored)
            VALUES (:release, :indicator, '2025-01-10', 1.234, true)
        """), {"release": ids[-1], "indicator": indicator_id})
        await session.execute(text("""
            INSERT INTO indicator_releases (indicator_id, released_at, actual)
            VALUES (:id, now() + interval '10 days', NULL)
        """), {"id": indicator_id})
        try:
            yield session, canonical, ids
        finally:
            await session.rollback()
    await dispose_engine()


@pytest.mark.asyncio
async def test_catalogue_and_revision_history(sample):
    session, canonical, ids = sample
    grouped = await library.catalogue(session, "us", theme="inflation", importance=1, search=canonical)
    assert grouped["count"] == 1
    row = grouped["themes"]["inflation"][0]
    assert row["latest_value"] == 3 and row["previous_value"] == 1
    assert float(row["surprise_sigma"]) == 1.234 and row["next_release"]
    history = (await library.history(session, "US", canonical))["rows"]
    assert [r["release_id"] for r in history] == list(reversed(ids))
    assert [r["actual"] for r in history] == [3, 3, 2]
    assert all(r["first_actual"] == 2 and r["latest_actual"] == 3 for r in history)
    assert all(r["revision_size"] == 1 and r["revision_count"] == 1 for r in history)
    assert history[0]["surprise_sigma"] is not None and history[-1]["surprise_sigma"] is None
    assert history[-1]["consensus"] == 1.5
    assert (await library.history(session, "US", canonical, date_from=date(2026, 1, 1)))["rows"] == []


@pytest.mark.asyncio
async def test_csv_stream_headers_and_cap(sample, monkeypatch):
    session, canonical, ids = sample

    @asynccontextmanager
    async def connection():
        yield session

    monkeypatch.setattr(data_exports, "get_sessionmaker", lambda: connection)
    monkeypatch.setattr(data_exports, "CSV_LIMIT", 2)
    response = await library.export_history(session, "US", canonical)
    assert response.media_type == "text/csv"
    assert 'attachment; filename="indicator-' in response.headers["content-disposition"]
    assert response.headers["x-export-row-limit"] == "2"
    chunks = [chunk async for chunk in response.body_iterator]
    rows = list(csv.DictReader(io.StringIO("".join(chunks))))
    assert len(rows) == 2 and int(rows[0]["release_id"]) == ids[-1]
    assert "first_actual" in rows[0] and "surprise_sigma" in rows[0]


@pytest.mark.asyncio
async def test_empty_mapped_indicator_and_china(sample):
    session, canonical, _ = sample
    await session.execute(text("DELETE FROM indicator_releases WHERE indicator_id=(SELECT id FROM indicators WHERE canonical_name=:name)"), {"name": canonical})
    result = await library.catalogue(session, "US", search=canonical)
    assert result["count"] == 1 and result["themes"]["inflation"][0]["latest_value"] is None
    china = await library.catalogue(session, "CN")
    assert china["calendar_only"] is True
    from app.settings import SUPPORTED_CURRENCIES
    assert "CNY" not in SUPPORTED_CURRENCIES
    with pytest.raises(LookupError):
        await library.history(session, "US", "nonexistent-" + canonical)


def test_legacy_module_has_no_runtime_callers():
    callers = []
    for path in Path("app").rglob("*.py"):
        if path.name == "release_ledger.py":
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if "import" in line and "release_ledger" in line:
                callers.append(str(path))
    assert callers == []


@pytest.mark.parametrize("category,theme", [("Inflation", "inflation"), ("Labor", "labor"),
    ("Monetary Policy", "policy"), ("Housing", "housing"), ("unknown", "other")])
def test_themes(category, theme):
    assert library.theme_for(category) == theme
