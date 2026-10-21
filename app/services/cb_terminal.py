"""Central Bank Terminal panels (Step 12 Part G): one builder per panel, each returning a ctx with a state.

States: ok | empty | unavailable | pending | error (error is set by the route on exceptions).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.processing.cb_officials import THEMES, match_officials, official, officials
from app.services import (
    cb_projection_loaders, cb_statement_diff, cb_summary, cb_tone, cb_tracking, cb_upcoming, cb_votes, ecb_projections,
    ecb_tracking, fed_projections, fed_regime, news, cb_other_tracking,
)
from app.services.cb_manual_imports import manual_sources
from app.services.cb_summary import bank_config

BANKS = ("FED", "ECB", "BOE", "BOJ", "RBA", "BOC", "RBNZ", "SNB")
VIEWS = {"overview": ("summary", "balance", "statement", "speakers", "profile", "upcoming", "headlines", "library"),
         "projections": ("summary", "proj-revisions", "proj-dots", "proj-risk", "proj-tracking", "proj-vintages",
                         "proj-formats")}
FOCUS = [*THEMES, "other"]
VARIABLE_LABELS = {"real_gdp": "Real GDP", "unemployment_rate": "Unemployment rate", "pce_inflation": "PCE inflation",
                   "core_pce_inflation": "Core PCE inflation", "federal_funds_rate": "Fed funds rate",
                   "hicp_inflation": "HICP inflation", "core_hicp_inflation": "Core HICP inflation",
                   "cpi_inflation": "CPI inflation", "core_cpi_inflation": "Core CPI inflation",
                   "core_core_cpi_inflation": "Core-core CPI inflation",
                   "policy_rate": "Policy rate"}
BANK_READ_NOTES = {
    "BOC": "Internal policy-rate assumption is not published; read against the Bank's own guidance.",
    "RBNZ": "Published OCR track: compare the track with market pricing; low inflation with a rising track is hawkish.",
}
INFLATION_VAR = {"FED": "pce_inflation", "ECB": "hicp_inflation"}
READ_NOTES = {
    "own_views": "Each participant's own appropriate policy; low inflation alongside a high rate path is hawkish.",
    "market_curve": "Conditioned on market rates; inflation below target means market pricing is too tight.",
    "market_path": "Conditioned on the market cash-rate path; below target implies room to ease vs pricing.",
    "board_median": "Median of board members; assumption on rates not published.",
    "constant_rate": "Policy rate held constant; inflation falling below target at a constant rate implies cuts.",
}


def _pending(message: str) -> dict[str, Any]:
    return {"state": "pending", "message": message}


def _manual(bank: str, kind: str) -> str | None:
    reason = manual_sources(bank).get(kind)
    return f"Manual import source ({reason}); add data at /admin/cb-imports." if reason else None


# ── Overview panels ───────────────────────────────────────────────────

async def summary(bank: str, **_: Any) -> dict[str, Any]:
    data = await cb_summary.get_summary(bank)
    diff = await cb_statement_diff.get_statement_diff(bank)
    cfg = bank_config()[bank]
    return {"state": "ok", "bank": bank, "name": data["name"], "currency": data["currency"],
            "mandate": cfg["mandate"].replace("_", " "), "summary": data,
            "guidance": (f"{diff['changed_count']} statement sentences changed since {diff['previous']['date']:%d %b}"
                         if diff["status"] == "available" else diff.get("reason", "statement diff unavailable"))}


async def balance(bank: str, **_: Any) -> dict[str, Any]:
    if bank in ("BOE", "BOJ"):
        votes = await cb_votes.committee_balance(bank)
        tone = await cb_tone.get_committee_balance(bank)
        if votes["status"] != "available":
            return {"state": "pending", "message": "Published policy votes pending.", "votes": votes}
        return {"state": "ok", "votes": votes, "data": tone,
                "peak": max((w["active"] for w in tone["weeks"]), default=0) or 1}
    data = await cb_tone.get_committee_balance(bank)
    if not any(w["active"] for w in data["weeks"]):
        return {"state": "empty", "message": _manual(bank, "speeches") or "No attributed speeches or comments in 12 weeks."}
    peak = max(w["active"] for w in data["weeks"]) or 1
    return {"state": "ok", "data": data, "peak": peak}


async def statement(bank: str, **_: Any) -> dict[str, Any]:
    data = await cb_statement_diff.get_statement_diff(bank)
    if data["status"] != "available":
        return {"state": "pending" if data["status"] == "manual_import" else "unavailable", "message": data["reason"]}
    return {"state": "ok", "data": data, "changes": data["changes"][:8], "more": max(0, len(data["changes"]) - 8)}


async def speakers(bank: str, **_: Any) -> dict[str, Any]:
    data = await cb_tone.get_speakers(bank)
    if not data["speakers"]:
        return {"state": "empty", "message": _manual(bank, "speeches") or "No attributed speeches or comments yet."}
    return {"state": "ok", "data": data, "focus": FOCUS}


async def profile(bank: str, speaker: str | None = None, **_: Any) -> dict[str, Any]:
    data = await cb_tone.get_speakers(bank)
    people = data["speakers"]
    if not people:
        return {"state": "empty", "message": "No speaker selected: no attributed speeches yet."}
    chosen = next((p for p in people if p["speaker_id"] == speaker), people[0])
    async with get_sessionmaker()() as session:
        # investingLive sometimes republishes a headline: one row per distinct sentence and day.
        points = (await session.execute(text("""SELECT sentence_date, sentence, min(theme) AS theme
            FROM cb_speech_sentences WHERE bank = :b AND speaker_id = :s
            GROUP BY sentence_date, sentence ORDER BY sentence_date DESC, min(position) LIMIT 8"""),
            {"b": bank, "s": chosen["speaker_id"]})).mappings().all()
        dots = []
        if data["tone_method"]:
            dots = (await session.execute(text("""SELECT s.sentence_date AS d, c.score::float AS score
                FROM cb_speech_sentences s JOIN cb_sentence_scores c ON c.sentence_id = s.id AND c.method = :m
                WHERE s.bank = :b AND s.speaker_id = :s AND c.score <> 0 AND s.sentence_date >= :since"""),
                {"b": bank, "s": chosen["speaker_id"], "m": data["tone_method"],
                 "since": date.today() - timedelta(days=180)})).mappings().all()
    start = date.today() - timedelta(days=180)
    plotted = [{"x": round((d["d"] - start).days / 180 * 100, 1), "y": round((2 - d["score"]) / 4 * 100, 1),
                "score": d["score"], "date": d["d"]} for d in dots]
    return {"state": "ok", "data": data, "speaker": chosen, "points": points, "dots": plotted, "people": people}


async def upcoming(bank: str, **_: Any) -> dict[str, Any]:
    data = await cb_upcoming.get_upcoming(bank)
    rows = []
    for meeting in data["meetings"]:
        window = meeting["blackout"]
        if window.get("start"):
            rows.append({"date": window["start"], "what": f"Blackout starts (to {window['end']:%d %b})",
                         "tag": "blackout" + ("" if window.get("verified") else " · unverified")})
        rows.append({"date": meeting["date"], "what": "Policy decision" + (" + " + meeting["projection_name"]
                     if meeting["projections"] else ""), "tag": "meeting" if meeting["official"] else "meeting · unofficial"})
        if meeting["minutes"]:
            rows.append({"date": meeting["minutes"]["date"], "what": meeting["minutes"]["name"].capitalize(),
                         "tag": "minutes" if meeting["minutes"]["exact"] else "minutes · approx."})
    for event in data["speeches"].get("events", [])[:8]:
        rows.append({"date": event["date"], "what": f"{event['speaker'] or 'Official'}: {event.get('event') or event['kind']}",
                     "tag": "speech"})
    rows.sort(key=lambda r: r["date"])
    return {"state": "ok" if rows else "empty", "rows": rows[:16], "speeches": data["speeches"],
            "message": "No upcoming items."}


async def headlines(bank: str, **_: Any) -> dict[str, Any]:
    context = officials()[bank].get("context", [])
    rows = [{key: row[key] for key in ("id", "title", "url", "published_at")}
            for row in await news.raw_headlines(since=datetime.now(UTC) - timedelta(days=21), limit=400)]
    picked, seen = [], set()
    for row in rows:
        if row["title"] in seen:
            continue
        named = [s for b, s, _ in match_officials(row["title"], on=row["published_at"].date()) if b == bank]
        if named or any(word in row["title"] for word in context):
            picked.append({**row, "speakers": named})
            seen.add(row["title"])
        if len(picked) >= 10:
            break
    return {"state": "ok" if picked else "empty", "rows": picked,
            "message": "No investingLive headlines for this bank in 21 days."}


async def library(bank: str, **_: Any) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text("""SELECT sp.speech_date, sp.title, sp.url, sp.speaker_id, sp.source,
                (SELECT count(*) FROM cb_speech_sentences s WHERE s.source_type = 'speech' AND s.source_id = sp.id) AS points
            FROM cb_speeches sp WHERE sp.bank = :b ORDER BY sp.speech_date DESC LIMIT 6"""), {"b": bank})).mappings().all()
    speeches = [{**r, "who": (official(bank, r["speaker_id"]) or {}).get("name") if r["speaker_id"] else None,
                 "link": r["url"] if r["url"].startswith("http") else None} for r in rows]
    return {"state": "ok", "speeches": speeches, "manual": _manual(bank, "speeches")}


# ── Projections panels ────────────────────────────────────────────────

async def proj_revisions(bank: str, **_: Any) -> dict[str, Any]:
    if bank == "FED":
        current = (await fed_projections.get_projections("latest"))["rounds"][0]
        revisions = await cb_tracking.get_revisions()
        previous = {(r["variable"], r["horizon"]): r for r in revisions.get("revisions", [])}
        horizons = sorted({h for v in current["variables"].values() for h in v}, key=lambda h: (h == "longer_run", h))
        rows = [{"name": VARIABLE_LABELS.get(var, var), "cells": [
            {"horizon": h, **values.get(h, {}), "previous": previous.get((var, h), {}).get("previous_median"),
             "change": previous.get((var, h), {}).get("median_change")} for h in horizons]}
            for var, values in current["variables"].items()]
        return {"state": "ok", "round": revisions.get("round"), "previous_round": revisions.get("previous_round"),
                "horizons": horizons, "rows": rows, "flag": revisions.get("reaction_function"), "stats": "median · CT · range"}
    if bank == "ECB":
        revisions = await ecb_tracking.get_revisions()
        if revisions.get("status") == "unavailable":
            return {"state": "unavailable", "message": revisions["reason"]}
        data = await ecb_projections.get_projections("latest")
        return _point_rows(data, revisions.get("revisions", []), revisions)
    if bank not in cb_projection_loaders.BANKS:
        return {"state": "unavailable", "message": "No projections for this bank."}
    revisions = await cb_projection_loaders.get_revisions(bank)
    data = await cb_projection_loaders.get_projections(bank)
    if data["status"] != "available":
        return {"state": "pending", "message": _manual(bank, "projections") or data["reason"]}
    return _point_rows(data, revisions.get("revisions", []), revisions)


def _point_rows(data: dict, revisions: list[dict], meta: dict) -> dict[str, Any]:
    current = data["rounds"][0]
    by_key = {(r["variable"], r["horizon"]): r for r in revisions}
    horizons = sorted({h for v in current["variables"].values() for h in v if h != "constant"})[:8]
    rows = []
    for var, values in current["variables"].items():
        cells = []
        for h in horizons:
            stat = values.get(h, {})
            value = stat.get("point", stat.get("median"))
            rev = by_key.get((var, h), {})
            cells.append({"horizon": h, "median": value, "previous": rev.get("previous", rev.get("previous_median")),
                          "change": rev.get("change", rev.get("median_change")),
                          "range_low": stat.get("range_low"), "range_high": stat.get("range_high")})
        rows.append({"name": VARIABLE_LABELS.get(var, var), "cells": cells})
    return {"state": "ok", "round": current["release_date"], "previous_round": meta.get("previous_round"),
            "horizons": horizons, "rows": rows, "flag": meta.get("reaction_function"),
            "stats": ("ranges only" if current.get("coverage") == "ranges only" else "board median · range")
            if data["bank"] == "BOJ" else "point projection",
            "source_date": current.get("source_date"), "source_url": current.get("source_url"),
            "assumption": current["variables"].get("policy_rate", {}).get("constant", {}).get("assumption")}


async def proj_dots(bank: str, **_: Any) -> dict[str, Any]:
    if bank != "FED":
        return {"state": "unavailable", "message": "Only the Fed publishes participants' rate dots."}
    dots = await fed_projections.get_dots()
    gap = await fed_regime.get_gap()
    market = {g["horizon"]: g["market"] for g in gap.get("gaps", []) if g.get("market") is not None}
    medians = {g["horizon"]: g["sep_median"] for g in gap.get("gaps", [])}
    rates = [d["rate"] for points in dots["horizons"].values() for d in points] + list(market.values())
    low, high = (min(rates) - 0.25, max(rates) + 0.25) if rates else (0, 5)
    y = lambda r: round((high - r) / (high - low) * 100, 1)  # noqa: E731
    columns = [{"horizon": h, "dots": [{**d, "y": y(d["rate"])} for d in points],
                "median": medians.get(h), "median_y": y(medians[h]) if medians.get(h) is not None else None,
                "market": market.get(h), "market_y": y(market[h]) if h in market else None}
               for h, points in dots["horizons"].items()]
    ticks = [{"label": f"{low + (high - low) * k / 4:.2f}", "y": 100 - k * 25} for k in range(5)]
    return {"state": "ok", "round": dots["release_date"], "columns": columns, "ticks": ticks}


async def proj_risk(bank: str, **_: Any) -> dict[str, Any]:
    if bank != "FED":
        return {"state": "unavailable", "message": "Risk-balance counts are published by the Fed only."}
    data = await fed_projections.get_risk_balance()
    rows = []
    for var, kinds in data["variables"].items():
        risk, unc = kinds.get("risk", {}), kinds.get("uncertainty", {})
        n = risk.get("participants") or 1
        rows.append({"name": VARIABLE_LABELS.get(var, var), "risk": risk, "uncertainty": unc,
                     "pct": {k: round(risk.get(k, 0) / n * 100, 1) for k in
                             ("lower_or_downside", "similar_or_balanced", "higher_or_upside")}})
    return {"state": "ok", "round": data["release_date"], "rows": rows}


async def proj_tracking(bank: str, **_: Any) -> dict[str, Any]:
    if bank == "FED":
        data = await cb_tracking.get_tracking()
    elif bank == "ECB":
        data = await ecb_tracking.get_tracking()
        if data.get("status") == "unavailable":
            return {"state": "unavailable", "message": data["reason"]}
    elif bank in cb_other_tracking.BANKS:
        data = await cb_other_tracking.get_tracking(bank)
        if data.get("status") == "unavailable":
            return {"state": "unavailable", "message": data["reason"]}
    else:
        return _pending("CB Tracking for this bank is pending (needs its data series mapped to its projections).")
    return {"state": "ok", "data": data, "rows": [{"name": VARIABLE_LABELS.get(k, k), **v}
                                                  for k, v in data.get("variables", {}).items()]}


async def proj_vintages(bank: str, **_: Any) -> dict[str, Any]:
    variable = INFLATION_VAR.get(bank, "cpi_inflation")
    year = date.today().year
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text("""SELECT release_date, horizon, value::float AS value FROM cb_projection_values
            WHERE bank = :b AND variable = :v AND horizon IN (:y, :q) AND stat IN ('median', 'point')
            ORDER BY release_date"""), {"b": bank, "v": variable, "y": str(year), "q": f"{year}Q4"})).mappings().all()
    by_round: dict[date, dict[str, float]] = {}
    for row in rows:
        by_round.setdefault(row["release_date"], {})[row["horizon"]] = row["value"]
    points = [{"round": d, "value": h.get(f"{year}Q4", h.get(str(year)))} for d, h in by_round.items()][-8:]
    if not points:
        return {"state": "pending", "message": _manual(bank, "projections") or f"No {year} projections stored."}
    top = max(abs(p["value"]) for p in points) or 1
    for p in points:
        p["height"] = round(abs(p["value"]) / top * 100, 1)
    return {"state": "ok", "points": points, "label": VARIABLE_LABELS.get(variable, variable), "year": year}


async def proj_formats(bank: str, **_: Any) -> dict[str, Any]:
    rows = []
    for code in BANKS:
        cfg = bank_config()[code]
        rows.append({"bank": code, "current": code == bank, "publication": cfg["projections"]["name"],
                     "path": cfg["rate_path_type"].replace("_", " "), "conditioning": cfg["conditioning"].replace("_", " "),
                     "read": BANK_READ_NOTES.get(code, READ_NOTES.get(cfg["conditioning"], "")),
                     "manual": code != "BOJ" and "projections" in manual_sources(code)})
    return {"state": "ok", "rows": rows}


PANELS = {"summary": summary, "balance": balance, "statement": statement, "speakers": speakers, "profile": profile,
          "upcoming": upcoming, "headlines": headlines, "library": library, "proj-revisions": proj_revisions,
          "proj-dots": proj_dots, "proj-risk": proj_risk, "proj-tracking": proj_tracking,
          "proj-vintages": proj_vintages, "proj-formats": proj_formats}
TITLES = {"summary": "Summary", "balance": "Committee balance", "statement": "Statement language tracker",
          "speakers": "Speaker tracker", "profile": "Profile", "upcoming": "Upcoming",
          "headlines": "Latest CB headlines", "library": "Speech library",
          "proj-revisions": "Projections and revisions", "proj-dots": "Dots vs market",
          "proj-risk": "Risk balance and uncertainty", "proj-tracking": "CB Tracking",
          "proj-vintages": "How this year's view evolved", "proj-formats": "How each central bank publishes"}
