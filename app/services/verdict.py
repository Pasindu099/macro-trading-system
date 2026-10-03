"""Deterministic, explainable USD/EUR desk verdicts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.services import cb_tracking, fed_regime, positioning
from app.services.curve_metrics import get_curve
from app.services.macro_state import get_macro_state_board

CONFIG_PATH = Path("config/verdict.yaml")
SUPPORTED = {"USD": "US", "EUR": "DE"}
TRACKING_VALUES = {"running_hot": 1.0, "on_track": 0.0, "running_cold": -1.0}


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _clip(value: float, bound: float) -> float:
    return max(-bound, min(bound, value))


def _component(value: float | None, raw: Any = None, reason: str | None = None) -> dict[str, Any]:
    return {"status": "available" if value is not None else "unavailable", "value": value,
            "raw": raw, "reason": reason}


def _bias(score: float, thresholds: dict[str, float]) -> str:
    if score <= thresholds["strongly_bearish"]:
        return "strongly_bearish"
    if score < thresholds["bearish"]:
        return "bearish"
    if score < thresholds["bullish"]:
        return "neutral"
    if score < thresholds["strongly_bullish"]:
        return "bullish"
    return "strongly_bullish"


def _applies(row: dict[str, Any], currency: str) -> bool:
    key = row["scope_key"]
    if key == "GLOBAL" or key == currency or key.startswith(currency + ":"):
        return True
    return row["scope"] == "pair" and currency in key.split("/")


def score_verdict(currency: str, components: dict[str, dict[str, Any]],
                  active_situations: list[dict[str, Any]], config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    bound = cfg["normalization"]["component_clip"]
    weights = cfg["weights"]
    why = {}
    for name, weight in weights.items():
        item = components.get(name, _component(None, reason="No input"))
        value = _clip(item["value"], bound) if item["value"] is not None else None
        why[name] = {**item, "value": value, "weight": weight,
                     "contribution": round(value * weight, 4) if value is not None else None}
    available = [item["contribution"] for item in why.values() if item["contribution"] is not None]
    if not available:
        return {"currency": currency, "status": "unavailable", "reason": "No verdict inputs", "why": why}
    score = round(sum(available), 4)
    bias = _bias(score, cfg["bias_thresholds"])
    direction = 1 if score > 0 else -1 if score < 0 else 0
    signed = [item["contribution"] for item in why.values() if item["contribution"] not in (None, 0)]
    agreement = sum(value * direction > 0 for value in signed) / len(signed) if signed and direction else 0.0
    conviction_cfg = cfg["conviction"]
    conviction = ("high" if agreement >= conviction_cfg["high_agreement"] and len(signed) >= conviction_cfg["high_min_components"]
                  else "moderate" if agreement >= conviction_cfg["moderate_agreement"] else "low")
    crowding = why["positioning"].get("raw") or {}
    percentile = crowding.get("percentile") if isinstance(crowding, dict) else None
    crowded_with_bias = percentile is not None and (
        direction > 0 and percentile >= conviction_cfg["crowding_long_percentile"] or
        direction < 0 and percentile <= conviction_cfg["crowding_short_percentile"])
    high_severity = any(row["severity"] == "high" for row in active_situations)
    tiers = ["low", "moderate", "high"]
    conviction = tiers[max(0, tiers.index(conviction) - int(crowded_with_bias) - int(high_severity))]

    if any(row["situation_id"] == "risk_off" for row in active_situations):
        dominant = "geopolitics / risk sentiment"
    else:
        groups = {
            "cb": sum(why[name]["contribution"] or 0 for name in ("cb_tracking", "gap")),
            "data": sum(why[name]["contribution"] or 0 for name in ("macro", "curve", "situations")),
            "positioning": why["positioning"]["contribution"] or 0,
        }
        dominant = cfg["driver_labels"][max(groups, key=lambda name: abs(groups[name]))]

    aligned = sorted(((name, item["contribution"]) for name, item in why.items()
                      if item["contribution"] is not None and item["contribution"] * direction > 0),
                     key=lambda pair: abs(pair[1]), reverse=True)
    opposing = sorted(((name, item["contribution"]) for name, item in why.items()
                       if item["contribution"] is not None and item["contribution"] * direction < 0),
                      key=lambda pair: abs(pair[1]), reverse=True)

    def phrase(name: str, contribution: float) -> str:
        return cfg["phrases"][name]["positive" if contribution > 0 else "negative"]

    first = phrase(*aligned[0]) if aligned else cfg["templates"]["no_second"].capitalize()
    second = phrase(*aligned[1]).lower() if len(aligned) > 1 else cfg["templates"]["no_second"]
    main_risk = phrase(*opposing[0]).lower() if opposing else cfg["templates"]["no_opposition"]
    thesis = cfg["templates"]["thesis"].format(first=first, second=second,
                                                  bias=bias.replace("_", " "), currency=currency)
    thesis += " " + cfg["templates"]["risk"].format(risk=main_risk)
    return {"currency": currency, "status": "available", "horizon": cfg["horizon"],
            "bias": bias, "score": score, "conviction": conviction, "agreement": round(agreement, 3),
            "crowding_penalty": crowded_with_bias, "high_severity_penalty": high_severity,
            "dominant_driver": dominant, "main_risk": main_risk, "thesis": thesis,
            "why": why, "active_situations": active_situations}


async def _active_situations(currency: str) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""
            SELECT e.situation_id, e.scope, e.scope_key, e.started_at, e.evidence
            FROM situation_episodes e WHERE e.ended_at IS NULL
            ORDER BY e.started_at DESC
        """))
        active = [dict(row._mapping) for row in rows]
    effects = load_config()["situation_effects"]
    from app.services.situation_rules import load_config as load_situations_config
    severity = {name: spec["severity"] for name, spec in load_situations_config()["situations"].items()}
    return [{**row, "severity": severity[row["situation_id"]],
             "effect": effects.get(row["situation_id"], {}).get(currency, 0.0)}
            for row in active if _applies(row, currency)]


