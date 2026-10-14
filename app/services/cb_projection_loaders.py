"""Projection loaders, revisions and reaction-function flags for BoE, BoC, RBNZ and SNB (Step 12 Part B).

BoJ (Outlook Report) is PDF-only and RBA blocks scripted clients (HTTP 403); both
report as unavailable. Round dates are the bank's decision date in the
publication month, taken from the stored rate-decision history.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text

from app.db.session import get_sessionmaker, session_scope
from app.ingestion.run_logger import run_logger
from app.processing import cb_projection_parsers as parsers
from app.processing.xlsx_reader import read_sheet, sheet_names
from app.services.cb_summary import DECISIONS, bank_config
from app.services.cb_tracking import reaction_flag

JOB_NAME = "job:cb_projections"
BANKS = ("BOE", "BOJ", "RBA", "RBNZ", "BOC", "SNB")
LOADED = ("BOE", "BOC", "RBNZ", "SNB")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/126.0 Safari/537.36", "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
UNAVAILABLE = {"BOJ": "Outlook Report forecasts are published only in PDF; skipped (no LLM/PDF parsing)",
               "RBA": "rba.gov.au returns HTTP 403 to scripted clients; Statement on Monetary Policy tables not fetched"}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
BOE_MONTHS_BEFORE_2026 = [2, 5, 8, 11]
BOE_FIRST_HTML_YEAR = 2022            # 2020-2021 reports are PDF-only
RBNZ_FILE = re.compile(r"mps([a-z]{3})(\d{2})-data\.xlsx$|([a-z]{3})[a-z]*-20(\d{2})-monetary-policy-statement-data\.xlsx$")
SNB_CUBE = "https://data.snb.ch/api/cube/snbiprogq"
INFLATION = {"BOE": "cpi_inflation", "BOC": "cpi_inflation", "RBNZ": "cpi_inflation", "SNB": "cpi_inflation"}


# ── Round dates ───────────────────────────────────────────────────────

async def decision_dates(bank: str) -> list[date]:
    country, name = DECISIONS[bank]
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""
            SELECT DISTINCT r.released_at::date AS d FROM indicator_releases r JOIN indicators i ON i.id = r.indicator_id
            WHERE i.country_code = :c AND i.canonical_name = :n AND (r.actual IS NOT NULL OR :keep_null)
            ORDER BY 1"""), {"c": country, "n": name, "keep_null": bank == "SNB"})   # SNB decisions arrive without values
        return [r.d for r in rows]


def date_in_month(dates: list[date], year: int, month: int) -> date | None:
    return next((d for d in dates if d.year == year and d.month == month), None)


async def _write_round(bank: str, rows: list[dict[str, Any]]) -> int:
    release = rows[0]["release_date"]
    async with session_scope() as session:
        await session.execute(text("DELETE FROM cb_projection_values WHERE bank = :b AND release_date = :d"),
                              {"b": bank, "d": release})
        await session.execute(text("""INSERT INTO cb_projection_values (bank, release_date, variable, horizon, stat, value)
            VALUES (:bank, :release_date, :variable, :horizon, :stat, :value)"""), rows)
    return len(rows)


# ── Per-bank discovery ────────────────────────────────────────────────

async def _boe_rounds(client: httpx.AsyncClient, since: date, summary: dict) -> list[list[dict]]:
    dates = await decision_dates("BOE")
    rounds = []
    for year in range(max(since.year, BOE_FIRST_HTML_YEAR), date.today().year + 1):
        months = BOE_MONTHS_BEFORE_2026 if year < 2026 else bank_config()["BOE"]["projections"]["months"]
        for month in months:
            release = date_in_month(dates, year, month)
            key = f"{year}-{month:02d}"
            if release is None or release > date.today():
                continue
            url = f"https://www.bankofengland.co.uk/monetary-policy-report/{year}/{MONTHS[month - 1]}-{year}"
            response = await client.get(url)
            if response.status_code != 200:
                summary["skipped"][key] = f"HTTP {response.status_code}"
                continue
            try:
                rounds.append(parsers.parse_boe_mpr(response.text, release))
            except parsers.SkipRound as exc:
                summary["skipped"][key] = str(exc)
            except ValueError as exc:
                summary["errors"][key] = str(exc)
    summary["skipped"]["2020-2021"] = "PDF only"
    return rounds


