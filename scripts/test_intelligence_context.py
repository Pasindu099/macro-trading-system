"""Manual verification for the intelligence context payload.

Run with:
    python scripts/test_intelligence_context.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.intelligence_context import (  # noqa: E402
    TRACKED_CURRENCIES,
    build_currency_context,
    build_pair_context,
)


def main() -> int:
    try:
        asyncio.run(run())
    except (ConnectionError, OSError, SQLAlchemyError) as exc:
        print("DATABASE ERROR: Unable to reach the database.")
        print(f"Details: {exc}")
        return 2
    except Exception as exc:
        message = str(exc).lower()
        if any(token in message for token in ("connect", "database", "asyncpg", "socket")):
            print("DATABASE ERROR: Unable to reach the database.")
            print(f"Details: {exc}")
            return 2
        raise
    return 0


async def run() -> None:
    ctx = await build_currency_context()
    validation = ctx.get("validation", {})
    data_quality_score = float(validation.get("data_quality_score") or 0)

    print(f"data_quality_score: {data_quality_score:.1f}")
    print()
    print_summary_table(ctx)
    print_warnings(ctx)
    print()
    print("AUD raw context:")
    print(json.dumps(ctx["currencies"]["AUD"], indent=2, default=str))
    print()

    pair_ctx = await build_pair_context("AUD", "USD")
    differential = pair_ctx["differential"]
    print("AUD/USD differential:")
    print(f"fundamental_bias: {differential.get('fundamental_bias')}")
    print(f"cb_score_diff: {differential.get('cb_score_diff')}")
    print(f"stance_alignment: {differential.get('stance_alignment')}")
    if _neutral_from_missing_scores(pair_ctx):
        print(
            "WARNING: AUD/USD fundamental_bias is NEUTRAL because one or both "
            "cb_preferred_score values are null"
        )
    elif differential.get("fundamental_bias") == "NEUTRAL":
        print("AUD/USD fundamental_bias is NEUTRAL because CB scores are genuinely close")
    print()

    if data_quality_score >= 60:
        print("CONTEXT READY - data quality sufficient for Claude brief")
    else:
        print("CONTEXT NOT READY - fix data issues above before proceeding to Claude integration")


def print_summary_table(ctx: dict[str, Any]) -> None:
    print("Currency | CB Score | CB Label | Stance | Surprises | Rate Prob | Data%")
    print("---------|----------|----------|--------|-----------|-----------|------")
    currencies = ctx.get("currencies", {})
    for currency in TRACKED_CURRENCIES:
        profile = currencies.get(currency, {})
        cb_score = _format_number(profile.get("cb_preferred_score"))
        cb_label = _short_label(profile.get("cb_preferred_label"))
        stance = _short_stance(profile.get("stance_label"))
        surprises = _surprise_count(profile)
        rate_prob = _rate_prob_summary(profile.get("rate_probability"))
        data_pct = _currency_data_pct(profile)
        print(
            f"{currency:<8} | {cb_score:<8} | {cb_label:<8} | "
            f"{stance:<6} | {surprises:<9} | {rate_prob:<9} | {data_pct:.0f}%"
        )


def print_warnings(ctx: dict[str, Any]) -> None:
    currencies = ctx.get("currencies", {})
    for currency in TRACKED_CURRENCIES:
        profile = currencies.get(currency, {})
        if profile.get("cb_preferred_score") is None:
            print(
                f"WARNING: {currency} cb_preferred_score returned null - "
                "check processed.cb_preferred_score has rows for this currency"
            )
        if _surprise_item_count(profile) == 0:
            print(
                f"WARNING: {currency} has 0 recent surprises - check indicator_releases "
                "has rows with is_latest=TRUE and released_at within 45 days"
            )


def _currency_data_pct(profile: dict[str, Any]) -> float:
    total, populated = _count_leaf_fields(profile)
    return (populated / total * 100) if total else 0.0


def _count_leaf_fields(value: Any) -> tuple[int, int]:
    if isinstance(value, dict):
        total = 0
        populated = 0
        for key, item in value.items():
            if key == "data_available":
                continue
            item_total, item_populated = _count_leaf_fields(item)
            total += item_total
            populated += item_populated
        return total, populated
    if isinstance(value, list):
        if not value:
            return 1, 0
        total = 0
        populated = 0
        for item in value:
            item_total, item_populated = _count_leaf_fields(item)
            total += item_total
            populated += item_populated
        return total, populated
    return 1, 0 if value is None else 1


def _format_number(value: Any) -> str:
    if value is None:
        return "null"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _short_label(value: Any) -> str:
    return str(value or "null")[:8]


def _short_stance(value: Any) -> str:
    normalized = str(value or "null").lower()
    if "hawk" in normalized or "bull" in normalized:
        return "Hawk"
    if "dove" in normalized or "bear" in normalized:
        return "Dove"
    if normalized == "null":
        return "null"
    return str(value)[:6]


def _surprise_count(profile: dict[str, Any]) -> str:
    count = _surprise_item_count(profile)
    return f"{count} found"


def _surprise_item_count(profile: dict[str, Any]) -> int:
    recent = profile.get("recent_surprises")
    if isinstance(recent, dict):
        items = recent.get("items")
        return len(items) if isinstance(items, list) else 0
    if isinstance(recent, list):
        return len(recent)
    return 0


def _rate_prob_summary(rate_probability: Any) -> str:
    if not isinstance(rate_probability, dict) or not rate_probability.get("data_available"):
        return "null"
    hike = _format_pct(rate_probability.get("hike_pct"))
    hold = _format_pct(rate_probability.get("hold_pct"))
    return f"H:{hike} Ho:{hold}"


def _format_pct(value: Any) -> str:
    if value is None:
        return "null"
    try:
        return f"{float(value):.0f}"
    except (TypeError, ValueError):
        return str(value)


def _neutral_from_missing_scores(pair_ctx: dict[str, Any]) -> bool:
    if pair_ctx["differential"].get("fundamental_bias") != "NEUTRAL":
        return False
    return (
        pair_ctx["base"].get("cb_preferred_score") is None
        or pair_ctx["quote"].get("cb_preferred_score") is None
    )


if __name__ == "__main__":
    raise SystemExit(main())
