"""Real-Postgres release-to-score and revision cleanup checkpoint.

The fixture and all derived rows live in an outer transaction that rolls back.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Indicator, IndicatorRelease
from app.db.session import dispose_engine, get_engine
from app.services.event_innovation_jobs import score_new_releases


@pytest.mark.asyncio
async def test_jobs_release_to_score_and_revision_cleanup() -> None:
    engine = get_engine()
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                indicator = (await session.execute(
                    select(Indicator).where(
                        Indicator.country_code == "US",
                        Indicator.canonical_name == "nfp",
                    )
                )).scalar_one()
                first_retrieval = datetime.now(timezone.utc) + timedelta(days=1)
                release = IndicatorRelease(
                    indicator_id=indicator.id,
                    period="2099-01",
                    period_start_date=date(2099, 1, 1),
                    released_at=datetime(2099, 1, 5, tzinfo=timezone.utc),
                    actual=Decimal("100"), estimate=Decimal("90"),
                    retrieved_at=first_retrieval, is_latest=True,
                )
                session.add(release)
                await session.flush()

                await score_new_releases(session, first_retrieval - timedelta(seconds=1))
                first_score_id = (await session.execute(text("""
                    SELECT release_id FROM event_innovation_scores
                    WHERE indicator_id = :indicator_id AND release_date = '2099-01-05'
                """), {"indicator_id": indicator.id})).scalar_one()
                assert first_score_id == release.id

                release.is_latest = False
                revision = IndicatorRelease(
                    indicator_id=indicator.id,
                    period="2099-01",
                    period_start_date=date(2099, 1, 1),
                    released_at=datetime(2099, 1, 5, tzinfo=timezone.utc),
                    actual=Decimal("105"), estimate=Decimal("90"),
                    retrieved_at=first_retrieval + timedelta(minutes=1),
                    is_latest=True,
                )
                session.add(revision)
                await session.flush()
                await score_new_releases(session, first_retrieval - timedelta(seconds=1))
                score_ids = (await session.execute(text("""
                    SELECT release_id FROM event_innovation_scores
                    WHERE indicator_id = :indicator_id AND release_date = '2099-01-05'
                """), {"indicator_id": indicator.id})).scalars().all()
                assert score_ids == [revision.id]
        finally:
            await transaction.rollback()
    await dispose_engine()
