"""Speech pipeline (Step 12 Part E; no LLM).

Official sources only, identified User-Agent, ~1 s between requests:
FED Board yearly index + Dallas Fed RSS, ECB speeches dataset (CSV), BoE RSS,
BoJ yearly index, BoC RSS, SNB RSS + speeches page. RBA/RBNZ are manual-import
sources: their policymakers are covered by attributed investingLive headlines.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
import sys
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import text

from app.db.session import get_sessionmaker, session_scope
from app.ingestion.run_logger import run_logger
from app.processing.cb_officials import extract_points, match_officials
from app.processing.cb_statements import clean_text, pdf_text
from app.services import source_identity

logger = logging.getLogger(__name__)

JOB_NAME = "job:cb_speeches"
COMMENTS_JOB = "job:cb_speaker_comments"
BANKS = ("FED", "ECB", "BOE", "BOJ", "RBA", "BOC", "RBNZ", "SNB")
BACKFILL_DAYS = 366
DELAY = 1.0
MAX_TEXT = 200_000
MIN_TEXT = 400
ECB_DATASET = "https://www.ecb.europa.eu/press/key/shared/data/all_ECB_speeches.csv"
RSS = {"FED_DALLAS": "https://www.dallasfed.org/rss/speeches.xml",
       "BOE": "https://www.bankofengland.co.uk/rss/speeches",
       "BOC": "https://www.bankofcanada.ca/content_type/speeches/feed/",
       "SNB": "https://www.snb.ch/public/rss/en/news"}
FIELD_LIMIT = 10_000_000


# ── Discovery: dicts {date, title, url, kind, text?, speaker_text, source} ──

def parse_rss(payload: str, source: str, since: date) -> list[dict[str, Any]]:
    """RSS 2.0 and RSS 1.0 (RDF, dc:date) items, namespace-agnostic."""
    root = ElementTree.fromstring(payload.encode("utf-8") if isinstance(payload, str) else payload)

    def child(node, name: str) -> str:
        found = next((c for c in node if c.tag.rsplit("}", 1)[-1] == name), None)
        return (found.text or "").strip() if found is not None else ""

    out = []
    for item in (n for n in root.iter() if n.tag.rsplit("}", 1)[-1] == "item"):
        title, link = child(item, "title"), child(item, "link")
        try:
            raw = child(item, "pubDate")
            day = parsedate_to_datetime(raw).date() if raw else date.fromisoformat(child(item, "date")[:10])
        except (TypeError, ValueError):
            continue
        if day < since or not link:
            continue
        description = BeautifulSoup(child(item, "description"), "html.parser").get_text(" ", strip=True)
        out.append({"date": day, "title": title, "url": link, "kind": "html", "source": source,
                    "speaker_text": f"{title} {description}"})
    return out


def parse_ecb_dataset(payload: str, since: date) -> list[dict[str, Any]]:
    csv.field_size_limit(min(sys.maxsize, FIELD_LIMIT))
    out = []
    for row in csv.DictReader(io.StringIO(payload.lstrip("﻿")), delimiter="|"):
        try:
            day = date.fromisoformat((row.get("date") or "")[:10])
        except ValueError:
            continue
        if day < since:
            continue
        title = (row.get("title") or "").strip()
        out.append({"date": day, "title": title, "kind": "inline", "source": "ECB speeches dataset",
                    "url": f"ecb-dataset:{day}:{(row.get('speakers') or '').strip()}:{title[:120]}",
                    "text": clean_text((row.get("contents") or "").replace("  ", "\n")),
                    "speaker_text": f"{row.get('speakers') or ''} {row.get('subtitle') or ''}"})
    return out


def parse_fed_index(html: str, since: date) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for link in soup.find_all("a", href=re.compile(r"^/newsevents/speech/[a-z-]+(\d{8})a\.htm$")):
        stamp = re.search(r"(\d{8})a\.htm$", link["href"]).group(1)
        day = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
        if day < since:
            continue
        block = link.find_parent(["div", "li"]) or link
        out.append({"date": day, "title": link.get_text(" ", strip=True), "kind": "html", "source": "Fed Board",
                    "url": "https://www.federalreserve.gov" + link["href"],
                    "speaker_text": block.get_text(" ", strip=True)})
    return out


def parse_boj_index(html: str, since: date) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for link in soup.find_all("a", href=re.compile(r"/en/about/press/koen_\d{4}/ko(\d{6})[a-z]\.htm$")):
        stamp = re.search(r"ko(\d{6})[a-z]\.htm$", link["href"]).group(1)
        day = date(2000 + int(stamp[:2]), int(stamp[2:4]), int(stamp[4:]))
        if day < since:
            continue
        row = link.find_parent("tr") or link
        out.append({"date": day, "title": link.get_text(" ", strip=True), "kind": "html", "source": "BoJ speeches",
                    "url": "https://www.boj.or.jp" + link["href"], "speaker_text": row.get_text(" ", strip=True)})
    return out


def parse_snb_listing(html: str, since: date) -> list[dict[str, Any]]:
    out = []
    for href, stamp in set(re.findall(r'href="(/en/publications/communication/speeches/\d{4}/ref_(\d{8})_[a-z]+)"', html)):
        day = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
        if day >= since:
            out.append({"date": day, "title": "", "kind": "html", "source": "SNB speeches",
                        "url": "https://www.snb.ch" + href, "speaker_text": ""})
    return out


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    await asyncio.sleep(DELAY)
    response = await client.get(url)
    response.raise_for_status()
    return response


async def discover(client: httpx.AsyncClient, bank: str, since: date) -> list[dict[str, Any]]:
    if bank == "FED":
        items = []
        for year in range(since.year, date.today().year + 1):
            items += parse_fed_index((await _get(client, f"https://www.federalreserve.gov/newsevents/speech/"
                                                         f"{year}-speeches.htm")).text, since)
        items += [{**i, "speaker_text": f"Dallas Fed {i['speaker_text']}"}
                  for i in parse_rss((await _get(client, RSS["FED_DALLAS"])).text, "Dallas Fed", since)]
        return items
    if bank == "ECB":
        return parse_ecb_dataset((await _get(client, ECB_DATASET)).text, since)
    if bank in ("BOE", "BOC"):
        return parse_rss((await _get(client, RSS[bank])).text, f"{bank} RSS", since)
    if bank == "BOJ":
        items = []
        for year in range(since.year, date.today().year + 1):
            items += parse_boj_index((await _get(client, f"https://www.boj.or.jp/en/announcements/press/"
                                                         f"koen_{year}/index.htm")).text, since)
        return items
    if bank == "SNB":
        items = [i for i in parse_rss((await _get(client, RSS["SNB"])).text, "SNB RSS", since)
                 if "/speeches/" in i["url"]]
        listing = parse_snb_listing((await _get(client, "https://www.snb.ch/en/news-publications/speeches")).text, since)
        known = {i["url"] for i in items}
        return items + [i for i in listing if i["url"] not in known]
    return []


# ── Text and attribution ──────────────────────────────────────────────

def page_text(html: str) -> tuple[str, str | None, str | None]:
    """(text, title, pdf link) from a speech page."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "form", "noscript", "aside"]):
        tag.decompose()
    title = soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else None
    node = soup.select_one("#article, article, main, #contents") or soup.body or soup
    pdf = next((a["href"] for a in soup.find_all("a", href=True) if a["href"].lower().endswith(".pdf")), None)
    return clean_text(node.get_text("\n")), title, pdf


