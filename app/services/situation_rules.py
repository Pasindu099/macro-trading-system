"""Deterministic situation thresholds and episode hysteresis."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path("config/situations.yaml")


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def condition(rule: dict[str, Any], evidence: dict[str, float | None]) -> bool | None:
    """Return None when a required input is absent."""
    if "all" in rule:
        values = [condition(part, evidence) for part in rule["all"]]
        return False if False in values else None if None in values else True
    if "any" in rule:
        values = [condition(part, evidence) for part in rule["any"]]
        return True if True in values else None if None in values else False
    value = evidence.get(rule["field"])
    if value is None:
        return None
    target = rule["value"]
    return {
        "gt": lambda: value > target,
        "gte": lambda: value >= target,
        "lt": lambda: value < target,
        "lte": lambda: value <= target,
        "eq": lambda: value == target,
    }[rule["op"]]()


def build_episodes(spec: dict[str, Any], daily: list[tuple[date, dict[str, float | None]]]) -> list[dict[str, Any]]:
    """Require consecutive calendar days to enter and exit; missing data pauses both."""
    episodes: list[dict[str, Any]] = []
    enter_start: date | None = None
    exit_start: date | None = None
    active: dict[str, Any] | None = None
    previous: date | None = None
    for day, evidence in sorted(daily):
        if previous is not None and day != previous + timedelta(days=1):
            enter_start = exit_start = None
        previous = day
        if active is None:
            if condition(spec["trigger"], evidence) is True:
                enter_start = enter_start or day
                if (day - enter_start).days + 1 >= spec["min_persistence_days"]:
                    active = {"started_at": enter_start, "ended_at": None,
                              "evidence": {"trigger": dict(evidence), "current": dict(evidence)}}
                    episodes.append(active)
                    enter_start = None
            else:
                enter_start = None
            continue
        if condition(spec["exit"], evidence) is True:
            exit_start = exit_start or day
            if (day - exit_start).days + 1 >= spec.get("exit_persistence_days", 5):
                active["ended_at"] = exit_start
                active = None
                exit_start = None
        else:
            exit_start = None
            active["evidence"]["current"] = dict(evidence)
    return episodes


def current_state(spec: dict[str, Any], episodes: list[dict[str, Any]],
                  latest_evidence: dict[str, float | None] | None) -> str:
    if episodes and episodes[-1]["ended_at"] is None:
        return "active"
    if latest_evidence is None:
        return "unavailable"
    if "watch" in spec and condition(spec["watch"], latest_evidence) is True:
        return "watch"
    return "inactive" if condition(spec["trigger"], latest_evidence) is not None else "unavailable"
