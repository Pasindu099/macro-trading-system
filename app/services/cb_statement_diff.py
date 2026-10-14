"""Sentence-level diff of the latest vs previous policy statement (Step 12 Part C; difflib, no tone tagging)."""

from __future__ import annotations

import difflib
import re
from typing import Any

from sqlalchemy import text

from app.db.session import get_sessionmaker

DOC_TYPE = "policy_statement"
MIN_WORDS = 4
# Keywords match at word starts; first topic with the most hits wins; no hit -> "other".
TOPICS = {
    "policy stance": ["policy rate", "interest rate", "target range", "federal funds", "cash rate", "bank rate",
                      "deposit facility", "overnight rate", "basis point", "percentage point", "hike", "cut",
                      "raise", "lower", "maintain", "hold ", "held", "unchanged", "tighten", "easing", "ease", "accommodat",
                      "restrictive", "stance", "decid", "vote", "ocr"],
    "inflation": ["inflation", "price", "cpi", "pce", "hicp", "cost"],
    "labor": ["employment", "unemployment", "labour", "labor", "job", "wage", "payroll", "workers"],
    "growth": ["growth", "gdp", "activity", "economy", "economic", "demand", "output", "consumption",
               "investment", "spending", "recession", "export", "import", "trade"],
    "balance sheet": ["balance sheet", "asset purchase", "securities", "holdings", "quantitative", "bond purchase",
                      "jgb", "reinvest", "gilt", "app ", "pepp", "runoff", "run-off"],
    "FX": ["exchange rate", "currency", "franc", "yen", "dollar", "sterling", "twi", "appreciat", "depreciat",
           "foreign exchange"],
}
_PROTECT = ["U.S.", "U.K.", "p.m.", "a.m.", "e.g.", "i.e.", "No.", "Mr.", "Ms.", "Dr.", "St.", "Jan.", "Feb.",
            "Aug.", "Sept.", "Oct.", "Nov.", "Dec.", "approx."]
_DATE = re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December|"
                   r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.?\s+\d{1,2}(?:,\s*\d{4})?|\b\d{1,2}\s+"
                   r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
                   r"(?:\s+\d{4})?\b|\b20\d\d\b")


def _paragraphs(body: str) -> list[str]:
    """A line ending without punctuation is a heading unless the next line continues in lower case (PDF wrap)."""
    out: list[str] = []
    for line in (ln.strip() for ln in body.replace("­", "").splitlines()):
        if not line:
            continue
        if out and not re.search(r"[.!?:;]$", out[-1]) and re.match(r"[a-z(,]", line):
            out[-1] = f"{out[-1]} {line}"
        elif out and re.search(r"[,–-]$", out[-1]):
            out[-1] = f"{out[-1]} {line}"
        else:
            out.append(line)
    return out


def split_sentences(body: str) -> list[str]:
    sentences: list[str] = []
    for paragraph in _paragraphs(body):
        sentences += _split_flat(paragraph)
    return sentences


def _split_flat(paragraph: str) -> list[str]:
    flat = re.sub(r"\s+", " ", paragraph).strip()
    for i, token in enumerate(_PROTECT):
        flat = flat.replace(token, token.replace(".", f"\u0000{i}\u0000"))
    parts = re.split(r"(?<=[.!?;])\s+(?=[A-Z0-9\"“(‘'])", flat)
    out = []
    for part in parts:
        for i, token in enumerate(_PROTECT):
            part = part.replace(token.replace(".", f"\u0000{i}\u0000"), token)
        if len(part.split()) >= MIN_WORDS:
            out.append(part.strip())
    return out


def topic(sentence: str) -> str:
    lower = f" {sentence.lower()} "
    scores = {name: sum(len(re.findall(r"\b" + re.escape(word), lower)) for word in words)
              for name, words in TOPICS.items()}
    best = max(scores.values())
    return next(name for name, score in scores.items() if score == best) if best else "other"


def _key(sentence: str) -> str:
    """Comparison key: dates masked so a new meeting date alone is not a change."""
    return re.sub(r"\s+", " ", _DATE.sub("<date>", sentence)).strip().lower()


def diff_statements(previous: str, latest: str) -> list[dict[str, Any]]:
    """Changed sentences only: 'changed' (old→new), 'added' and 'removed'."""
    old, new = split_sentences(previous), split_sentences(latest)
    matcher = difflib.SequenceMatcher(a=[_key(s) for s in old], b=[_key(s) for s in new], autojunk=False)
    changes = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        olds, news = old[i1:i2], new[j1:j2]
        for k in range(max(len(olds), len(news))):
            before = olds[k] if k < len(olds) else None
            after = news[k] if k < len(news) else None
            if before and after:
                ratio = difflib.SequenceMatcher(a=before, b=after).ratio()
                if ratio < 0.4:                      # unrelated: report as removal + addition
                    changes.append({"kind": "removed", "old": before, "new": None, "topic": topic(before)})
                    changes.append({"kind": "added", "old": None, "new": after, "topic": topic(after)})
                    continue
                changes.append({"kind": "changed", "old": before, "new": after, "topic": topic(after),
                                "similarity": round(ratio, 2)})
            elif after:
                changes.append({"kind": "added", "old": None, "new": after, "topic": topic(after)})
            else:
                changes.append({"kind": "removed", "old": before, "new": None, "topic": topic(before)})
    return changes


async def latest_statements(bank: str, limit: int = 2) -> list[Any]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""SELECT doc_date, file_path, raw_text FROM cb_policy_documents
            WHERE bank = :b AND doc_type = :t AND raw_text IS NOT NULL ORDER BY doc_date DESC LIMIT :n"""),
                                     {"b": bank, "t": DOC_TYPE, "n": limit})
        return rows.all()


async def get_statement_diff(bank: str) -> dict[str, Any]:
    bank = bank.upper()
    rows = await latest_statements(bank)
    if len(rows) < 2:
        from app.services.cb_manual_imports import manual_sources
        reason = manual_sources(bank).get("statements")
        return {"bank": bank, "status": "manual_import" if reason else "unavailable",
                "reason": f"{reason}; upload statements at /admin/cb-imports" if reason
                else "Fewer than two statements stored"}
    latest, previous = rows
    changes = diff_statements(previous.raw_text, latest.raw_text)
    by_topic: dict[str, int] = {}
    for change in changes:
        by_topic[change["topic"]] = by_topic.get(change["topic"], 0) + 1
    return {"bank": bank, "status": "available", "method": "difflib sentence diff; dates masked; topic by keyword",
            "latest": {"date": latest.doc_date, "url": latest.file_path},
            "previous": {"date": previous.doc_date, "url": previous.file_path},
            "changed_count": len(changes), "by_topic": by_topic, "changes": changes}
