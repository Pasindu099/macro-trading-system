"""Speaker tone, shift, lean, focus mix and committee balance from word-list scores (Step 12 Part F).

Tone is shown only for a method that passes the labelled-precision gate (cb_tone_gate). Tone-bearing sentences are
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
from app.processing.tone_lexicon import SCORERS, lexicon, v2_rules
from app.services.cb_tone_gate import display_method

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

METHODS = tuple(SCORERS)
PENDING_LABEL = "Tone: pending AI layer"


async def score_pending(rescore: bool = False, methods: tuple[str, ...] = METHODS) -> dict[str, int]:
    """Score every extracted sentence lacking a score for each method (or all, with rescore)."""
    lexicon.cache_clear()
    v2_rules.cache_clear()
    written: dict[str, int] = {}
    async with run_logger(JOB_NAME) as run:
        for method in methods:
            async with get_sessionmaker()() as session:
                rows = (await session.execute(text("""SELECT s.id, s.sentence FROM cb_speech_sentences s
                    LEFT JOIN cb_sentence_scores c ON c.sentence_id = s.id AND c.method = :m
                    WHERE c.sentence_id IS NULL OR :all"""), {"m": method, "all": rescore})).all()
            values = []
            for row in rows:
                result = SCORERS[method](row.sentence)
                hits = [{k: v for k, v in h.items() if k != "start"} for h in result["hits"] + result.get("dropped", [])]
                values.append({"i": row.id, "m": method, "s": result["score"], "r": result["raw"], "h": json.dumps(hits)})
            for offset in range(0, len(values), 2000):
                async with session_scope() as session:
                    await session.execute(text("""INSERT INTO cb_sentence_scores (sentence_id, method, score, raw_score, hits)
                        VALUES (:i, :m, :s, :r, CAST(:h AS jsonb))
                        ON CONFLICT (sentence_id, method) DO UPDATE SET score = EXCLUDED.score,
                            raw_score = EXCLUDED.raw_score, hits = EXCLUDED.hits, scored_at = now()"""),
                                          values[offset:offset + 2000])
            written[method] = len(values)
        run.record_rows(sum(written.values()))
    return written


# ── Read side (gated) ─────────────────────────────────────────────────

async def _points(bank: str, since: date, method: str | None) -> list[Any]:
    async with get_sessionmaker()() as session:
        return (await session.execute(text("""SELECT s.speaker_id, s.sentence_date AS d, s.theme, s.source_type,
                s.source_id, COALESCE(c.score, 0)::float AS score
            FROM cb_speech_sentences s
            LEFT JOIN cb_sentence_scores c ON c.sentence_id = s.id AND c.method = :m
            WHERE s.bank = :b AND s.speaker_id IS NOT NULL AND s.sentence_date >= :since"""),
                                        {"b": bank, "m": method or "", "since": since})).all()


def _gate_block(method: str | None, gate: dict[str, Any]) -> dict[str, Any]:
    return {"tone_method": method, "label": f"word-list tone, {method.split('_')[-1]}" if method else PENDING_LABEL,
            "tone_status": "available" if method else "pending", "tone_gate": gate}


async def get_speakers(bank: str, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    method, gate = display_method()
    rows = await _points(bank, today - timedelta(days=BASELINE_DAYS), method)
    by_speaker: dict[str, list[tuple[date, float, str]]] = defaultdict(list)
    items: dict[str, dict[str, set]] = defaultdict(lambda: {"speech": set(), "comment": set()})
    recent: dict[str, dict[str, set]] = defaultdict(lambda: {"speech": set(), "comment": set()})
    for row in rows:
        by_speaker[row.speaker_id].append((row.d, row.score, row.theme))
        items[row.speaker_id][row.source_type].add(row.source_id)
        if row.d > today - timedelta(days=TONE_DAYS):
            recent[row.speaker_id][row.source_type].add(row.source_id)
    speakers = []
    for speaker_id, points in by_speaker.items():
        person = official(bank, speaker_id) or {}
        profile = speaker_profile(points, today)
        if method is None:          # gate closed: keep focus mix and counts, hide tone
            profile = {"focus_mix": profile["focus_mix"]}
        speakers.append({"speaker_id": speaker_id, "name": person.get("name", speaker_id), "role": person.get("role"),
                         "voter": is_voter(bank, speaker_id, today.year), "last_seen": max(d for d, _, _ in points),
                         "speeches_6m": len(items[speaker_id]["speech"]), "comments_6m": len(items[speaker_id]["comment"]),
                         "speeches_30d": len(recent[speaker_id]["speech"]),
                         "comments_30d": len(recent[speaker_id]["comment"]), **profile})
    speakers.sort(key=lambda s: s["last_seen"], reverse=True)
    return {"bank": bank, "as_of": today, **_gate_block(method, gate),
            "rules": {"tone_days": TONE_DAYS, "baseline_days": BASELINE_DAYS, "shift_threshold": SHIFT_THRESHOLD,
                      "lean_threshold": LEAN_THRESHOLD, "min_sentences": MIN_SENTENCES},
            "status": "available" if speakers else "empty", "speakers": speakers}


async def get_committee_balance(bank: str, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    method, gate = display_method()
    rows = await _points(bank, week_start(today) - timedelta(weeks=WEEKS), method)
    if method is None:
        weekly: dict[date, set] = defaultdict(set)
        for row in rows:
            if row.d >= week_start(today) - timedelta(weeks=WEEKS - 1):
                weekly[week_start(row.d)].add(row.speaker_id)
        first = week_start(today) - timedelta(weeks=WEEKS - 1)
        weeks = [{"week": first + timedelta(weeks=k), "active": len(weekly.get(first + timedelta(weeks=k), ())),
                  "shares": None} for k in range(WEEKS)]
        return {"bank": bank, "as_of": today, **_gate_block(None, gate), "weeks": weeks, "status": "pending"}
    weeks = committee_balance([(r.speaker_id, r.d, r.score) for r in rows], today,
                              voter_of=lambda s, y: is_voter(bank, s, y))
    return {"bank": bank, "as_of": today, **_gate_block(method, gate), "weeks": weeks,
            "status": "available" if any(w["active"] for w in weeks) else "empty"}
