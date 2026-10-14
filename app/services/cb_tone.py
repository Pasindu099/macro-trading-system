"""Speaker tone, shift, lean, focus mix and committee balance from word-list scores (Step 12 Part F).

All outputs are labelled "word-list tone, v1" (method lexicon_v1). Tone-bearing sentences are
those with a non-zero score; neutral sentences count only in the focus mix.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import mean
from typing import Any

from sqlalchemy import text

from app.db.session import get_sessionmaker, session_scope
from app.ingestion.run_logger import run_logger
from app.processing.cb_officials import THEMES, is_voter, official
from app.processing.tone_lexicon import lexicon, score_sentence

METHOD = "lexicon_v1"
LABEL = "word-list tone, v1"
TONE_DAYS, BASELINE_DAYS = 30, 180
SHIFT_THRESHOLD, LEAN_THRESHOLD = 0.4, 0.5
MIN_SENTENCES = 3
WEEKS = 12
JOB_NAME = "job:cb_tone_scoring"


# ── Maths (pure) ──────────────────────────────────────────────────────

def lean(value: float | None) -> str | None:
    if value is None:
        return None
    return "hawkish" if value >= LEAN_THRESHOLD else "dovish" if value <= -LEAN_THRESHOLD else "neutral"


def speaker_profile(points: list[tuple[date, float, str]], today: date) -> dict[str, Any]:
    """points: (date, score, theme) for one speaker's extracted sentences."""
    recent_start = today - timedelta(days=TONE_DAYS)
    base_start = today - timedelta(days=BASELINE_DAYS)
    recent = [s for d, s, _ in points if recent_start < d <= today and s != 0]
    baseline = [s for d, s, _ in points if base_start < d <= recent_start and s != 0]
    tone = round(mean(recent), 3) if len(recent) >= MIN_SENTENCES else None
    base = round(mean(baseline), 3) if len(baseline) >= MIN_SENTENCES else None
    shift = round(tone - base, 3) if tone is not None and base is not None else None
    themes = Counter(t for d, _, t in points if recent_start < d <= today)
    total = sum(themes.values())
    return {"tone_30d": tone, "tone_sentences": len(recent), "baseline_6m": base, "baseline_sentences": len(baseline),
            "shift": shift, "shifting": shift is not None and abs(shift) >= SHIFT_THRESHOLD,
            "shift_direction": None if shift is None or abs(shift) < SHIFT_THRESHOLD else
            ("hawkish" if shift > 0 else "dovish"),
            "lean": lean(tone),
            "focus_mix": {name: round(themes[name] / total, 3) for name in [*THEMES, "other"] if themes[name]}
            if total else {}}


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def committee_balance(points: list[tuple[str, date, float]], today: date, voter_of=lambda s, y: None,
                      weeks: int = WEEKS) -> list[dict[str, Any]]:
    """points: (speaker, date, non-zero score). One row per week (Monday), oldest first."""
    first = week_start(today) - timedelta(weeks=weeks - 1)
    by_week: dict[date, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for speaker, day, score in points:
        if score != 0 and first <= day <= today:
            by_week[week_start(day)][speaker].append(score)
    rows = []
    for k in range(weeks):
        monday = first + timedelta(weeks=k)
        speakers = by_week.get(monday, {})
        entries = [{"speaker_id": s, "tone": round(mean(v), 3), "lean": lean(mean(v)), "sentences": len(v),
                    "voter": voter_of(s, monday.year)} for s, v in sorted(speakers.items())]
        counts = Counter(e["lean"] for e in entries)
        n = len(entries)
        rows.append({"week": monday, "active": n,
                     "shares": {k2: round(counts[k2] / n, 3) for k2 in ("hawkish", "neutral", "dovish")} if n else None,
                     "speakers": entries})
    return rows


# ── Scoring job ───────────────────────────────────────────────────────

async def score_pending(rescore: bool = False) -> int:
    """Score every extracted sentence without a lexicon_v1 score (or all, with rescore)."""
    lexicon.cache_clear()
    async with run_logger(JOB_NAME) as run:
        async with get_sessionmaker()() as session:
            rows = (await session.execute(text("""SELECT s.id, s.sentence FROM cb_speech_sentences s
                LEFT JOIN cb_sentence_scores c ON c.sentence_id = s.id AND c.method = :m
                WHERE c.sentence_id IS NULL OR :all"""), {"m": METHOD, "all": rescore})).all()
        values = []
        for row in rows:
            result = score_sentence(row.sentence)
            values.append({"i": row.id, "m": METHOD, "s": result["score"], "r": result["raw"],
                           "h": json.dumps([{k: v for k, v in h.items() if k != "start"} for h in result["hits"]])})
        for offset in range(0, len(values), 2000):
            async with session_scope() as session:
                await session.execute(text("""INSERT INTO cb_sentence_scores (sentence_id, method, score, raw_score, hits)
                    VALUES (:i, :m, :s, :r, CAST(:h AS jsonb))
                    ON CONFLICT (sentence_id, method) DO UPDATE SET score = EXCLUDED.score,
                        raw_score = EXCLUDED.raw_score, hits = EXCLUDED.hits, scored_at = now()"""),
                                      values[offset:offset + 2000])
        run.record_rows(len(values))
    return len(values)


# ── Read side ─────────────────────────────────────────────────────────

async def _points(bank: str, since: date) -> list[Any]:
    async with get_sessionmaker()() as session:
        return (await session.execute(text("""SELECT s.speaker_id, s.sentence_date AS d, s.theme,
                COALESCE(c.score, 0)::float AS score, s.source_type
            FROM cb_speech_sentences s
            LEFT JOIN cb_sentence_scores c ON c.sentence_id = s.id AND c.method = :m
            WHERE s.bank = :b AND s.speaker_id IS NOT NULL AND s.sentence_date >= :since"""),
                                        {"b": bank, "m": METHOD, "since": since})).all()


async def get_speakers(bank: str, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    rows = await _points(bank, today - timedelta(days=BASELINE_DAYS))
    by_speaker: dict[str, list[tuple[date, float, str]]] = defaultdict(list)
    sources: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        by_speaker[row.speaker_id].append((row.d, row.score, row.theme))
        sources[row.speaker_id][row.source_type] += 1
    speakers = []
    for speaker_id, points in by_speaker.items():
        person = official(bank, speaker_id) or {}
        speakers.append({"speaker_id": speaker_id, "name": person.get("name", speaker_id), "role": person.get("role"),
                         "voter": is_voter(bank, speaker_id, today.year), "last_seen": max(d for d, _, _ in points),
                         "sentences_by_source": dict(sources[speaker_id]), **speaker_profile(points, today)})
    speakers.sort(key=lambda s: s["last_seen"], reverse=True)
    return {"bank": bank, "method": METHOD, "label": LABEL, "as_of": today,
            "rules": {"tone_days": TONE_DAYS, "baseline_days": BASELINE_DAYS, "shift_threshold": SHIFT_THRESHOLD,
                      "lean_threshold": LEAN_THRESHOLD, "min_sentences": MIN_SENTENCES},
            "status": "available" if speakers else "empty", "speakers": speakers}


async def get_committee_balance(bank: str, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    rows = await _points(bank, week_start(today) - timedelta(weeks=WEEKS))
    weeks = committee_balance([(r.speaker_id, r.d, r.score) for r in rows], today,
                              voter_of=lambda s, y: is_voter(bank, s, y))
    return {"bank": bank, "method": METHOD, "label": LABEL, "as_of": today, "weeks": weeks,
            "status": "available" if any(w["active"] for w in weeks) else "empty"}
