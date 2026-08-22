from __future__ import annotations

from datetime import date

from app.knowledge.retrieval import (
    RetrievalCandidate,
    RetrievalFilters,
    candidate_matches_filters,
    rank_candidate,
    stale_time_sensitive_multiplier,
)


def test_hybrid_retrieval_filters_metadata():
    candidate = RetrievalCandidate(
        object_id=1,
        knowledge_type="market_mechanics_explanation",
        publication_date=date(2026, 1, 9),
        assets=("JPY", "USD"),
        macro_themes=("positioning",),
        analyst="Brent Donnelly",
        institution="Spectra Markets",
    )
    filters = RetrievalFilters(assets=("JPY",), analyst="Brent Donnelly")

    assert candidate_matches_filters(candidate, filters)


def test_hybrid_retrieval_rejects_wrong_asset():
    candidate = RetrievalCandidate(
        object_id=1,
        knowledge_type="forecast",
        publication_date=date(2026, 1, 9),
        assets=("XAG",),
    )
    filters = RetrievalFilters(assets=("JPY",))

    assert not candidate_matches_filters(candidate, filters)


def test_stale_time_sensitive_views_are_down_ranked():
    multiplier = stale_time_sensitive_multiplier(
        "forecast",
        publication_date=date(2024, 1, 1),
        as_of_date=date(2026, 8, 21),
    )

    assert multiplier < 0.3


def test_timeless_frameworks_do_not_get_stale_penalty():
    multiplier = stale_time_sensitive_multiplier(
        "timeless_principle",
        publication_date=date(2024, 1, 1),
        as_of_date=date(2026, 8, 21),
    )

    assert multiplier == 1.0


def test_rank_uses_metadata_and_staleness():
    old_forecast = RetrievalCandidate(
        object_id=1,
        knowledge_type="forecast",
        publication_date=date(2025, 1, 1),
        assets=("USD",),
        semantic_score=0.9,
        metadata_score=0.8,
    )
    framework = RetrievalCandidate(
        object_id=2,
        knowledge_type="timeless_principle",
        publication_date=date(2025, 1, 1),
        assets=("USD",),
        semantic_score=0.7,
        metadata_score=0.8,
    )
    filters = RetrievalFilters(
        as_of_date=date(2026, 8, 21),
        assets=("USD",),
        current_market_context=True,
    )

    assert rank_candidate(framework, filters) > rank_candidate(old_forecast, filters)