async def _boc_rounds(client: httpx.AsyncClient, since: date, summary: dict) -> list[list[dict]]:
    rounds = []
    for release in await decision_dates("BOC"):
        if release < since or release > date.today() or release.month not in (1, 4, 7, 10):
            continue
        response = await client.get(f"https://www.bankofcanada.ca/publications/mpr/mpr-{release}/projections/")
        if response.status_code != 200:
            summary["skipped"][str(release)] = f"HTTP {response.status_code}"
            continue
        if f"mpr-{release:%Y-%m}" not in str(response.url):     # old rounds redirect to a newer report
            summary["skipped"][str(release)] = "No HTML projection page (PDF only or scenario-based report)"
            continue
        try:
            rounds.append(parsers.parse_boc_mpr(response.text, release))
        except ValueError as exc:
            summary["errors"][str(release)] = str(exc)
    return rounds


def rbnz_rounds_from_files(files: dict[str, bytes], dates: list[date]) -> tuple[list[list[dict]], dict[str, str]]:
    rounds, skipped = [], {}
    for name, content in sorted(files.items()):
        match = RBNZ_FILE.search(name)
        if not match:
            continue
        month = [m[:3] for m in MONTHS].index(match.group(1) or match.group(3)) + 1
        year = 2000 + int(match.group(2) or match.group(4))
        release = date_in_month(dates, year, month)
        if release is None:
            release = date(year, month, 1)
            skipped[name] = "No OCR decision stored for that month; first of month used"
        sheet = next((s for s in sheet_names(content) if s.strip() in ("Projections", "Baseline scenario")), None)
        try:
            if sheet is None:
                raise ValueError("No Projections/Baseline scenario sheet")
            rounds.append(parsers.parse_rbnz_projections(read_sheet(content, sheet), release))
        except ValueError as exc:
            skipped[name] = str(exc)
    return rounds, skipped


async def _rbnz_rounds(client: httpx.AsyncClient, since: date, summary: dict, files_dir: Path | None) -> list[list[dict]]:
    dates = await decision_dates("RBNZ")
    files: dict[str, bytes] = {}
    if files_dir is not None:
        files = {p.name: p.read_bytes() for p in files_dir.glob("*.xlsx")}
    else:
        sitemap = await client.get("https://www.rbnz.govt.nz/sitemap.xml")
        if sitemap.status_code != 200:
            summary["skipped"]["all"] = f"rbnz.govt.nz HTTP {sitemap.status_code} (Cloudflare); load with --rbnz-dir"
            return []
        pages = sorted(set(re.findall(r"<loc>([^<]*/monetary-policy-statements/20\d\d/[^<]*/monetary-policy-statement-"
                                      r"[^<]*?)</loc>", sitemap.text)))
        for page in pages:
            if page.endswith("web-version") or page.count("/") > 9:
                continue
            html = (await client.get(page)).text
            link = re.search(r'href="([^"]+(?:mps[a-z]{3}\d{2}|monetary-policy-statement)-data\.xlsx)"', html)
            if link:
                content = (await client.get(httpx.URL("https://www.rbnz.govt.nz").join(link.group(1)))).content
                files[link.group(1).rsplit("/", 1)[-1]] = content
    rounds, skipped = rbnz_rounds_from_files(files, dates)
    summary["skipped"].update(skipped)
    return [r for r in rounds if r[0]["release_date"] >= since]


async def _snb_rounds(client: httpx.AsyncClient, since: date, summary: dict) -> list[list[dict]]:
    dates = await decision_dates("SNB")
    payload = (await client.get(f"{SNB_CUBE}/data/csv/en")).text
    labels = parsers.parse_snb_labels(json.loads((await client.get(f"{SNB_CUBE}/dimensions/en")).text.lstrip("﻿")))
    releases = {}
    for code in labels:
        ym = parsers.snb_round_month(code)
        if ym:
            found = date_in_month(dates, *ym)
            if found:
                releases[code] = found
            elif date(*ym, 1) >= since:
                summary["skipped"][code] = "No decision date stored; first of month used"
    return list(parsers.parse_snb_cube(payload, labels, releases, since).values())


