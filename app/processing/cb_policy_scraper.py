"""Scrapers for central bank monetary policy statements.

Each bank scraper fetches the last 12 months of official statements from
the bank's public website and returns a list of StatementRecord dicts:

    {"bank", "meeting_date", "source_url", "raw_text", "report_type"}

All scrapers are best-effort: if a page fails to fetch or parse, we skip
that statement rather than failing the whole run.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, timedelta
from html.parser import HTMLParser
from typing import Any, TypedDict

import httpx

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

_REQUEST_DELAY = 0.6  # seconds between requests per bank
_FETCH_TIMEOUT = 20.0  # seconds
_MAX_TEXT_CHARS = 8000  # truncate raw statement text before storage


class StatementRecord(TypedDict, total=False):
    bank: str            # required
    meeting_date: date   # required
    source_url: str      # required
    raw_text: str        # required
    report_type: str     # required
    source_pdf_url: str  # optional — direct PDF download link from CB website


# ── HTML text extraction ─────────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    """Strip HTML tags and return clean visible text."""

    _SKIP_TAGS = frozenset(
        ["script", "style", "nav", "footer", "header", "noscript", "iframe", "svg", "aside"]
    )
    _BLOCK_TAGS = frozenset(
        ["p", "h1", "h2", "h3", "h4", "h5", "li", "tr", "br", "div", "section", "article"]
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip: int = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        t = tag.lower()
        if t in self._SKIP_TAGS:
            self._skip += 1
        elif t in self._BLOCK_TAGS and self._parts:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self._SKIP_TAGS and self._skip:
            self._skip -= 1
        elif t in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n[ \t]+", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _extract_text(html: str) -> str:
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
    except Exception:
        pass
    return extractor.get_text()


def _extract_section(html: str, *patterns: str) -> str:
    """Try each regex pattern in order; fall back to full-page extraction."""
    for pattern in patterns:
        m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if m:
            return _extract_text(m.group(0))
    return _extract_text(html)


def _clean_text(text: str) -> str:
    """Remove cookie banners, navigation remnants and very short lines."""
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if len(stripped) < 4:
            continue
        low = stripped.lower()
        if any(
            kw in low
            for kw in (
                "cookie", "javascript", "subscribe", "sign up", "newsletter",
                "click here", "back to top", "privacy policy", "terms of use",
                "skip to", "share this", "print this",
            )
        ):
            continue
        cleaned.append(stripped)
    return "\n".join(cleaned)


# ── PDF link discovery ───────────────────────────────────────────────────────

# Terms that indicate a link is the main policy document PDF (not an appendix)
_PDF_PREFERRED_TERMS = (
    "statement", "decision", "assessment", "monetary", "mps", "minutes",
    "press", "policy", "rate", "mpmpro", "annex", "implementation",
)
_PDF_SKIP_TERMS = ("publication", "annual", "report", "speech", "wp", "working", "research")


def _find_pdf_link(html: str, base_url: str) -> str | None:
    """Return the best-guess PDF link from a CB page's HTML.

    Looks for <a href="...pdf"> elements, prefers links that mention monetary
    policy terms. Falls back to the first .pdf link if nothing preferred found.
    """
    # Parse base to build absolute URLs
    from urllib.parse import urljoin, urlparse
    hrefs = re.findall(r'href=["\']([^"\']+\.pdf[^"\']*)["\']', html, re.IGNORECASE)
    if not hrefs:
        return None

    preferred: list[str] = []
    fallback: list[str] = []

    for href in hrefs:
        # Resolve relative URLs
        abs_url = urljoin(base_url, href.split("?")[0])
        lower = href.lower()
        if any(t in lower for t in _PDF_SKIP_TERMS):
            continue
        if any(t in lower for t in _PDF_PREFERRED_TERMS):
            preferred.append(abs_url)
        else:
            fallback.append(abs_url)

    return (preferred or fallback or [None])[0]


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=_HEADERS,
        timeout=_FETCH_TIMEOUT,
        follow_redirects=True,
    )


async def _fetch(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text
    except httpx.HTTPStatusError as exc:
        logger.warning("HTTP %s for %s", exc.response.status_code, url)
        return None
    except httpx.HTTPError as exc:
        logger.warning("Fetch error for %s: %s", url, exc)
        return None


# ── Per-bank scrapers ────────────────────────────────────────────────────────

async def _scrape_fed(client: httpx.AsyncClient, since: date) -> list[StatementRecord]:
    """Federal Reserve FOMC statements."""
    base = "https://www.federalreserve.gov"
    calendar_html = await _fetch(client, f"{base}/monetarypolicy/fomccalendars.htm")
    if not calendar_html:
        return []

    # Statement links look like: /newsevents/pressreleases/monetary20260129a.htm
    found = re.findall(
        r"/newsevents/pressreleases/monetary(\d{4})(\d{2})(\d{2})a\.htm",
        calendar_html,
    )
    found = sorted(set(found), reverse=True)

    records: list[StatementRecord] = []
    for year_s, month_s, day_s in found:
        try:
            meeting_date = date(int(year_s), int(month_s), int(day_s))
        except ValueError:
            continue
        if meeting_date < since:
            break

        url = f"{base}/newsevents/pressreleases/monetary{year_s}{month_s}{day_s}a.htm"
        html = await _fetch(client, url)
        await asyncio.sleep(_REQUEST_DELAY)
        if not html:
            continue

        text = _extract_section(
            html,
            r'<div[^>]+id=["\']content["\'][^>]*>(.*?)</article',
            r'<div[^>]+class=["\'][^"\']*col-xs-12[^"\']*["\'][^>]*>(.*?)</div>\s*</div>',
        )
        text = _clean_text(text)
        if len(text) < 150:
            continue

        # FED publishes its statement as both .htm and .pdf at the same path
        pdf_url = url.replace(".htm", ".pdf")
        records.append(
            StatementRecord(
                bank="FED",
                meeting_date=meeting_date,
                source_url=url,
                source_pdf_url=pdf_url,
                raw_text=text[:_MAX_TEXT_CHARS],
                report_type="statement",
            )
        )

    return records


async def _scrape_ecb(client: httpx.AsyncClient, since: date) -> list[StatementRecord]:
    """European Central Bank monetary policy decisions."""
    records: list[StatementRecord] = []
    # ECB organises decisions by year
    years_to_check = sorted({since.year, date.today().year}, reverse=True)

    for year in years_to_check:
        index_url = (
            f"https://www.ecb.europa.eu/press/govcouncil/mopo/{year}.en.html"
        )
        index_html = await _fetch(client, index_url)
        await asyncio.sleep(_REQUEST_DELAY)
        if not index_html:
            continue

        # Links look like: /press/pr/date/2026/html/ecb.mp260130...en.html
        links = re.findall(
            r'href="(/press/pr/date/(\d{4})/html/[^"]+\.en\.html)"',
            index_html,
        )
        for href, link_year in sorted(set(links), key=lambda x: x[0], reverse=True):
            # Extract date from filename — ecb.mp{YY}{MM}{DD}
            m = re.search(r"mp(\d{2})(\d{2})(\d{2})", href)
            if not m:
                continue
            yy, mm, dd = m.groups()
            try:
                full_year = 2000 + int(yy)
                meeting_date = date(full_year, int(mm), int(dd))
            except ValueError:
                continue
            if meeting_date < since:
                continue

            url = f"https://www.ecb.europa.eu{href}"
            html = await _fetch(client, url)
            await asyncio.sleep(_REQUEST_DELAY)
            if not html:
                continue

            text = _extract_section(
                html,
                r'<section[^>]+class=["\'][^"\']*press-release[^"\']*["\'][^>]*>(.*?)</section>',
                r'<div[^>]+id=["\']main-wrapper["\'][^>]*>(.*?)</footer>',
            )
            text = _clean_text(text)
            if len(text) < 150:
                continue

            records.append(
                StatementRecord(
                    bank="ECB",
                    meeting_date=meeting_date,
                    source_url=url,
                    source_pdf_url=_find_pdf_link(html, url) or "",
                    raw_text=text[:_MAX_TEXT_CHARS],
                    report_type="statement",
                )
            )

    return records


async def _scrape_boe(client: httpx.AsyncClient, since: date) -> list[StatementRecord]:
    """Bank of England Monetary Policy Summary."""
    index_url = (
        "https://www.bankofengland.co.uk"
        "/monetary-policy-summary-and-minutes/monetary-policy-summary-and-minutes"
    )
    index_html = await _fetch(client, index_url)
    if not index_html:
        return []

    # Links look like: /monetary-policy-summary-and-minutes/2026/february-2026
    links = re.findall(
        r'href="(/monetary-policy-summary-and-minutes/(\d{4})/[^"]+)"',
        index_html,
    )
    records: list[StatementRecord] = []
    base = "https://www.bankofengland.co.uk"

    month_map = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }

    for href, year_s in sorted(set(links), key=lambda x: x[0], reverse=True):
        # Derive approximate date from URL slug
        slug = href.split("/")[-1].lower()
        matched_month = None
        for name, num in month_map.items():
            if name in slug:
                matched_month = num
                break
        if matched_month is None:
            continue
        try:
            meeting_date = date(int(year_s), matched_month, 1)
        except ValueError:
            continue
        if meeting_date < since:
            break

        url = f"{base}{href}"
        html = await _fetch(client, url)
        await asyncio.sleep(_REQUEST_DELAY)
        if not html:
            continue

        text = _extract_section(
            html,
            r'<div[^>]+class=["\'][^"\']*page-content[^"\']*["\'][^>]*>(.*?)</div>\s*</main>',
            r'<main[^>]*>(.*?)</main>',
        )
        text = _clean_text(text)
        if len(text) < 150:
            continue

        records.append(
            StatementRecord(
                bank="BOE",
                meeting_date=meeting_date,
                source_url=url,
                source_pdf_url=_find_pdf_link(html, url) or "",
                raw_text=text[:_MAX_TEXT_CHARS],
                report_type="statement",
            )
        )

    return records


async def _scrape_boj(client: httpx.AsyncClient, since: date) -> list[StatementRecord]:
    """Bank of Japan monetary policy decisions."""
    index_url = "https://www.boj.or.jp/en/mopo/mpmdeci/index.htm"
    index_html = await _fetch(client, index_url)
    if not index_html:
        return []

    # Links look like: /en/mopo/mpmdeci/mpr_2026/k260119a.htm
    links = re.findall(
        r'href="(/en/mopo/mpmdeci/mpr_(\d{4})/k(\d{6})[a-z]?\.htm)"',
        index_html,
    )
    records: list[StatementRecord] = []
    base = "https://www.boj.or.jp"

    for href, year_s, date_s in sorted(set(links), key=lambda x: x[2], reverse=True):
        # date_s is YYMMDD e.g. 260119
        try:
            yy, mm, dd = date_s[:2], date_s[2:4], date_s[4:6]
            meeting_date = date(2000 + int(yy), int(mm), int(dd))
        except (ValueError, IndexError):
            continue
        if meeting_date < since:
            break

        url = f"{base}{href}"
        html = await _fetch(client, url)
        await asyncio.sleep(_REQUEST_DELAY)
        if not html:
            continue

        text = _extract_section(
            html,
            r'<div[^>]+id=["\']main["\'][^>]*>(.*?)</div>\s*<div[^>]+id=["\']footer',
            r'<div[^>]+class=["\'][^"\']*main[^"\']*["\'][^>]*>(.*?)</footer>',
        )
        text = _clean_text(text)
        if len(text) < 100:
            continue

        records.append(
            StatementRecord(
                bank="BOJ",
                meeting_date=meeting_date,
                source_url=url,
                source_pdf_url=_find_pdf_link(html, url) or "",
                raw_text=text[:_MAX_TEXT_CHARS],
                report_type="statement",
            )
        )

    return records


async def _scrape_rba(client: httpx.AsyncClient, since: date) -> list[StatementRecord]:
    """Reserve Bank of Australia board decisions."""
    index_url = "https://www.rba.gov.au/monetary-policy/rba-board-decisions/"
    index_html = await _fetch(client, index_url)
    if not index_html:
        return []

    # Links: /monetary-policy/rba-board-decisions/2026/2026-02-18.html
    links = re.findall(
        r'href="(/monetary-policy/rba-board-decisions/(\d{4})/(\d{4}-\d{2}-\d{2})\.html)"',
        index_html,
    )
    records: list[StatementRecord] = []
    base = "https://www.rba.gov.au"

    for href, year_s, date_str in sorted(set(links), key=lambda x: x[2], reverse=True):
        try:
            meeting_date = date.fromisoformat(date_str)
        except ValueError:
            continue
        if meeting_date < since:
            break

        url = f"{base}{href}"
        html = await _fetch(client, url)
        await asyncio.sleep(_REQUEST_DELAY)
        if not html:
            continue

        text = _extract_section(
            html,
            r'<div[^>]+class=["\'][^"\']*article[^"\']*["\'][^>]*>(.*?)</div>\s*</section>',
            r'<main[^>]*>(.*?)</main>',
        )
        text = _clean_text(text)
        if len(text) < 100:
            continue

        records.append(
            StatementRecord(
                bank="RBA",
                meeting_date=meeting_date,
                source_url=url,
                source_pdf_url=_find_pdf_link(html, url) or "",
                raw_text=text[:_MAX_TEXT_CHARS],
                report_type="statement",
            )
        )

    return records


async def _scrape_boc(client: httpx.AsyncClient, since: date) -> list[StatementRecord]:
    """Bank of Canada interest rate announcements."""
    index_url = "https://www.bankofcanada.ca/press/press-releases/"
    index_html = await _fetch(client, index_url)
    if not index_html:
        return []

    # Links look like: /2026/01/fad-press-release-2026-01-29/
    # Filter for interest rate decisions
    links = re.findall(
        r'href="(https://www\.bankofcanada\.ca/(\d{4})/(\d{2})/[^"]*(?:fad|interest-rate)[^"]*)"',
        index_html,
    )
    # Also try alternate pattern
    if not links:
        links_raw = re.findall(
            r'href="(https://www\.bankofcanada\.ca/(\d{4})/(\d{2})/[^"]+/)"',
            index_html,
        )
        # Filter by title context
        links = [
            (href, y, m)
            for href, y, m in links_raw
            if "fad" in href or "rate" in href.lower()
        ]

    records: list[StatementRecord] = []
    for url, year_s, month_s in sorted(set(links), key=lambda x: (x[1], x[2]), reverse=True):
        try:
            meeting_date = date(int(year_s), int(month_s), 1)
        except ValueError:
            continue
        if meeting_date < since:
            break

        html = await _fetch(client, url)
        await asyncio.sleep(_REQUEST_DELAY)
        if not html:
            continue

        # Try to get actual date from page
        date_m = re.search(r"(\w+ \d{1,2},? \d{4})", html)
        if date_m:
            from datetime import datetime
            for fmt in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y"):
                try:
                    meeting_date = datetime.strptime(date_m.group(1), fmt).date()
                    break
                except ValueError:
                    pass

        text = _extract_section(
            html,
            r'<div[^>]+class=["\'][^"\']*page__body[^"\']*["\'][^>]*>(.*?)</footer>',
            r'<article[^>]*>(.*?)</article>',
        )
        text = _clean_text(text)
        if len(text) < 100:
            continue

        records.append(
            StatementRecord(
                bank="BOC",
                meeting_date=meeting_date,
                source_url=url,
                source_pdf_url=_find_pdf_link(html, url) or "",
                raw_text=text[:_MAX_TEXT_CHARS],
                report_type="statement",
            )
        )

    return records


async def _scrape_snb(client: httpx.AsyncClient, since: date) -> list[StatementRecord]:
    """Swiss National Bank quarterly monetary policy assessments."""
    # SNB meets quarterly; index page lists all assessments
    index_url = (
        "https://www.snb.ch/en/the-snb/mandates-goals/monetary-policy/"
        "monetary-policy-strategies-and-communication/interest-rate-decisions"
    )
    index_html = await _fetch(client, index_url)
    if not index_html:
        return []

    # Links look like: /en/publications/communication/press-releases/2026/pre_20260320_3
    links = re.findall(
        r'href="(/en/publications/communication/press-releases/(\d{4})/pre_(\d{8})[^"]*)"',
        index_html,
    )
    records: list[StatementRecord] = []
    base = "https://www.snb.ch"

    for href, year_s, date_s in sorted(set(links), key=lambda x: x[2], reverse=True):
        try:
            meeting_date = date(int(date_s[:4]), int(date_s[4:6]), int(date_s[6:8]))
        except (ValueError, IndexError):
            continue
        if meeting_date < since:
            break

        url = f"{base}{href}"
        html = await _fetch(client, url)
        await asyncio.sleep(_REQUEST_DELAY)
        if not html:
            continue

        text = _extract_section(
            html,
            r'<div[^>]+class=["\'][^"\']*content[^"\']*["\'][^>]*>(.*?)</footer>',
            r'<main[^>]*>(.*?)</main>',
        )
        text = _clean_text(text)
        if len(text) < 100:
            continue

        records.append(
            StatementRecord(
                bank="SNB",
                meeting_date=meeting_date,
                source_url=url,
                source_pdf_url=_find_pdf_link(html, url) or "",
                raw_text=text[:_MAX_TEXT_CHARS],
                report_type="statement",
            )
        )

    return records


async def _scrape_rbnz(client: httpx.AsyncClient, since: date) -> list[StatementRecord]:
    """Reserve Bank of New Zealand monetary policy reviews."""
    index_url = "https://www.rbnz.govt.nz/hub/publications/monetary-policy-review"
    index_html = await _fetch(client, index_url)
    if not index_html:
        return []

    # Links look like: /hub/publications/monetary-policy-review/2026/february-2026
    links = re.findall(
        r'href="(/hub/publications/monetary-policy-(?:review|statement)/(\d{4})/[^"]+)"',
        index_html,
    )
    records: list[StatementRecord] = []
    base = "https://www.rbnz.govt.nz"

    month_map = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }

    for href, year_s in sorted(set(links), key=lambda x: x[0], reverse=True):
        slug = href.split("/")[-1].lower()
        matched_month = next(
            (num for name, num in month_map.items() if name in slug), None
        )
        if matched_month is None:
            continue
        try:
            meeting_date = date(int(year_s), matched_month, 1)
        except ValueError:
            continue
        if meeting_date < since:
            break

        url = f"{base}{href}"
        html = await _fetch(client, url)
        await asyncio.sleep(_REQUEST_DELAY)
        if not html:
            continue

        text = _extract_section(
            html,
            r'<div[^>]+class=["\'][^"\']*field-items[^"\']*["\'][^>]*>(.*?)</div>\s*</article>',
            r'<article[^>]*>(.*?)</article>',
        )
        text = _clean_text(text)
        if len(text) < 100:
            continue

        records.append(
            StatementRecord(
                bank="RBNZ",
                meeting_date=meeting_date,
                source_url=url,
                source_pdf_url=_find_pdf_link(html, url) or "",
                raw_text=text[:_MAX_TEXT_CHARS],
                report_type="statement",
            )
        )

    return records


# ── Dispatch table ───────────────────────────────────────────────────────────

_SCRAPERS: dict[str, Any] = {
    "FED": _scrape_fed,
    "ECB": _scrape_ecb,
    "BOE": _scrape_boe,
    "BOJ": _scrape_boj,
    "RBA": _scrape_rba,
    "BOC": _scrape_boc,
    "SNB": _scrape_snb,
    "RBNZ": _scrape_rbnz,
}


async def scrape_all_banks(lookback_months: int = 12) -> list[StatementRecord]:
    """Scrape monetary policy statements from all 8 central banks.

    Returns a flat list of StatementRecord dicts sorted by meeting_date desc.
    Individual bank failures are swallowed and logged.
    """
    since = date.today() - timedelta(days=lookback_months * 31)
    results: list[StatementRecord] = []

    async with _make_client() as client:
        for bank, scraper_fn in _SCRAPERS.items():
            try:
                bank_records = await scraper_fn(client, since)
                results.extend(bank_records)
                logger.info(
                    "Scraped %d statements for %s", len(bank_records), bank
                )
            except Exception as exc:
                logger.error("Scraper failed for %s: %s", bank, exc, exc_info=True)

    results.sort(key=lambda r: r["meeting_date"], reverse=True)
    return results


async def scrape_bank(bank: str, lookback_months: int = 12) -> list[StatementRecord]:
    """Scrape one bank's statements (useful for targeted refresh)."""
    scraper_fn = _SCRAPERS.get(bank.upper())
    if scraper_fn is None:
        raise ValueError(f"Unknown bank: {bank!r}. Valid banks: {list(_SCRAPERS)}")
    since = date.today() - timedelta(days=lookback_months * 31)
    async with _make_client() as client:
        return await scraper_fn(client, since)