def attribute(bank: str, item: dict[str, Any], body: str) -> str | None:
    """Speaker from the listing text first, then the title and opening of the speech."""
    for blob in (item.get("speaker_text") or "", f"{item.get('title') or ''}\n{body[:800]}"):
        matches = match_officials(blob, bank=bank, on=item["date"])
        if matches:
            return matches[0][1]
    return None


async def fetch_text(client: httpx.AsyncClient, item: dict[str, Any]) -> tuple[str, str]:
    if item["kind"] == "inline":
        return item["text"], item["title"]
    response = await _get(client, item["url"])
    if response.headers.get("content-type", "").startswith("application/pdf"):
        return pdf_text(response.content), item["title"]
    body, title, pdf = page_text(response.text)
    if len(body) < MIN_TEXT and pdf:
        pdf_response = await _get(client, str(httpx.URL(item["url"]).join(pdf)))
        body = pdf_text(pdf_response.content)
    return body, item["title"] or title or ""


# ── Store ─────────────────────────────────────────────────────────────

async def _known_urls(bank: str) -> set[str]:
    async with get_sessionmaker()() as session:
        return set((await session.execute(text("SELECT url FROM cb_speeches WHERE bank = :b"), {"b": bank})).scalars())


async def store_sentences(session, source_type: str, source_id: int, bank: str, speaker: str | None, day: date,
                          body: str) -> int:
    points = extract_points(body)
    await session.execute(text("DELETE FROM cb_speech_sentences WHERE source_type = :t AND source_id = :i"),
                          {"t": source_type, "i": source_id})
    if points:
        await session.execute(text("""INSERT INTO cb_speech_sentences
            (source_type, source_id, bank, speaker_id, sentence_date, position, sentence, theme)
            VALUES (:t, :i, :b, :s, :d, :p, :x, :h)"""),
                              [{"t": source_type, "i": source_id, "b": bank, "s": speaker, "d": day, "p": pos,
                                "x": sentence, "h": th} for pos, sentence, th in points])
    return len(points)


