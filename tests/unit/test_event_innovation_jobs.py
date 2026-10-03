"""Selection and lock behavior of the incremental analytics job."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date

import pytest

from app.processing.event_innovation import BundleResult, ScoredRelease, load_config
from app.services import event_innovation_jobs as jobs


def _score(release_id: int, indicator_id: int, day: int, name: str,
           bundle_key: str | None = None) -> ScoredRelease:
    return ScoredRelease(
        release_id=release_id, indicator_id=indicator_id, country_code="US",
        release_date=date(2026, 10, day), canonical_name=name,
        actual=1.0, consensus=0.0, surprise_raw=1.0, surprise_scale=1.0,
        surprise_normalized=1.0, decay_bucket="high_freq_high_revision",
        half_life_days=10.0, scored=True, bundle_key=bundle_key,
    )


def test_incremental_plan_writes_suffix_and_affected_bundle_partners() -> None:
    config = load_config()
    old = _score(1, 10, 1, "nfp")
    changed = _score(2, 10, 2, "nfp", "US_NFP_DAY")
    partner = _score(3, 20, 2, "unemployment_rate", "US_NFP_DAY")
    unrelated = _score(4, 30, 2, "gdp_qoq")
    bundle = BundleResult("US_NFP_DAY", "US", date(2026, 10, 2))

    plan = jobs.plan_incremental(
        [old, changed, partner, unrelated], [bundle],
        {10: date(2026, 10, 2)}, config,
    )

    assert [row.release_id for row in plan.scores] == [2, 3]
    assert plan.bundles == [bundle]
    assert plan.affected_bundle_keys == {("US_NFP_DAY", date(2026, 10, 2))}
    assert jobs.plan_incremental(
        [old, changed, partner, unrelated], [bundle],
        {10: date(2026, 10, 2)}, config,
    ).scores == plan.scores


@pytest.mark.asyncio
async def test_concurrent_incremental_run_is_logged_skipped(monkeypatch) -> None:
    statuses: list[str] = []

    @asynccontextmanager
    async def held_lock(_name):
        yield False

    @asynccontextmanager
    async def fake_logger(_name):
        class Tracker:
            def mark_skipped(self):
                statuses.append("skipped")
        yield Tracker()

    monkeypatch.setattr(jobs, "job_lock", held_lock)
    monkeypatch.setattr(jobs, "run_logger", fake_logger)
    await jobs.run_incremental_event_innovation()
    assert statuses == ["skipped"]
