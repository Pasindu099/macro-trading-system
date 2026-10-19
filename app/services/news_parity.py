"""Side-by-side parity between news_pipeline collectors and the app collector (Step 13 B).

Both write raw_news and dedupe on md5(url). App inserts carry raw_payload.collector='app';
items the pipeline stored first get raw_payload.seen_by_app=true when the app fetch sees them.
Parity = every pipeline item from an overlapping source was also seen by the app, and the
app-seen series has no gap longer than the full series has.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.db.session import get_sessionmaker

OVERLAP_SOURCES = ("investinglive", "federal_reserve", "ecb", "al_jazeera")


def _max_gap(times: list[datetime]) -> timedelta:
    ordered = sorted(times)
    return max((b - a for a, b in zip(ordered, ordered[1:])), default=timedelta(0))


def parity_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """rows: source, published_at, collector ('app' or None), seen_by_app (bool)."""
    out: dict[str, dict[str, Any]] = {}
    for source in sorted({row["source"] for row in rows}):
        items = [row for row in rows if row["source"] == source]
        pipeline = [row for row in items if row["collector"] != "app"]
        app_seen = [row for row in items if row["collector"] == "app" or row["seen_by_app"]]
        missed = [row for row in pipeline if not row["seen_by_app"]]
        gap_all = _max_gap([row["published_at"] for row in items])
        gap_app = _max_gap([row["published_at"] for row in app_seen])
        out[source] = {
            "items": len(items), "pipeline_first": len(pipeline), "app_first": len(items) - len(pipeline),
            "missed_by_app": len(missed), "max_gap_all_h": round(gap_all.total_seconds() / 3600, 1),
            "max_gap_app_h": round(gap_app.total_seconds() / 3600, 1),
            "parity": not missed and gap_app <= gap_all,
        }
    return out


async def load_rows(since: datetime, until: datetime) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text("""
            SELECT source, published_at, raw_payload->>'collector' AS collector,
                   coalesce((raw_payload->>'seen_by_app')::boolean, false) AS seen_by_app
            FROM raw_news
            WHERE source = ANY(:sources) AND published_at >= :since AND published_at < :until
        """), {"sources": list(OVERLAP_SOURCES), "since": since, "until": until})).mappings().all()
    return [dict(row) for row in rows]