async def load_projections(banks: tuple[str, ...] = LOADED, since: date = date(2020, 1, 1),
                           rbnz_dir: Path | None = None) -> dict[str, Any]:
    """Idempotent: each parsed round replaces that bank's rows for its release date."""
    result: dict[str, Any] = {}
    async with run_logger(JOB_NAME) as run, httpx.AsyncClient(timeout=60, follow_redirects=True,
                                                                headers=HEADERS) as client:
        for bank in banks:
            summary: dict[str, Any] = {"rounds": 0, "rows": 0, "skipped": {}, "errors": {}}
            if bank in UNAVAILABLE:
                result[bank] = {"status": "unavailable", "reason": UNAVAILABLE[bank]}
                continue
            try:
                if bank == "BOE":
                    rounds = await _boe_rounds(client, since, summary)
                elif bank == "BOC":
                    rounds = await _boc_rounds(client, since, summary)
                elif bank == "RBNZ":
                    rounds = await _rbnz_rounds(client, since, summary, rbnz_dir)
                else:
                    rounds = await _snb_rounds(client, since, summary)
            except (httpx.HTTPError, ValueError) as exc:
                summary["errors"]["fetch"] = str(exc)
                run.errors.append(f"{bank}: {exc}")
                rounds = []
            for rows in rounds:
                summary["rows"] += await _write_round(bank, rows)
                summary["rounds"] += 1
            run.record_rows(summary["rows"])
            result[bank] = summary
    return result


# ── Read side: projections and revisions ──────────────────────────────

async def rounds(bank: str) -> list[date]:
    async with get_sessionmaker()() as session:
        return list((await session.execute(text("SELECT DISTINCT release_date FROM cb_projection_values "
                                                "WHERE bank = :b ORDER BY 1"), {"b": bank})).scalars())


async def round_values(bank: str, release: date) -> dict[tuple[str, str, str], float]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""SELECT variable, horizon, stat, value::float AS value
            FROM cb_projection_values WHERE bank = :b AND release_date = :d"""), {"b": bank, "d": release})
        return {(r.variable, r.horizon, r.stat): r.value for r in rows}


def compute_revisions(bank: str, now: dict, before: dict, latest: date) -> dict[str, Any]:
    rows = []
    for (variable, horizon, stat), value in sorted(now.items()):
        if stat != "point" or (variable, horizon, stat) not in before:
            continue
        rows.append({"variable": variable, "horizon": horizon, "value": value,
                     "previous": before[(variable, horizon, stat)],
                     "change": round(value - before[(variable, horizon, stat)], 3)})
    by_key = {(r["variable"], r["horizon"]): r["change"] for r in rows}
    horizon = next((h for h in (f"{latest.year}Q4", str(latest.year), f"{latest.year + 1}Q4", str(latest.year + 1))
                    if (INFLATION[bank], h) in by_key), None)
    inflation = by_key.get((INFLATION[bank], horizon)) if horizon else None
    conditioning = bank_config()[bank]["conditioning"]
    if conditioning == "constant_rate" or not any(v == "policy_rate" for v, _, s in now if s == "point"):
        flag = {"flag": "unavailable", "reason": f"No projected policy-rate path ({conditioning})"}
    else:
        rate = by_key.get(("policy_rate", horizon)) if horizon else None
        flag = {"flag": reaction_flag(inflation, rate), "inflation_revision": inflation, "policy_rate_revision": rate}
        if inflation is not None and inflation >= 0.2 and rate is None:
            flag = {**flag, "flag": "unavailable", "reason": "No policy-rate value at a horizon common to both rounds"}
    return {"revisions": rows, "reaction_function": {**flag, "horizon": horizon, "conditioning": conditioning,
            "rule": "Inflation up ≥ 0.2pp: policy path unchanged → tolerance; up → response (Fed rule)"}}


async def get_revisions(bank: str) -> dict[str, Any]:
    dates = await rounds(bank)
    if len(dates) < 2:
        return {"bank": bank, "status": "unavailable", "reason": UNAVAILABLE.get(bank, "Fewer than two rounds stored")}
    latest, previous = dates[-1], dates[-2]
    result = compute_revisions(bank, await round_values(bank, latest), await round_values(bank, previous), latest)
    return {"bank": bank, "status": "available", "round": latest, "previous_round": previous, **result}


async def get_projections(bank: str, round_: str = "latest") -> dict[str, Any]:
    dates = await rounds(bank)
    cfg = bank_config()[bank]
    meta = {"bank": bank, "publication": cfg["projections"]["name"], "conditioning": cfg["conditioning"],
            "rate_path_type": cfg["rate_path_type"], "round_date_method": "decision date in publication month"}
    if not dates:
        return {**meta, "status": "unavailable", "reason": UNAVAILABLE.get(bank, "No projections stored")}
    wanted = dates if round_ == "all" else [dates[-1]] if round_ == "latest" else [date.fromisoformat(round_)]
    out = []
    for day in wanted:
        variables: dict[str, dict] = {}
        for (variable, horizon, stat), value in sorted((await round_values(bank, day)).items()):
            variables.setdefault(variable, {}).setdefault(horizon, {})[stat] = value
        out.append({"release_date": day, "variables": variables})
    return {**meta, "status": "available", "rounds": out}
