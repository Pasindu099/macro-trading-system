"""Published monetary-policy votes; unsupported formats are skipped, never inferred."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timezone

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import text

from app.db.session import get_sessionmaker, session_scope
from app.services import source_identity
from app.services.cb_projection_loaders import decision_dates

MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december")
BOJ_DIRS = ("state", "mpr")


def _names(raw: str) -> list[str]:
    return [re.sub(r"^and\s+", "", name.strip()) for name in re.split(r",\s*|\s+and\s+", raw) if name.strip()]


def _change(raw: str) -> float | None:
    match = re.search(r"(?:increase|raise|rise|reduce|cut|lower).*?\b(?:by\s*)?(\d+(?:/\d+|\.\d+)?) percentage points?", raw, re.I)
    if not match:
        return 0.0 if re.search(r"\bno change\b", raw, re.I) else None
    sign = -1 if re.search(r"reduce|cut|lower", match.group(0), re.I) else 1
    value = match.group(1)
    number = float(value.split("/")[0]) / float(value.split("/")[1]) if "/" in value else float(value)
    return sign * number * 100


def parse_boe(html: str, meeting: date) -> tuple[datetime, list[dict]]:
    body = " ".join(BeautifulSoup(html, "html.parser").get_text(" ", strip=True).split())
    published = re.search(r"Published on\s+(\d{1,2}\s+\w+\s+20\d\d)", body, re.I)
    if not published:
        raise ValueError("BoE publication date missing")
    stamp = datetime.combine(datetime.strptime(published.group(1), "%d %B %Y").date(), time.min, timezone.utc)
    section = body[body.rfind("The Chair invited the Committee to vote on the proposition"):]
    matches = list(re.finditer(r"(?:\w+|\d+) members? \(([^)]+)\)\s+voted (in favour of|against) (?:the first|the|this) proposition", section, re.I))
    if not matches:
        raise ValueError("BoE named policy vote missing")
    rows = []
    for match in matches[:2]:
        vote = "for" if "favour" in match.group(2).lower() else "against"
        following = section[match.end():match.end() + 200]
        bp = _change(following) if vote == "against" else None
        for member in _names(match.group(1)):
            rows.append({"member": member, "vote": vote, "preferred_change_bp": bp})
    if len(rows) < 7 or len(rows) > 9 or not any(r["vote"] == "for" for r in rows):
        raise ValueError("BoE vote count invalid")
    return stamp, rows


def parse_boj(html: str, meeting: date) -> tuple[datetime, list[dict]]:
    body = " ".join(BeautifulSoup(html, "html.parser").get_text(" ", strip=True).split())
    match = re.search(r"Voting for the action:\s*(.*?)\.\s*Voting against the action:\s*(.*?)\.", body, re.I)
    if not match:
        raise ValueError("BoJ named policy vote missing")
    yes = _names(match.group(1))
    no = [] if match.group(2).lower() == "none" else _names(match.group(2))
    if len(yes) + len(no) != 9:
        raise ValueError("BoJ vote count invalid")
    preferred = None
    if no:
        proposal = re.search(r"(?:proposed|preferred).*?(?:remain at|to) around (\d+(?:\.\d+)?) percent", body[match.end():match.end() + 800], re.I)
        chosen = re.search(r"overnight call rate to remain at around (\d+(?:\.\d+)?) percent", body[:match.start()], re.I)
        if proposal and chosen:
            preferred = (float(proposal.group(1)) - float(chosen.group(1))) * 100
    rows = [{"member": name, "vote": vote, "preferred_change_bp": preferred if vote == "against" else None}
            for vote, names in (("for", yes), ("against", no)) for name in names]
    return datetime.combine(meeting, time.min, timezone.utc), rows


def parse_fed(html: str, meeting: date) -> tuple[datetime, list[dict]]:
    body = " ".join(BeautifulSoup(html, "html.parser").get_text(" ", strip=True).split())
    match = re.search(r"Voting for the monetary policy action (?:were|was) (.*?)\.\s*Voting against (?:this|the) action (?:were|was) (.*?)\.\s*(?:For media|Implementation Note|Last Update)", body, re.I)
    if not match:
        match = re.search(r"Voting for the monetary policy action (?:were|was) (.*?)\.\s*(?:For media|Implementation Note|Last Update)", body, re.I)
    if not match:
        raise ValueError("Fed named policy vote missing")
    yes = [re.sub(r",\s*(?:Chair|Vice Chair)$", "", name.strip()) for name in re.split(r";\s*(?:and\s+)?|,\s+and\s+", match.group(1))]
    if len(yes) == 1:
        yes = _names(match.group(1))
    no = []
    dissent_rows = []
    if match.lastindex == 2:
        for clause in re.split(r";\s*(?:and\s+)?", match.group(2)):
            name_part, _, preference = clause.partition(", who preferred")
            for name in re.split(r"\s+and\s+", name_part):
                if name.strip():
                    no.append(name.strip())
                    dissent_rows.append({"member": name.strip(), "vote": "against", "preferred_change_bp": _change(preference)})
    if len(yes) + len(no) < 7 or len(yes) + len(no) > 12:
        raise ValueError("Fed vote count invalid")
    rows = [{"member": name, "vote": "for", "preferred_change_bp": None} for name in yes] + dissent_rows
    return datetime.combine(meeting, time.min, timezone.utc), rows


def source_urls(bank: str, meeting: date) -> list[str]:
    if bank == "BOE":
        return [f"https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/{meeting.year}/{MONTHS[meeting.month - 1]}-{meeting.year}"]
    if bank == "BOJ":
        return [f"https://www.boj.or.jp/en/mopo/mpmdeci/{directory}_{meeting.year}/k{meeting:%y%m%d}a.htm" for directory in BOJ_DIRS]
    return [f"https://www.federalreserve.gov/newsevents/pressreleases/monetary{meeting:%Y%m%d}a.htm"]


async def load_votes(since: date = date(2020, 1, 1), banks: tuple[str, ...] = ("BOE", "BOJ", "FED")) -> dict[str, dict]:
    result = {}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=source_identity.headers()) as client:
        for bank, parser in (("BOE", parse_boe), ("BOJ", parse_boj), ("FED", parse_fed)):
            if bank not in banks:
                continue
            summary = {"meetings": 0, "votes": 0, "skipped": 0}
            blocked = False
            for meeting in await decision_dates(bank):
                if meeting < since or meeting > date.today():
                    continue
                if blocked:
                    summary["skipped"] += 1
                    continue
                for url in source_urls(bank, meeting):
                    try:
                        response = await client.get(url)
                        if response.status_code == 403:
                            blocked = True
                            break
                        if response.status_code != 200:
                            continue
                        stamp, rows = parser(response.text, meeting)
                        if stamp.date() > date.today():
                            continue
                        async with session_scope() as session:
                            await session.execute(text("DELETE FROM cb_votes WHERE bank=:b AND meeting_date=:d"), {"b": bank, "d": meeting})
                            await session.execute(text("""INSERT INTO cb_votes
                                (bank, meeting_date, member, vote, preferred_change_bp, source_url, published_at)
                                VALUES (:bank, :meeting_date, :member, :vote, :preferred_change_bp, :source_url, :published_at)"""),
                                [{**row, "bank": bank, "meeting_date": meeting, "source_url": url, "published_at": stamp} for row in rows])
                        summary["meetings"] += 1
                        summary["votes"] += len(rows)
                        break
                    except (httpx.HTTPError, ValueError):
                        continue
                else:
                    summary["skipped"] += 1
                if blocked:
                    summary["skipped"] += 1
            if blocked:
                summary["blocked"] = "Official source returned HTTP 403"
            result[bank] = summary
    return result


async def committee_balance(bank: str, as_of: datetime | None = None) -> dict:
    as_of = as_of or datetime.now(timezone.utc)
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text("""SELECT meeting_date, member, vote, preferred_change_bp, source_url, published_at
            FROM cb_votes WHERE bank=:b AND published_at<=:as_of ORDER BY meeting_date DESC, member"""),
            {"b": bank, "as_of": as_of})).mappings().all()
    meetings = []
    for row in rows:
        if not meetings or meetings[-1]["date"] != row["meeting_date"]:
            meetings.append({"date": row["meeting_date"], "for": 0, "against": 0, "dissent_bp": set(), "source_url": row["source_url"], "members": []})
        meeting = meetings[-1]
        meeting[row["vote"]] += 1
        meeting["members"].append({"name": row["member"], "vote": row["vote"], "preferred_change_bp": float(row["preferred_change_bp"]) if row["preferred_change_bp"] is not None else None})
        if row["vote"] == "against" and row["preferred_change_bp"] is not None:
            meeting["dissent_bp"].add(float(row["preferred_change_bp"]))
    for meeting in meetings:
        meeting["dissent_bp"] = sorted(meeting["dissent_bp"])
    return {"status": "available" if meetings else "pending", "latest": meetings[0] if meetings else None,
            "previous": meetings[1] if len(meetings) > 1 else None}