async def store_speech(bank: str, item: dict[str, Any], body: str, title: str, speaker: str | None) -> int:
    async with session_scope() as session:
        speech_id = (await session.execute(text("""INSERT INTO cb_speeches
            (bank, speaker_id, speech_date, title, url, text, source)
            VALUES (:b, :s, :d, :t, :u, :x, :src) ON CONFLICT (url) DO NOTHING RETURNING id"""),
            {"b": bank, "s": speaker, "d": item["date"], "t": title[:500] or "(untitled)", "u": item["url"],
             "x": body[:MAX_TEXT], "src": item["source"]})).scalar_one_or_none()
        if speech_id is None:
            return 0
        return await store_sentences(session, "speech", speech_id, bank, speaker, item["date"], body)


async def ingest_speeches(banks: tuple[str, ...] = BANKS, since: date | None = None) -> dict[str, Any]:
    """Idempotent (dedupe on URL); blocked banks log manual_import."""
    from app.services.cb_manual_imports import is_manual, mark_manual_run
    since = since or date.today() - timedelta(days=BACKFILL_DAYS)
    result: dict[str, Any] = {}
    async with run_logger(JOB_NAME) as run, httpx.AsyncClient(timeout=60, follow_redirects=True,
                                                                headers=source_identity.headers()) as client:
        for bank in banks:
            if is_manual(bank, "speeches"):
                result[bank] = await mark_manual_run(JOB_NAME, bank, "speeches")
                continue
            summary: dict[str, Any] = {"listed": 0, "new": 0, "unattributed": 0, "sentences": 0, "failed": 0}
            try:
                items = await discover(client, bank, since)
            except (httpx.HTTPError, ElementTree.ParseError, ValueError) as exc:
                run.errors.append(f"{bank} discovery: {exc}")
                result[bank] = {**summary, "error": str(exc)[:200]}
                continue
            known = await _known_urls(bank)
            for item in items:
                summary["listed"] += 1
                if item["url"] in known:
                    continue
                try:
                    body, title = await fetch_text(client, item)
                except (httpx.HTTPError, ValueError) as exc:
                    summary["failed"] += 1
                    logger.warning("%s speech %s failed: %s", bank, item["url"], exc)
                    continue
                if len(body) < MIN_TEXT:
                    summary["failed"] += 1
                    continue
                speaker = attribute(bank, item, body)
                summary["unattributed"] += speaker is None
                summary["sentences"] += await store_speech(bank, item, body, title, speaker)
                summary["new"] += 1
                known.add(item["url"])
            run.record_rows(summary["new"])
            result[bank] = summary
    return result


async def rebuild_sentences() -> int:
    """Re-run point extraction on every stored speech and comment (after a dictionary change)."""
    total = 0
    async with session_scope() as session:
        speeches = (await session.execute(text("SELECT id, bank, speaker_id, speech_date, text FROM cb_speeches"))).all()
        for row in speeches:
            total += await store_sentences(session, "speech", row.id, row.bank, row.speaker_id, row.speech_date,
                                           row.text or "")
        comments = (await session.execute(text(
            "SELECT id, bank, speaker_id, published_at, headline FROM cb_speaker_comments"))).all()
        for row in comments:
            total += await store_sentences(session, "comment", row.id, row.bank, row.speaker_id,
                                           row.published_at.date(), re.sub(r"^[^:]{0,60}:\s*", "", row.headline))
    return total


# ── investingLive short comments ──────────────────────────────────────

def attribute_headline(headline: str, on: date) -> list[tuple[str, str]]:
    """(bank, speaker_id) for each official named in a headline."""
    return [(bank, speaker) for bank, speaker, _ in match_officials(headline, on=on)]


async def attribute_comments(since: datetime | None = None) -> dict[str, int]:
    since = since or datetime.now().astimezone() - timedelta(days=BACKFILL_DAYS)
    counts: dict[str, int] = {}
    async with run_logger(COMMENTS_JOB) as run:
        async with get_sessionmaker()() as session:
            rows = (await session.execute(text("""SELECT id, title, published_at FROM raw_news
                WHERE source = 'investinglive' AND published_at >= :since
                  AND id NOT IN (SELECT news_id FROM cb_speaker_comments)"""), {"since": since})).all()
        async with session_scope() as session:
            for row in rows:
                for bank, speaker in attribute_headline(row.title, row.published_at.date()):
                    comment_id = (await session.execute(text("""INSERT INTO cb_speaker_comments
                        (news_id, bank, speaker_id, published_at, headline) VALUES (:n, :b, :s, :p, :h)
                        ON CONFLICT (news_id, speaker_id) DO NOTHING RETURNING id"""),
                        {"n": row.id, "b": bank, "s": speaker, "p": row.published_at, "h": row.title})).scalar_one_or_none()
                    if comment_id is None:
                        continue
                    counts[bank] = counts.get(bank, 0) + 1
                    await store_sentences(session, "comment", comment_id, bank, speaker, row.published_at.date(),
                                          re.sub(r"^[^:]{0,60}:\s*", "", row.title))
        run.record_rows(sum(counts.values()))
    return counts
