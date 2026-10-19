"""Parse investingLive data prints and stage auditable calendar actuals."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text

from app.db.models import IndicatorRelease
from app.db.session import session_scope
from app.services.event_innovation_jobs import run_incremental_event_innovation

NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?")
VS = re.compile(r"\bvs\.?\s*([-+]?\d+(?:\.\d+)?)\s*%?\s*(?:exp(?:ected)?|est(?:imate)?|forecast)", re.I)
COUNTRIES = ((r"\b(?:US|U\.S\.|United States)\b", "US"), (r"\b(?:UK|U\.K\.|Britain)\b", "UK"),
             (r"\b(?:Germany|German)\b", "DE"), (r"\b(?:Eurozone|Euro area)\b", "EZ"),
             (r"\bJapan\b", "JP"), (r"\bAustralia\b", "AU"), (r"\bCanada\b", "CA"),
             (r"\bNew Zealand\b", "NZ"), (r"\bSwitzerland\b", "CH"))
INDICATORS = ((r"\b(?:core CPI|core inflation)\b", "core_cpi"),
              (r"\b(?:CPI|inflation rate)\b", "cpi_headline"),
              (r"\b(?:nonfarm payrolls|non-farm payrolls|NFP)\b", "nonfarm_payrolls"),
              (r"\bunemployment rate\b", "unemployment_rate"),
              (r"\bretail sales\b", "retail_sales"),
              (r"\bGDP\b", "gdp"))


@dataclass(frozen=True)
class DataPrint:
    country: str
    canonical_prefix: str
    actual: Decimal
    consensus: Decimal
    comparison: str | None


def parse_headline(title: str) -> DataPrint | None:
    """Only parse explicit actual-vs-expected headlines; ambiguity stays unfilled."""
    match = VS.search(title)
    if not match:
        return None
    preceding = NUMBER.findall(title[:match.start()])
    if not preceding:
        return None
    country = next((code for pattern, code in COUNTRIES if re.search(pattern, title, re.I)), None)
    indicator = next((prefix for pattern, prefix in INDICATORS if re.search(pattern, title, re.I)), None)
    if not country or not indicator:
        return None
    comparison = "mom" if re.search(r"\b(?:m/m|mom|monthly)\b", title, re.I) else (
        "yoy" if re.search(r"\b(?:y/y|yoy|annual)\b", title, re.I) else None)
    return DataPrint(country, indicator, Decimal(preceding[-1]), Decimal(match.group(1)), comparison)


def choose_candidate(candidates: list[dict], parsed: DataPrint) -> int | None:
    """Resolve co-released variants by forecast, then collapse exact duplicate versions."""
    matching_forecast = [row for row in candidates if row["estimate"] == parsed.consensus]
    if matching_forecast:
        candidates = matching_forecast
    identities = {(row["canonical_name"], row["period"], row["released_at"]) for row in candidates}
    return max(row["id"] for row in candidates) if len(identities) == 1 else None


async def process_pending(*, limit: int = 200) -> dict[str, int]:
    counts = {"unparsed": 0, "unmatched": 0, "newswire": 0, "confirmed": 0, "discrepancy": 0}
    wrote_release = False
    async with session_scope() as session:
        rows = (await session.execute(text("""
            SELECT n.id, n.title, n.published_at FROM news_items n
            LEFT JOIN newswire_actuals a ON a.news_item_id = n.id
            WHERE n.source = 'investinglive' AND n.news_type = 'Data' AND a.news_item_id IS NULL
            ORDER BY n.published_at, n.id LIMIT :limit
        """), {"limit": limit})).mappings().all()
        for item in rows:
            parsed = parse_headline(item["title"])
            status, reason, release, newswire_id, eodhd_actual = "unparsed", None, None, None, None
            if parsed:
                candidates = (await session.execute(text("""
                    SELECT r.id, i.canonical_name, r.period, r.released_at, r.estimate FROM indicator_releases r
                    JOIN indicators i ON i.id = r.indicator_id
                    WHERE i.country_code = :country AND r.is_latest
                      AND r.released_at BETWEEN :start AND :end
                      AND i.canonical_name LIKE :prefix
                      AND (cast(:comparison AS text) IS NULL OR i.comparison = cast(:comparison AS text)
                           OR i.canonical_name LIKE :comp_suffix)
                    ORDER BY abs(extract(epoch FROM r.released_at - :published_at)), r.id
                """), {"country": parsed.country, "start": item["published_at"] - timedelta(minutes=30),
                        "end": item["published_at"] + timedelta(minutes=30),
                        "published_at": item["published_at"], "prefix": parsed.canonical_prefix + "%",
                        "comparison": parsed.comparison,
                        "comp_suffix": "%_" + (parsed.comparison or "") if parsed.comparison else ""})).mappings().all()
                candidate_id = choose_candidate([dict(row) for row in candidates], parsed) if candidates else None
                if candidate_id is None:
                    status, reason = "unmatched", "no_unique_calendar_match"
                else:
                    release = await session.get(IndicatorRelease, candidate_id)
                    if release.actual is not None:
                        eodhd_actual = release.actual
                        status = "confirmed" if release.actual == parsed.actual else "discrepancy"
                    else:
                        release.is_latest = False
                        newswire = IndicatorRelease(
                            indicator_id=release.indicator_id, period=release.period,
                            period_start_date=release.period_start_date, released_at=release.released_at,
                            actual=parsed.actual, estimate=(release.estimate if release.estimate is not None
                                                            else parsed.consensus),
                            previous=release.previous, change=release.change,
                            change_percentage=release.change_percentage,
                            retrieved_at=datetime.now(UTC), is_latest=True,
                            raw_payload={**(release.raw_payload or {}), "source_status": "newswire",
                                         "news_item_id": item["id"]},
                        )
                        session.add(newswire)
                        await session.flush()
                        newswire_id = newswire.id
                        status = "newswire"
                        wrote_release = True
            await session.execute(text("""
                INSERT INTO newswire_actuals (news_item_id, scheduled_release_id, newswire_release_id,
                                              actual, consensus, eodhd_actual, status, reason)
                VALUES (:news_item_id, :scheduled_release_id, :newswire_release_id,
                        :actual, :consensus, :eodhd_actual, :status, :reason)
            """), {"news_item_id": item["id"], "scheduled_release_id": release.id if release else None,
                    "newswire_release_id": newswire_id, "actual": parsed.actual if parsed else None,
                    "consensus": parsed.consensus if parsed else None, "eodhd_actual": eodhd_actual,
                    "status": status, "reason": reason})
            counts[status] += 1
    if wrote_release:
        await run_incremental_event_innovation()
    return counts
