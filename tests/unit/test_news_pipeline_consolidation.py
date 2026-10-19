import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services.news_parity import parity_summary

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 10, 19, 12, tzinfo=UTC)


def _row(source, minutes, collector=None, seen=False):
    return {"source": source, "published_at": T0 + timedelta(minutes=minutes),
            "collector": collector, "seen_by_app": seen}


def test_parity_holds_when_app_saw_every_pipeline_item():
    rows = [_row("investinglive", 0, seen=True), _row("investinglive", 30, "app", True),
            _row("investinglive", 90, seen=True)]
    summary = parity_summary(rows)["investinglive"]
    assert summary["parity"] is True
    assert summary["pipeline_first"] == 2 and summary["app_first"] == 1 and summary["missed_by_app"] == 0


def test_parity_fails_on_missed_item_and_reports_gap():
    rows = [_row("ecb", 0, seen=True), _row("ecb", 60), _row("ecb", 600, seen=True)]
    summary = parity_summary(rows)["ecb"]
    assert summary["parity"] is False and summary["missed_by_app"] == 1
    assert summary["max_gap_all_h"] == 9.0 and summary["max_gap_app_h"] == 10.0


def test_pages_desks_overview_read_news_only_through_service():
    readers = ["app/services/overview.py", "app/services/desk_panels.py", "app/services/cb_terminal.py",
               "app/api/routes/public.py"]
    for path in readers:
        source = (ROOT / path).read_text(encoding="utf-8")
        assert not re.search(r"FROM\s+(raw_news|enriched_news|intelligence\.news_alerts)\b", source), path


def test_search_index_migration_present():
    migration = (ROOT / "migrations/versions/2026_10_19_0038_news_search_indexes.py").read_text(encoding="utf-8")
    assert "to_tsvector('english'" in migration and "md5(url)" in migration
