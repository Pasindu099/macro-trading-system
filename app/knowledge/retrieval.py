"""Hybrid retrieval primitives for the Knowledge Bank.

Phase A keeps this deterministic and dependency-light. Later phases can add
embeddings, but the ranking contract already separates metadata, date, regime,
and stale-view handling from semantic similarity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


TIME_SENSITIVE_TYPES = {"time_sensitive_market_view", "forecast", "explicit_trade_idea"}
TIMELESS_TYPES = {
    "timeless_principle",
    "conditional_heuristic",
    "market_mechanics_explanation",
    "behavioural_insight",
}


@dataclass(frozen=True)
class RetrievalCandidate:
    object_id: int
    knowledge_type: str
    publication_date: date | None
    assets: tuple[str, ...] = ()
    instruments: tuple[str, ...] = ()
    macro_themes: tuple[str, ...] = ()
    analyst: str | None = None
    institution: str | None = None
    semantic_score: float = 0.0
    metadata_score: float = 0.0
    regime_score: float = 0.0
    source_title: str | None = None


@dataclass(frozen=True)
class RetrievalFilters:
    as_of_date: date | None = None
    assets: tuple[str, ...] = ()
    instruments: tuple[str, ...] = ()
    macro_themes: tuple[str, ...] = ()
    knowledge_types: tuple[str, ...] = ()
    analyst: str | None = None
    institution: str | None = None
    current_market_context: bool = False


def candidate_matches_filters(
    candidate: RetrievalCandidate,
    filters: RetrievalFilters,
) -> bool:
    if filters.knowledge_types and candidate.knowledge_type not in filters.knowledge_types:
        return False
    if filters.analyst and (candidate.analyst or "").lower() != filters.analyst.lower():
        return False
    if filters.institution and (candidate.institution or "").lower() != filters.institution.lower():
        return False
    if filters.assets and not _overlaps(filters.assets, candidate.assets):
        return False
    if filters.instruments and not _overlaps(filters.instruments, candidate.instruments):
        return False
    if filters.macro_themes and not _overlaps(filters.macro_themes, candidate.macro_themes):
        return False
    if filters.as_of_date and candidate.publication_date and candidate.publication_date > filters.as_of_date:
        return False
    return True


def rank_candidate(candidate: RetrievalCandidate, filters: RetrievalFilters) -> float:
    score = (
        candidate.semantic_score * 0.45
        + candidate.metadata_score * 0.35
        + candidate.regime_score * 0.20
    )
    if filters.assets and _overlaps(filters.assets, candidate.assets):
        score += 0.08
    if filters.macro_themes and _overlaps(filters.macro_themes, candidate.macro_themes):
        score += 0.06
    if candidate.knowledge_type in TIMELESS_TYPES:
        score += 0.05
    if filters.current_market_context:
        score *= stale_time_sensitive_multiplier(
            candidate.knowledge_type,
            candidate.publication_date,
            filters.as_of_date or date.today(),
        )
    return round(max(score, 0.0), 6)


def stale_time_sensitive_multiplier(
    knowledge_type: str,
    publication_date: date | None,
    as_of_date: date,
) -> float:
    if knowledge_type not in TIME_SENSITIVE_TYPES or publication_date is None:
        return 1.0
    age_days = max((as_of_date - publication_date).days, 0)
    if age_days <= 45:
        return 1.0
    if age_days <= 180:
        return 0.65
    if age_days <= 730:
        return 0.35
    return 0.18


def _overlaps(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    right_lower = {item.lower() for item in right}
    return any(item.lower() in right_lower for item in left)

