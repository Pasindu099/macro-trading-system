"""Projection loaders, revisions and reaction-function flags for BoE, BoC, RBNZ and SNB (Step 12 Part B).

BoJ Outlook highlights and parseable PDF forecast tables use official sources.
RBA remains a manual import. Other banks use stored decision dates.
"""

from __future__ import annotations

import io
import json
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader
from sqlalchemy import text

from app.services import source_identity

from app.db.session import get_sessionmaker, session_scope
from app.ingestion.run_logger import run_logger
from app.processing import cb_projection_parsers as parsers
from app.processing.xlsx_reader import read_sheet, sheet_names
from app.services.cb_manual_imports import is_manual, manual_sources, mark_manual_run
from app.services.cb_summary import DECISIONS, bank_config
from app.services.cb_tracking import reaction_flag

JOB_NAME = "job:cb_projections"
BANKS = ("BOE", "BOJ", "RBA", "RBNZ", "BOC", "SNB")
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
BOE_MONTHS_BEFORE_2026 = [2, 5, 8, 11]
BOE_FIRST_HTML_YEAR = 2022            # 2020-2021 reports are PDF-only
RBNZ_FILE = re.compile(r"mps([a-z]{3})(\d{2})-data\.xlsx$|([a-z]{3})[a-z]*-20(\d{2})-monetary-policy-statement-data\.xlsx$")
SNB_CUBE = "https://data.snb.ch/api/cube/snbiprogq"
INFLATION = {"BOE": "cpi_inflation", "BOJ": "core_cpi_inflation", "BOC": "cpi_inflation", "RBNZ": "cpi_inflation", "SNB": "cpi_inflation"}


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


async def _write_round(bank: str, rows: list[dict[str, Any]], source: dict | None = None) -> int:
    release = rows[0]["release_date"]
    async with session_scope() as session:
        await session.execute(text("DELETE FROM cb_projection_values WHERE bank = :b AND release_date = :d"),
                              {"b": bank, "d": release})
        await session.execute(text("""INSERT INTO cb_projection_values (bank, release_date, variable, horizon, stat, value)
            VALUES (:bank, :release_date, :variable, :horizon, :stat, :value)"""), rows)
        if source:
            await session.execute(text("""INSERT INTO cb_projection_round_sources
                (bank, release_date, source_date, source_url, pdf_url)
                VALUES (:bank, :release_date, :source_date, :source_url, :pdf_url)
                ON CONFLICT (bank, release_date) DO UPDATE SET source_date=EXCLUDED.source_date,
                source_url=EXCLUDED.source_url, pdf_url=EXCLUDED.pdf_url, validated_at=now()"""), source)
    return len(rows)


# ── Per-bank discovery ────────────────────────────────────────────────

BOJ_OUTLOOK = "https://www.boj.or.jp/en/mopo/outlook/index.htm"
BOJ_HIGHLIGHTS = "https://www.boj.or.jp/en/mopo/outlook/highlight/index.htm"


def _boj_date(raw: str) -> date | None:
    raw = re.sub(r"(?<=\d)\s+(?=\d)", "", raw.replace("\xa0", " "))
    match = re.search(r"\b([A-Za-z]{3,9})\.?\s*(\d{1,2})\s*,\s*(20\d\d)", raw)
    if not match:
        return None
    month = match.group(1).replace("Sept", "Sep")
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(f"{month} {match.group(2)}, {match.group(3)}", fmt).date()
        except ValueError:
            pass
    return None


def _boj_sources(outlook_html: str, highlights_html: str, since: date) -> dict[tuple[int, int], dict]:
    sources: dict[tuple[int, int], dict] = {}
    for a in BeautifulSoup(outlook_html, "html.parser").find_all("a", href=True):
        match = re.search(r"gor(\d{2})(01|04|07|10)a\.pdf$", a["href"])
        if match:
            year, month = 2000 + int(match.group(1)), int(match.group(2))
            if date(year, month, 1) >= date(since.year, since.month, 1):
                sources[(year, month)] = {"pdf_url": urljoin(BOJ_OUTLOOK, a["href"])}
    if since <= date(2020, 1, 31) and (2020, 1) not in sources:
        sources[(2020, 1)] = {"pdf_url": "https://www.boj.or.jp/en/mopo/outlook/gor2001a.pdf"}
    for a in BeautifulSoup(highlights_html, "html.parser").find_all("a", href=True):
        match = re.search(r"ten(20\d\d)(01|04|07|10)\.htm$", a["href"])
        if not match:
            continue
        key = (int(match.group(1)), int(match.group(2)))
        published = _boj_date(a.parent.parent.get_text(" ", strip=True))
        if key in sources and published:
            sources[key].update({"html_url": urljoin(BOJ_HIGHLIGHTS, a["href"]), "html_date": published})
    return sources