async def get_verdict(currency: str) -> dict[str, Any]:
    currency = currency.upper()
    if currency not in SUPPORTED:
        return {"currency": currency, "status": "unavailable", "reason": "No verdict for this desk"}
    cfg = load_config()
    async with get_sessionmaker()() as session:
        board = await get_macro_state_board(session)
    macro_row = next((row for row in board["rows"] if row["currency"] == currency), None)
    macro = _component(_clip(float(macro_row["overall_score"]), cfg["normalization"]["macro_z_clip"])
                       if macro_row and macro_row["overall_score"] is not None else None,
                       raw=macro_row, reason=None if macro_row else "No Macro State row")
    curve = await get_curve(SUPPORTED[currency], "1M")
    regime = curve.get("regime", {})
    curve_component = _component(cfg["curve_values"].get(regime.get("label"))
                                 if regime.get("status") == "available" else None,
                                 raw=regime, reason=regime.get("reason"))
    if currency == "USD":
        tracking = await cb_tracking.get_tracking()
        core = tracking.get("variables", {}).get("core_pce_inflation", {})
        cb_component = _component(TRACKING_VALUES.get(core.get("status")), raw=core,
                                  reason=core.get("reason", "Fed tracking unavailable"))
        gap = await fed_regime.get_gap()
        first_gap = next((row for row in gap.get("gaps", []) if row.get("gap_bp") is not None), None)
        gap_component = _component(_clip(first_gap["gap_bp"] / cfg["normalization"]["fed_gap_bp_per_unit"], 2)
                                   if first_gap else None, raw=first_gap, reason=gap.get("reason"))
    else:
        cb_component = _component(None, reason="ECB tracking pending Step 9 Part C")
        gap_component = _component(None, reason="ECB qualitative gap pending Step 9 Part C")
    crowding = await positioning.get_crowding()
    row = next((row for row in crowding["currencies"] if row["currency"] == currency), None)
    lever = row.get("leveraged_funds", {}) if row else {}
    percentile = lever.get("percentile")
    active = await _active_situations(currency)
    situation_value = _clip(sum(row["effect"] for row in active), 2)
    components = {"macro": macro, "cb_tracking": cb_component, "gap": gap_component,
                  "curve": curve_component, "situations": _component(situation_value, raw=active)}
    preliminary = sum((item["value"] or 0) * cfg["weights"][name] for name, item in components.items())
    position_value = (-0.5 if preliminary > 0 and percentile is not None and percentile >= cfg["conviction"]["crowding_long_percentile"]
                      else 0.5 if preliminary < 0 and percentile is not None and percentile <= cfg["conviction"]["crowding_short_percentile"]
                      else 0.0 if percentile is not None else None)
    components["positioning"] = _component(position_value, raw={"percentile": percentile},
                                           reason=None if percentile is not None else "No COT percentile")
    return score_verdict(currency, components, active, cfg)
