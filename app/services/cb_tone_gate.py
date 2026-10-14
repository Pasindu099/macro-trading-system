"""Tone display gate: labelled precision per method from the manual-review sample file (Step 12 decision)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.processing.tone_lexicon import lexicon

GOOD = {"✓", "✔", "y", "yes", "1", "ok", "correct"}
BAD = {"✗", "✘", "x", "n", "no", "0", "wrong"}
SECTION = re.compile(r"^##\s+(lexicon_v\d+)\b", re.M)


def gate_config() -> dict[str, Any]:
    return lexicon()["display_gate"]


def parse_labels(markdown: str) -> dict[str, dict[str, int]]:
    """{method: {"correct": n, "wrong": n, "unlabelled": n}} over rows with a non-zero score."""
    counts: dict[str, dict[str, int]] = {}
    sections = SECTION.split(markdown)
    for method, body in zip(sections[1::2], sections[2::2]):
        header = None
        tally = counts.setdefault(method, {"correct": 0, "wrong": 0, "unlabelled": 0})
        for line in body.splitlines():
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if header is None:
                header = [c.lower() for c in cells]
                continue
            if set(cells[0]) <= set("-: "):
                continue
            row = dict(zip(header, cells))
            try:
                score = float(row.get("score", "0").replace("+", ""))
            except ValueError:
                continue
            if score == 0:
                continue
            label = row.get("review", "").strip().lower()
            key = "correct" if label in GOOD else "wrong" if label in BAD else "unlabelled"
            tally[key] += 1
    return counts


def precision_by_method(path: Path | None = None) -> dict[str, dict[str, Any]]:
    cfg = gate_config()
    path = path or Path(cfg["sample_path"])
    counts = parse_labels(path.read_text(encoding="utf-8")) if path.exists() else {}
    out = {}
    for method, tally in counts.items():
        labelled = tally["correct"] + tally["wrong"]
        precision = round(tally["correct"] / labelled, 3) if labelled >= int(cfg["min_labels"]) else None
        out[method] = {**tally, "labelled": labelled, "precision": precision,
                       "passes": precision is not None and precision >= float(cfg["min_precision"])}
    return out


def display_method(path: Path | None = None) -> tuple[str | None, dict[str, Any]]:
    """Best method passing the gate (highest precision; v2 wins ties), else None with the evidence."""
    results = precision_by_method(path)
    passing = [(r["precision"], m) for m, r in results.items() if r["passes"]]
    method = max(passing)[1] if passing else None
    labels = sum(r["labelled"] for r in results.values())
    return method, {"min_precision": gate_config()["min_precision"], "min_labels": gate_config()["min_labels"],
                    "methods": results, "status": "passed" if method else ("failed" if labels else "awaiting labels")}