async def _boj_rounds(client: httpx.AsyncClient, since: date, summary: dict) -> list[tuple[list[dict], dict]]:
    index = await client.get(BOJ_OUTLOOK)
    highlights = await client.get(BOJ_HIGHLIGHTS)
    index.raise_for_status()
    highlights.raise_for_status()
    rounds = []
    for (year, month), links in sorted(_boj_sources(index.text, highlights.text, since).items()):
        key = f"{year}-{month:02d}"
        if date(year, month, 1) > date.today():
            continue
        pdf_rows, html_rows = [], []
        pdf_date = None
        response = await client.get(links["pdf_url"])
        if response.status_code == 200:
            try:
                pages = PdfReader(io.BytesIO(response.content)).pages
                pdf_date = _boj_date((pages[0].extract_text() or "")[:180])
                if pdf_date and pdf_date <= date.today():
                    pdf_rows = parsers.parse_boj_outlook_pdf(" ".join(page.extract_text() or "" for page in pages), pdf_date)
            except (ValueError, OSError) as exc:
                summary["skipped"][key] = f"PDF: {exc}"
        if links.get("html_url") and links["html_date"] <= date.today():
            response = await client.get(links["html_url"])
            if response.status_code == 200:
                try:
                    html_rows = parsers.parse_boj_highlights(response.text, links["html_date"])
                except ValueError as exc:
                    summary["skipped"][key] = f"HTML: {exc}"
        if not html_rows and not pdf_rows:
            summary["skipped"].setdefault(key, "No validated HTML or PDF forecast table")
            continue
        pdf_values = {(r["variable"], r["horizon"]): r["value"] for r in pdf_rows if r["stat"] == "median"}
        if any(pdf_values.get((r["variable"], r["horizon"]), r["value"]) != r["value"] for r in html_rows):
            summary["errors"][key] = "HTML median differs from Outlook PDF"
            continue
        release = links["html_date"] if html_rows else pdf_date
        if release is None:
            summary["skipped"][key] = "Source date missing"
            continue
        merged = {(r["variable"], r["horizon"], r["stat"]): {**r, "release_date": release} for r in pdf_rows}
        merged.update({(r["variable"], r["horizon"], r["stat"]): {**r, "release_date": release} for r in html_rows})
        variables = {r["variable"] for r in merged.values() if r["stat"] == "median"}
        if not {"real_gdp", "core_cpi_inflation"} <= variables:
            summary["errors"][key] = "Required GDP/core CPI medians missing"
            continue
        rounds.append((list(merged.values()), {"bank": "BOJ", "release_date": release,
                       "source_date": release, "source_url": links["html_url"] if html_rows else links["pdf_url"],
                       "pdf_url": links["pdf_url"] if pdf_rows else None}))
    return rounds


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


async def load_projections(banks: tuple[str, ...] = BANKS, since: date = date(2020, 1, 1)) -> dict[str, Any]:
    """Idempotent: each parsed round replaces that bank's rows for its release date."""
    result: dict[str, Any] = {}
    async with run_logger(JOB_NAME) as run, httpx.AsyncClient(timeout=60, follow_redirects=True,
                                                                headers=source_identity.headers()) as client:
        for bank in banks:
            summary: dict[str, Any] = {"rounds": 0, "rows": 0, "skipped": {}, "errors": {}}
            if is_manual(bank, "projections") and bank != "BOJ":
                result[bank] = await mark_manual_run(JOB_NAME, bank, "projections")
                continue
            try:
                if bank == "BOE":
                    rounds = await _boe_rounds(client, since, summary)
                elif bank == "BOJ":
                    rounds = await _boj_rounds(client, since, summary)
                elif bank == "BOC":
                    rounds = await _boc_rounds(client, since, summary)
                else:
                    rounds = await _snb_rounds(client, since, summary)
            except (httpx.HTTPError, ValueError) as exc:
                summary["errors"]["fetch"] = str(exc)
                run.errors.append(f"{bank}: {exc}")
                rounds = []
            for item in rounds:
                rows, source = item if bank == "BOJ" else (item, None)
                summary["rows"] += await _write_round(bank, rows, source)
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
        if stat not in ("point", "median") or (variable, horizon, stat) not in before:
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
        return {"bank": bank, "status": "unavailable",
                "reason": manual_sources(bank).get("projections", "Fewer than two rounds stored")}
    latest, previous = dates[-1], dates[-2]
    result = compute_revisions(bank, await round_values(bank, latest), await round_values(bank, previous), latest)
    return {"bank": bank, "status": "available", "round": latest, "previous_round": previous, **result}


async def get_projections(bank: str, round_: str = "latest") -> dict[str, Any]:
    dates = await rounds(bank)
    cfg = bank_config()[bank]
    meta = {"bank": bank, "publication": cfg["projections"]["name"], "conditioning": cfg["conditioning"],
            "rate_path_type": cfg["rate_path_type"],
            "round_date_method": "validated official source date" if bank == "BOJ" else "decision date in publication month"}
    if not dates:
        return {**meta, "status": "manual_import" if is_manual(bank, "projections") else "unavailable",
                "reason": manual_sources(bank).get("projections", "No projections stored")}
    wanted = dates if round_ == "all" else [dates[-1]] if round_ == "latest" else [date.fromisoformat(round_)]
    out = []
    for day in wanted:
        variables: dict[str, dict] = {}
        for (variable, horizon, stat), value in sorted((await round_values(bank, day)).items()):
            variables.setdefault(variable, {}).setdefault(horizon, {})[stat] = value
        source = None
        if bank == "BOJ":
            async with get_sessionmaker()() as session:
                source = (await session.execute(text("""SELECT source_date, source_url, pdf_url
                    FROM cb_projection_round_sources WHERE bank=:b AND release_date=:d"""),
                    {"b": bank, "d": day})).mappings().first()
        out.append({"release_date": day, "variables": variables,
                    **({"source_date": source["source_date"], "source_url": source["source_url"],
                        "pdf_url": source["pdf_url"]} if source else
                       {"source_date": day, "source_url": None, "pdf_url": None} if bank == "BOJ" else {})})
    return {**meta, "status": "available", "rounds": out}
