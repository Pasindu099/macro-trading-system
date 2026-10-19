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

NUM = r"[-+]?\d+(?:\.\d+)?"
UNIT = r"\s*(?:%|K|k|M|bn|B|million|billion)?"
NUMBER = re.compile(r"(?<![\w.$])" + NUM + r"(?=" + UNIT + r"(?![\w]))")
COMPARE = re.compile(  # "+0.4% vs +0.3% expected", "54.9 versus 55.2 estimate", "+3.8% vs +3.8% y/y prelim"
    r"\b(?:vs\.?|versus)\s*(" + NUM + r")" + UNIT + r"\s*(?:[ymq]/[ymq]\s*)?"
    r"(exp(?:ected)?|est(?:imate)?|forecast|consensus|prelim(?:inary)?|prior|prev(?:ious)?)\b", re.I)
PAREN = re.compile(  # "4.7% (expected 4.6%, prior 4.7%)", "4% (vs. expected 4%, prior 3.5%)"
    r"(" + NUM + r")" + UNIT + r"\s*(?:[ymq]/[ymq]\s*)?\((?:vs\.?\s*)?(?:exp(?:ected)?|est(?:imate)?)\.?:?\s*(" + NUM + r")",
    re.I)
NARRATIVE = re.compile(  # first clause only: "Tokyo core CPI jumps to 2.7%"
    r"\b(?:confirmed at|(?:rises|rose|falls|fell|eases|eased|slips|slipped|slides|slid|jumps|jumped|climbs|climbed|"
    r"slows|slowed|edges|edged|returns to growth|improves|improved|drops|dropped)\s+(?:to|at)|comes in at|"
    r"(?:rises|rose|rise|hits|hit)(?:\s+to)?)\s+(" + NUM + r")"
    + UNIT + r"(?![\w])", re.I)
NOT_RELEASE = re.compile(r"\b(?:PBOC|People.s Bank of China|reference rate|mid-?point|central rate|news wrap|"
                         r"calendar|GDPNow|Dallas Fed Trimmed|seen at|preview|expects?)\b", re.I)
COUNTRIES = ((r"\b(?:US|U\.S\.|United States)\b", "US"), (r"\b(?:UK|U\.K\.|Britain|British)\b", "UK"),
             (r"\b(?:Germany|German)\b", "DE"), (r"\b(?:Eurozone|Euro area|Euro zone)\b", "EU"),
             (r"\b(?:Japan|Japanese|Tokyo)\b", "JP"), (r"\b(?:Australia|Australian)\b", "AU"),
             (r"\b(?:Canada|Canadian|Bank of Canada)\b", "CA"), (r"\b(?:New Zealand|NZ)\b", "NZ"),
             (r"\b(?:Switzerland|Swiss)\b", "CH"), (r"\b(?:China|Chinese)\b", "CN"),
             (r"\b(?:France|French)\b", "FR"), (r"\b(?:Spain|Spanish)\b", "ES"), (r"\b(?:Italy|Italian)\b", "IT"))
# Indicators only published for the US: the headline may omit the country.
US_ONLY = re.compile(r"\b(?:ISM|JOLTS|UMich|Michigan|NAHB|Case-?Shiller|jobless claims|non-?farm payrolls|"
                     r"Dallas Fed|Philly Fed|Empire State|Conference Board)\b", re.I)
# Most specific first. Values are exact calendar base names; comparison suffixes are added in matching.
INDICATORS = (
    (r"\bTokyo core CPI\b", ("tokyo_core_cpi",)),
    (r"\bcore PCE\b", ("core_pce_price_index",)),
    (r"\b(?:PCE)\b", ("pce_price_index",)),
    (r"\b(?:core CPI|core inflation)\b", ("core_cpi",)),
    (r"\b(?:CPI|inflation rate|consumer prices)\b", ("cpi_headline", "consumer_price_index", "monthly_cpi_indicator",
                                                     "cpi_monthly")),
    (r"\b(?:non-?farm payrolls|NFP)\b", ("nfp",)),
    (r"\bunemployment rate\b", ("unemployment_rate",)),
    (r"\bretail sales\b", ("retail_sales",)),
    (r"\bGDP\b", ("gdp", "real_gdp")),
    (r"\bISM (?:non-? ?manufacturing|services)\b", ("ism_services_pmi",)),
    (r"\bISM manufacturing\b", ("ism_manufacturing_pmi",)),
    (r"\bmanufacturing PMI\b", ("manufacturing_pmi", "sp_global_manufacturing_pmi")),
    (r"\bservices PMI\b", ("services_pmi", "sp_global_services_pmi")),
    (r"\bcomposite PMI\b", ("composite_pmi", "sp_global_composite_pmi")),
    (r"\b(?:initial )?jobless claims\b", ("initial_jobless_claims",)),
    (r"\b(?:UMich|Michigan)\b.*\bconsumer sentiment\b", ("umich_consumer_sentiment",)),
    (r"\bbusiness outlook survey\b", ("boc_business_outlook_survey",)),
    (r"\bbusiness confidence\b", ("business_confidence", "nab_business_confidence")),
    (r"\bconsumer confidence\b", ("consumer_confidence",)),
    (r"\bbuilding permits\b", ("building_permits",)),
    (r"\bindustrial production\b", ("industrial_production",)),
    (r"\bimport price(?:s| index)?\b", ("import_prices", "import_price_index")),
    (r"\bfactory orders\b", ("factory_orders",)),
    (r"\bservices producer prices\b", ("services_ppi",)),
    (r"\b(?:factory inflation|producer prices|PPI)\b", ("ppi", "producer_price_index")),
    # Parsed for the audit trail; no calendar event carries these names, so they stay unmatched.
    (r"\bdurable goods\b", ("durable_goods",)),
    (r"\bCase-?Shiller\b", ("case_shiller_hpi",)),
    (r"\bNAHB\b", ("nahb_housing_market_index",)),
    (r"\bexisting home sales\b", ("existing_home_sales",)),
    (r"\bJOLTS\b", ("jolts_job_openings",)),
    (r"\bDallas Fed manufacturing\b", ("dallas_fed_manufacturing_index",)),
)
SUFFIXES = {None: ("", "_mom", "_yoy", "_qoq"), "mom": ("_mom",), "yoy": ("_yoy",), "qoq": ("_qoq",)}


