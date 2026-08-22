"""Deterministic validation helpers for manual headline analysis."""

from __future__ import annotations

from dataclasses import dataclass


NO_TRADE_REASONS = {
    "not_new": "The information is not genuinely new.",
    "reaction_exhausted": "The market reaction appears exhausted.",
    "missing_prices": "Current prices are unavailable for responsible numerical levels.",
    "no_invalidation": "No observable invalidation can be defined.",
    "no_stop": "A practical stop cannot be defined.",
    "poor_risk_reward": "Risk-reward is inadequate.",
    "unconfirmed": "The headline is unconfirmed.",
    "event_risk": "Immediate event risk makes the setup unreliable.",
    "contradictory_history": "Historical evidence is contradictory.",
    "too_indirect": "The proposed instrument is too indirect.",
}


@dataclass(frozen=True)
class ManualHeadlineGate:
    genuinely_new: bool = True
    reaction_exhausted: bool = False
    current_prices_available: bool = True
    observable_invalidation: bool = True
    practical_stop_available: bool = True
    adequate_risk_reward: bool = True
    confirmed: bool = True
    immediate_event_risk: bool = False
    historical_evidence_contradictory: bool = False
    instrument_too_indirect: bool = False


def no_trade_reason(gate: ManualHeadlineGate) -> str | None:
    checks = (
        (not gate.genuinely_new, "not_new"),
        (gate.reaction_exhausted, "reaction_exhausted"),
        (not gate.current_prices_available, "missing_prices"),
        (not gate.observable_invalidation, "no_invalidation"),
        (not gate.practical_stop_available, "no_stop"),
        (not gate.adequate_risk_reward, "poor_risk_reward"),
        (not gate.confirmed, "unconfirmed"),
        (gate.immediate_event_risk, "event_risk"),
        (gate.historical_evidence_contradictory, "contradictory_history"),
        (gate.instrument_too_indirect, "too_indirect"),
    )
    for failed, reason_key in checks:
        if failed:
            return NO_TRADE_REASONS[reason_key]
    return None


def contains_fabricated_numerical_levels(
    current_prices_available: bool,
    entry: str | None,
    stop: str | None,
    targets: list[str],
) -> bool:
    if current_prices_available:
        return False
    text = " ".join([entry or "", stop or "", *targets])
    return any(char.isdigit() for char in text)

