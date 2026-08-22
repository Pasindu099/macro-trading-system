"""Typed research and recommendation schemas for the Knowledge Bank.

These Pydantic models are intentionally independent from the UI and model
provider. They describe durable knowledge objects that can later be populated
by higher-quality extraction passes without changing the source archive.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class KnowledgeType(StrEnum):
    TIMELESS_PRINCIPLE = "timeless_principle"
    CONDITIONAL_HEURISTIC = "conditional_heuristic"
    HISTORICAL_MARKET_OBSERVATION = "historical_market_observation"
    TIME_SENSITIVE_MARKET_VIEW = "time_sensitive_market_view"
    FORECAST = "forecast"
    EXPLICIT_TRADE_IDEA = "explicit_trade_idea"
    RISK = "risk"
    INVALIDATION = "invalidation"
    MARKET_MECHANICS_EXPLANATION = "market_mechanics_explanation"
    BEHAVIOURAL_INSIGHT = "behavioural_insight"
    POST_EVENT_REFLECTION = "post_event_reflection"


class AttributionType(StrEnum):
    DIRECTLY_STATED = "directly_stated"
    PARAPHRASED = "paraphrased"
    DERIVED_FRAMEWORK = "derived_by_applying_framework"
    EXTERNAL_DATA_SUPPORTED = "supported_by_external_or_current_data"
    AGENT_INFERENCE = "agent_inference"
    SPECULATIVE_SCENARIO = "speculative_scenario"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class SourceProvenance(BaseModel):
    document_id: int | None = None
    source_file_id: int | None = None
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    source_section: str | None = None
    supporting_passage: str | None = None

    @model_validator(mode="after")
    def page_range_is_ordered(self) -> "SourceProvenance":
        if self.page_start and self.page_end and self.page_end < self.page_start:
            raise ValueError("page_end must be greater than or equal to page_start")
        return self


class RegimeTags(BaseModel):
    growth: list[Literal["accelerating", "stable", "slowing", "uncertain"]] = []
    inflation: list[Literal["rising", "stable", "falling", "uncertain"]] = []
    monetary_policy: list[Literal["tightening", "neutral", "easing", "uncertain"]] = []
    liquidity: list[Literal["improving", "deteriorating", "uncertain"]] = []
    volatility: list[Literal["low", "transitioning", "high", "uncertain"]] = []
    risk_appetite: list[Literal["risk_on", "mixed", "risk_off", "uncertain"]] = []
    usd_regime: list[Literal["broad_strength", "broad_weakness", "divergent", "uncertain"]] = []
    commodity_regime: list[Literal["demand_led", "supply_led", "speculative", "uncertain"]] = []
    correlation_regime: list[Literal["normal", "unstable", "breaking_down", "uncertain"]] = []


class MarketObservation(BaseModel):
    what_happened: str
    relevant_prices_or_moves: list[str] = []
    policy_expectations: str | None = None
    positioning: str | None = None
    consensus_narrative: str | None = None
    relevant_events: list[str] = []
    assets_affected: list[str] = []
    observation_date: date | None = None
    source: SourceProvenance


class AnalystClaim(BaseModel):
    claim: str
    analyst_interpretation: str | None = None
    direction: str | None = None
    confidence: str | None = None
    time_horizon: str | None = None
    supporting_evidence: list[str] = []
    contradictory_evidence: list[str] = []
    catalyst: str | None = None
    invalidation: str | None = None
    source: SourceProvenance
    attribution: AttributionType = AttributionType.PARAPHRASED


class CausalChainStep(BaseModel):
    order: int = Field(ge=1)
    stage: str
    implication: str
    assets: list[str] = []


class CausalChain(BaseModel):
    name: str
    steps: list[CausalChainStep] = Field(min_length=2)
    source: SourceProvenance

    @field_validator("steps")
    @classmethod
    def steps_are_ordered(cls, value: list[CausalChainStep]) -> list[CausalChainStep]:
        orders = [step.order for step in value]
        if orders != sorted(orders):
            raise ValueError("causal chain steps must be ordered")
        return value


class ReusableFramework(BaseModel):
    framework_name: str
    explanation: str
    when_it_applies: list[str] = []
    required_conditions: list[str] = []
    when_it_may_fail: list[str] = []
    relevant_assets_and_regimes: list[str] = []
    historical_examples: list[str] = []
    source: SourceProvenance


class TradeIdea(BaseModel):
    instrument: str
    asset_class: str
    direction: Literal["long", "short", "relative_value", "no_trade", "conditional"]
    thesis: str
    entry_logic: str | None = None
    stop: str | None = None
    targets: list[str] = []
    expected_horizon: str | None = None
    catalyst: str | None = None
    risk: str | None = None
    invalidation: str | None = None
    alternative_expression: str | None = None
    values_explicit_in_source: bool = False
    source: SourceProvenance | None = None


class Scenario(BaseModel):
    name: Literal["Base case", "Alternative case", "Tail case"] | str
    probability_range: str
    reasoning: str
    expected_market_path: str
    confirmation_signals: list[str] = []
    invalidation: str
    catalysts: list[str] = []
    main_risks: list[str] = []


class HeadlineInput(BaseModel):
    headline: str = Field(min_length=3)
    article_text: str | None = None
    timestamp: datetime
    current_market_prices: dict[str, float] = {}
    user_thesis: str | None = None
    preferred_asset: str | None = None
    holding_horizon: str | None = None


class RankedTradeExpression(BaseModel):
    rank: int = Field(ge=1, le=3)
    instrument: str
    direction: Literal["long", "short", "relative_value", "conditional"]
    entry_zone_or_trigger: str | None = None
    stop: str | None = None
    target_1: str | None = None
    target_2: str | None = None
    expected_holding_period: str
    estimated_risk_reward: str | None = None
    thesis: str
    catalyst: str | None = None
    confirmation_conditions: list[str] = []
    invalidation: str | None = None
    principal_risks: list[str] = []
    confidence: str
    relative_quality: str

    @model_validator(mode="after")
    def actionable_trade_has_risk_controls(self) -> "RankedTradeExpression":
        missing = [
            name
            for name, value in (
                ("entry_zone_or_trigger", self.entry_zone_or_trigger),
                ("stop", self.stop),
                ("target_1", self.target_1),
                ("invalidation", self.invalidation),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "actionable trade is missing required risk-control fields: "
                + ", ".join(missing)
            )
        return self


class HeadlineAnalysis(BaseModel):
    event_interpretation: dict[str, str]
    cross_asset_map: dict[str, str]
    scenarios: list[Scenario] = Field(min_length=3)
    ranked_trade_expressions: list[RankedTradeExpression] = Field(max_length=3)
    no_trade_reason: str | None = None
    decision_support_disclaimer: str = (
        "Research and decision support only; not guaranteed financial advice."
    )

    @model_validator(mode="after")
    def no_trade_is_explicit(self) -> "HeadlineAnalysis":
        if not self.ranked_trade_expressions and not self.no_trade_reason:
            raise ValueError("no_trade_reason is required when no trades are returned")
        return self


class RecommendationRecord(BaseModel):
    recommendation_id: str = Field(default_factory=lambda: str(uuid4()))
    analysis_timestamp: datetime
    headline_or_event_id: str | None = None
    information_available: dict[str, str | list[str] | dict[str, str]] = {}
    prices_used: dict[str, float] = {}
    retrieved_research_object_ids: list[int] = []
    scenarios: list[Scenario] = []
    probabilities: dict[str, str] = {}
    selected_instrument: str | None = None
    direction: str | None = None
    proposed_entry: str | None = None
    stop: str | None = None
    targets: list[str] = []
    horizon: str | None = None
    confidence: str | None = None
    invalidation: str | None = None
    recommendation_status: str = "draft"
    model: str | None = None
    prompt_version: str | None = None
    supersedes_recommendation_id: str | None = None
    entry_triggered: bool | None = None
    entry_timestamp: datetime | None = None
    exit_timestamp: datetime | None = None
    exit_reason: str | None = None
    return_value: float | None = None
    maximum_favourable_excursion: float | None = None
    maximum_adverse_excursion: float | None = None
    thesis_outcome: str | None = None
    execution_outcome: str | None = None


class KnowledgeObjectPayload(BaseModel):
    knowledge_type: KnowledgeType
    concise_statement: str
    detailed_explanation: str | None = None
    assets: list[str] = []
    instruments: list[str] = []
    countries: list[str] = []
    central_banks: list[str] = []
    macro_themes: list[str] = []
    event_types: list[str] = []
    market_regime: RegimeTags = Field(default_factory=RegimeTags)
    direction: str | None = None
    time_horizon: str | None = None
    confidence_language: str | None = None
    supporting_evidence: list[str] = []
    contradictory_evidence: list[str] = []
    catalysts: list[str] = []
    risks: list[str] = []
    invalidation_conditions: list[str] = []
    attribution: AttributionType
    source: SourceProvenance
    review_status: ReviewStatus = ReviewStatus.PENDING