@dataclass(frozen=True)
class DataPrint:
    country: str
    names: tuple[str, ...]  # candidate calendar canonical names
    actual: Decimal
    consensus: Decimal | None  # market expectation only (never prior / prelim)
    comparison: str | None
    reference: Decimal | None = None  # prelim value for final prints; disambiguation only

    @property
    def canonical_prefix(self) -> str:
        return self.names[0]


def _country(title: str, first_clause: str) -> str | None:
    found = [code for pattern, code in COUNTRIES if re.search(pattern, first_clause, re.I if code != "US" else 0)]
    if len(set(found)) == 1:
        return found[0]
    if not found and US_ONLY.search(first_clause):
        return "US"
    if len(set(found)) > 1 and "US" in found and US_ONLY.search(first_clause):
        return "US"
    return None


def parse_headline(title: str) -> DataPrint | None:
    """Parse explicit data prints; anything ambiguous stays unfilled."""
    if NOT_RELEASE.search(title):
        return None
    actual = consensus = reference = None
    clause = title
    if (match := COMPARE.search(title)):
        preceding = NUMBER.findall(title[:match.start()])
        if not preceding:
            return None
        actual = Decimal(preceding[-1])
        kind = match.group(2).lower()
        if kind.startswith(("exp", "est", "fore", "cons")):
            consensus = Decimal(match.group(1))
        elif kind.startswith("prelim"):
            reference = Decimal(match.group(1))
        clause = title[:match.start()]
    elif (match := PAREN.search(title)):
        actual, consensus = Decimal(match.group(1)), Decimal(match.group(2))
        clause = title[:match.start()]
    else:
        first = re.split(r",|;| as | amid | while | but | ahead of ", title, maxsplit=1)[0]
        match = NARRATIVE.search(first)
        if not match or len(NUMBER.findall(first)) != 1:
            return None
        actual = Decimal(match.group(1))
        clause = first[:match.start()]
    indicators = [names for pattern, names in INDICATORS if re.search(pattern, clause, re.I)]
    country = _country(title, clause)
    if not country or not indicators:
        return None
    names = indicators[0]
    flash = re.search(r"\b(?:flash|prelim(?:inary)?)\b", clause, re.I)
    if "PMI" in clause.upper():
        names = names + tuple(n + "_flash" for n in names) if flash else names
    comparison = "mom" if re.search(r"\b(?:m/m|mom|monthly)\b", title, re.I) else (
        "yoy" if re.search(r"\b(?:y/y|yoy|annual)\b", title, re.I) else (
            "qoq" if re.search(r"\b(?:q/q|qoq)\b", title, re.I) else None))
    expanded = tuple(n + s for n in names for s in SUFFIXES[comparison])
    return DataPrint(country, expanded, actual, consensus, comparison, reference)


def choose_candidate(candidates: list[dict], parsed: DataPrint) -> int | None:
    """Resolve co-released variants by forecast, then collapse exact duplicate versions."""
    target = parsed.consensus if parsed.consensus is not None else parsed.reference
    matching_forecast = [row for row in candidates if target is not None and row["estimate"] == target]
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
                      AND i.canonical_name = ANY(:names)
                    ORDER BY abs(extract(epoch FROM r.released_at - :published_at)), r.id
                """), {"country": parsed.country, "start": item["published_at"] - timedelta(minutes=30),
                        "end": item["published_at"] + timedelta(minutes=30),
                        "published_at": item["published_at"], "names": list(parsed.names)})).mappings().all()
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
