"""Full non-truncating Event Innovation reconciliation for deployment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.processing.event_innovation import (
    BundleResult, ScoredRelease, build_bundles, load_config,
    load_release_records, persist, score_releases,
)
from app.services.event_innovation_jobs import (
    delete_stale_bundles, delete_superseded_scores,
)


@dataclass
class RebuildPlan:
    scores: list[ScoredRelease]
    bundles: list[BundleResult]
    indicator_ids: list[int]
    existing_bundle_keys: set[tuple[str, date]]
    score_inserts: int
    score_updates: int
    orphan_deletes: int
    bundle_inserts: int
    bundle_updates: int
    bundle_deletes: int

    def counts(self) -> dict[str, int]:
        return {
            "score_rows_to_insert": self.score_inserts,
            "score_rows_to_update": self.score_updates,
            "orphan_score_rows_to_delete": self.orphan_deletes,
            "bundle_rows_to_insert": self.bundle_inserts,
            "bundle_rows_to_update": self.bundle_updates,
            "bundle_rows_to_delete": self.bundle_deletes,
        }


async def plan_full_rebuild(session: AsyncSession) -> RebuildPlan:
    config = load_config()
    records = await load_release_records(session)
    scores = score_releases(records, config)
    bundles = build_bundles(scores, config)
    existing_scores = (await session.execute(text(
        "SELECT release_id, indicator_id FROM event_innovation_scores"
    ))).all()
    existing_bundles = (await session.execute(text(
        "SELECT bundle_key, release_date FROM release_bundles"
    ))).all()
    score_ids = {row.release_id for row in scores}
    old_score_ids = {row.release_id for row in existing_scores}
    bundle_keys = {(row.bundle_key, row.release_date) for row in bundles}
    old_bundle_keys = {(row.bundle_key, row.release_date) for row in existing_bundles}
    return RebuildPlan(
        scores=scores,
        bundles=bundles,
        indicator_ids=sorted(
            {row.indicator_id for row in scores}
            | {row.indicator_id for row in existing_scores}
        ),
        existing_bundle_keys=old_bundle_keys,
        score_inserts=len(score_ids - old_score_ids),
        score_updates=len(score_ids & old_score_ids),
        orphan_deletes=len(old_score_ids - score_ids),
        bundle_inserts=len(bundle_keys - old_bundle_keys),
        bundle_updates=len(bundle_keys & old_bundle_keys),
        bundle_deletes=len(old_bundle_keys - bundle_keys),
    )


async def apply_full_rebuild(session: AsyncSession, plan: RebuildPlan) -> int:
    """Reconcile all indicators without truncation, in the caller's transaction."""
    await delete_superseded_scores(session, plan.indicator_ids)
    await delete_stale_bundles(session, plan.existing_bundle_keys, plan.bundles)
    written = await persist(session, plan.scores, plan.bundles)
    return written["scores"] + written["bundles"]
