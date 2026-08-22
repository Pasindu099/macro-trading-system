from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.knowledge.headline import (
    ManualHeadlineGate,
    contains_fabricated_numerical_levels,
    no_trade_reason,
)
from app.knowledge.schemas import (
    AnalystClaim,
    AttributionType,
    CausalChain,
    CausalChainStep,
    HeadlineAnalysis,
    RecommendationRecord,
    Scenario,
    SourceProvenance,
    TradeIdea,
)


def test_source_attribution_is_explicit():
    claim = AnalystClaim(
        claim="Limited reaction can itself be the signal.",
        analyst_interpretation="The market had already priced much of the headline.",
        source=SourceProvenance(document_id=1, page_start=2, page_end=2),
        attribution=AttributionType.PARAPHRASED,
    )

    assert claim.attribution == AttributionType.PARAPHRASED


def test_causal_chain_requires_ordered_steps():
    with pytest.raises(ValidationError):
        CausalChain(
            name="Event to FX",
            steps=[
                CausalChainStep(order=2, stage="rates", implication="Yields fall"),
                CausalChainStep(order=1, stage="event", implication="Payrolls miss"),
            ],
            source=SourceProvenance(document_id=1, page_start=1, page_end=1),
        )


def test_trade_idea_does_not_require_invented_levels_during_extraction():
    trade = TradeIdea(
        instrument="USD/JPY",
        asset_class="fx",
        direction="short",
        thesis="Policy surprise can pressure USD/JPY.",
        values_explicit_in_source=False,
    )

    assert trade.entry_logic is None
    assert trade.stop is None
    assert trade.targets == []


def test_no_trade_condition_for_missing_prices():
    reason = no_trade_reason(ManualHeadlineGate(current_prices_available=False))

    assert reason == "Current prices are unavailable for responsible numerical levels."


def test_protects_against_fabricated_price_levels():
    assert contains_fabricated_numerical_levels(
        current_prices_available=False,
        entry="Buy above 1.1020",
        stop=None,
        targets=[],
    )


def test_headline_analysis_requires_no_trade_reason_when_no_trades():
    scenario = Scenario(
        name="Base case",
        probability_range="40-55%",
        reasoning="The event is mostly priced.",
        expected_market_path="Limited follow-through.",
        invalidation="Fresh confirmation arrives.",
    )
    with pytest.raises(ValidationError):
        HeadlineAnalysis(
            event_interpretation={"what_happened": "Headline crossed"},
            cross_asset_map={"fx": "Mixed"},
            scenarios=[scenario, scenario, scenario],
            ranked_trade_expressions=[],
        )


def test_recommendation_revision_creates_new_immutable_version():
    original = RecommendationRecord(
        analysis_timestamp=datetime(2026, 8, 21, tzinfo=UTC),
        selected_instrument="EUR/USD",
    )
    revised = RecommendationRecord(
        analysis_timestamp=datetime(2026, 8, 21, 1, tzinfo=UTC),
        selected_instrument="EUR/USD",
        supersedes_recommendation_id=original.recommendation_id,
    )

    assert revised.recommendation_id != original.recommendation_id
    assert revised.supersedes_recommendation_id == original.recommendation_id

