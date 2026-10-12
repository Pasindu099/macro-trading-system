"""Read models for the seven Overview panels. No upstream score calculations live here."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import re

import yaml
from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.services import ecb_regime, fed_regime, positioning, situations, verdict
from app.services.curve_metrics import get_curve
from app.services.macro_state import get_macro_state_board
from app.services.meeting_calendar import get_upcoming_meetings
from app.services.fred import load_series

CURRENCIES = ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF")
BANKS = {"USD": "FED", "EUR": "ECB", "GBP": "BOE", "JPY": "BOJ", "AUD": "RBA",
         "NZD": "RBNZ", "CAD": "BOC", "CHF": "SNB"}
COUNTRIES = {"USD": "US", "EUR": "DE", "GBP": "UK", "JPY": "JP", "AUD": "AU",
             "NZD": "NZ", "CAD": "CA", "CHF": "CH"}
DOMINANCE_CONFIG = Path("config/dominance.yaml")
HIERARCHY = tuple(yaml.safe_load(DOMINANCE_CONFIG.read_text(encoding="utf-8"))["tiers"])


async def _board() -> dict:
    async with get_sessionmaker()() as session:
        board = await get_macro_state_board(session)
    today = datetime.now(UTC).date()
    board["rows"] = [row for row in board["rows"] if isinstance(row.get("date"), (date, datetime))
                     and (row["date"].date() if isinstance(row["date"], datetime) else row["date"]) <= today]
    return board


def _pair_rows(scores: dict[str, float], pairs: list[dict]) -> list[dict]:
    rows = []
    for item in pairs:
        base, quote = item["base"], item["quote"]
        a, b = scores.get(base), scores.get(quote)
        rows.append({"pair": item["pair"], "overall": round(a - b, 2) if a is not None and b is not None else None,
                     "policy": "available" if {base, quote} == {"EUR", "USD"} else "pending"})
    return sorted(rows, key=lambda row: abs(row["overall"] or 0), reverse=True)


NEWS_CURRENCY_WORDS = {
    "USD": ("USD", "US", "FED", "DOLLAR", "DXY", "UNITED STATES"),
    "EUR": ("EUR", "EURO", "ECB", "EUROZONE", "GERMANY", "FRANCE"),
    "GBP": ("GBP", "STERLING", "BOE", "BRITAIN", "UK"),
    "JPY": ("JPY", "YEN", "BOJ", "JAPAN"),
    "AUD": ("AUD", "AUSSIE", "RBA", "AUSTRALIA"),
    "NZD": ("NZD", "KIWI", "RBNZ", "NEW ZEALAND"),
    "CAD": ("CAD", "LOONIE", "BOC", "CANADA"),
    "CHF": ("CHF", "FRANC", "SNB", "SWITZERLAND"),
}
NEWS_TOPICS = (
    ("Geopolitical shock", ("WAR", "INVASION", "MISSILE", "SANCTIONS", "CONFLICT", "TARIFF", "CEASEFIRE")),
    ("Risk sentiment", ("VIX", "RISK-OFF", "EQUITIES", "STOCKS", "MARKET SELLOFF")),
    ("CB policy divergence", ("RATE", "FED", "ECB", "BOE", "BOJ", "RBA", "RBNZ", "BOC", "SNB", "CENTRAL BANK", "YIELD", "OIS")),
    ("Economic data", ("CPI", "INFLATION", "PAYROLL", "JOBS", "UNEMPLOYMENT", "GDP", "RETAIL SALES", "PMI", "WAGES")),
    ("Positioning", ("POSITIONING", "COT", "SPECULATORS", "SHORT SQUEEZE")),
    ("Technical", ("BREAKOUT", "SUPPORT LEVEL", "RESISTANCE LEVEL")),
)
SITUATION_NEWS_TOPICS = {
    "energy_shock": ("OIL", "BRENT", "ENERGY"),
    "fr_fiscal_stress": ("FRANCE", "FRENCH", "OAT"),
    "labor_deterioration": ("JOBS", "PAYROLL", "UNEMPLOYMENT", "LABOR", "LABOUR"),
    "risk_off": ("VIX", "RISK-OFF", "EQUITIES", "STOCKS", "MARKET SELLOFF"),
    "bear_steepening": ("YIELD", "STEEPENING", "BOND"),
    "yields_up_currency_down": ("YIELD", "BOND"),
    "correlation_break": ("CORRELATION", "DECOUPLING"),
    "intervention_risk_jpy": ("INTERVENTION", "YEN", "JPY"),
}


def _word_match(headline: str, words: tuple[str, ...]) -> bool:
    upper = headline.upper()
    return any(re.search(r"(?<![A-Z])" + re.escape(word) + r"(?![A-Z])", upper) for word in words)


def _classify_news(headline: str, episodes: list[dict]) -> dict | None:
    currencies = [currency for currency, words in NEWS_CURRENCY_WORDS.items() if _word_match(headline, words)]
    tier = next((name for name, words in NEWS_TOPICS if _word_match(headline, words)), None)
    for episode in episodes:
        words = SITUATION_NEWS_TOPICS.get(episode["situation_id"], ())
        affected = episode["scope_key"].replace(":", "/").split("/")
        if words and _word_match(headline, words) and (not currencies or
                "GLOBAL" in affected or any(currency in affected for currency in currencies)):
            return {"currencies": currencies or ["G10"], "tag": episode["name"]}
    return {"currencies": currencies or ["G10"], "tag": tier} if tier else None


def _dominance_tiers(shares: dict | None, *, risk_off: bool, geo_count: int,
                     vix: float | None, vix_change: float | None,
                     stretched: list[str], squeeze_against_crowd: bool) -> tuple[list[dict], str]:
    """Return six fixed tiers and the highest-priority qualifying driver."""
    rates = shares.get("rates") if shares else None
    data = shares.get("data") if shares else None
    geopolitical = risk_off and geo_count >= 2
    elevated = risk_off or (vix is not None and vix > 20) or (vix_change is not None and vix_change >= 4)
    policy = rates is not None and rates >= .25 and (data is None or rates >= data)
    economic = data is not None and data >= .25 and (rates is None or data > rates)
    positioning_lead = bool(stretched and squeeze_against_crowd)
    lead = next((name for qualifies, name in ((geopolitical, HIERARCHY[0]),
               (risk_off, HIERARCHY[1]), (policy, HIERARCHY[2]),
               (economic, HIERARCHY[3]), (positioning_lead, HIERARCHY[4]))
               if qualifies), "No single driver")
    risk_state = "Dominant" if risk_off and lead == HIERARCHY[1] else (
        "Elevated" if elevated else "Calm" if vix is not None and vix < 15 else "Neutral" if vix is not None else "Pending")
    evidence = [
        f"{geo_count} high-impact geopolitics headlines in 3 days" if geo_count else "No confirmed geopolitical cluster",
        f"VIX {vix:.1f}" if vix is not None else "VIX pending",
        f"Rates explain {rates:.0%} of G10 moves (20d)" if rates is not None else "Rates attribution pending",
        f"Data explain {data:.0%} of G10 moves (20d)" if data is not None else "Data attribution pending",
        f"Stretched in {', '.join(stretched)}" if stretched else "No confirmed positioning stretch",
        "Level engine pending",
    ]
    rows = []
    for index, name in enumerate(HIERARCHY):
        active = (geopolitical, elevated, policy or (rates is not None and rates >= .15),
                  economic or (data is not None and data >= .15), bool(stretched), False)[index]
        state = "Dominant" if name == lead else risk_state if index == 1 else "Active" if index == 0 and active else "Supporting" if active else "Secondary"
        rows.append({"number": index + 1, "name": name, "state": state, "evidence": evidence[index]})
    return rows, lead


async def hero() -> dict:
    episodes = [row for row in await situations.get_situation_episodes(active=True)
                if row["started_at"] <= datetime.now(UTC).date()]
    board = await _board()
    today = datetime.now(UTC).date()
    ranking = sorted((row for row in board["rows"] if row["currency"] in CURRENCIES
                      and row.get("overall_score") is not None and isinstance(row.get("date"), date)
                      and row["date"] <= today),
                     key=lambda row: row["overall_score"], reverse=True)
    if len(ranking) < 8:
        return {"state": "pending", "message": "All-eight currency ranking pending current data."}
    async with get_sessionmaker()() as session:
        snapshot = (await session.execute(text("""
            SELECT shares FROM dominance_snapshots WHERE window_days = 20
              AND snapshot_date <= current_date ORDER BY snapshot_date DESC LIMIT 1
        """))).mappings().first()
        headlines = (await session.execute(text("""
            SELECT title FROM raw_news WHERE source = 'investinglive.com' AND is_gated_relevant
              AND published_at BETWEEN now() - interval '3 days' AND now()
        """))).scalars().all()
    keywords = yaml.safe_load(DOMINANCE_CONFIG.read_text(encoding="utf-8"))["geopolitics_keywords"]
    geo_count = sum(any(word in title.lower() for word in keywords) for title in headlines)
    vix_rows = [(day, value) for day, value in await load_series("VIXCLS", today - timedelta(days=10)) if day <= today]
    vix = vix_rows[-1][1] if vix_rows else None
    old_vix = next((value for day, value in reversed(vix_rows) if day <= today - timedelta(days=5)), None)
    crowd = (await positioning.get_crowding()).get("currencies", [])
    stretched = [row["currency"] for row in crowd if (p := row.get("leveraged_funds", {}).get("percentile")) is not None
                 and (p >= 85 or p <= 15)]
    squeeze = (await positioning.get_squeeze()).get("currencies", []) if stretched else []
    spot = await positioning.load_spot() if stretched else {}
    squeeze_against_crowd = any(
        row["currency"] in stretched and row.get("leveraged_funds", {}).get("status") == "high"
        and (move := positioning.pct_move(spot[row["currency"]].on(today - timedelta(days=7)),
                                          spot[row["currency"]].on(today))) is not None
        and (move < -1.5 if row["leveraged_funds"]["percentile"] >= 85 else move > 1.5)
        for row in squeeze if row["currency"] in spot
    )
    tiers, driver = _dominance_tiers(snapshot["shares"] if snapshot else None,
        risk_off=any(row["situation_id"] == "risk_off" for row in episodes), geo_count=geo_count,
        vix=vix, vix_change=vix - old_vix if vix is not None and old_vix is not None else None,
        stretched=stretched, squeeze_against_crowd=squeeze_against_crowd)
    top = max(episodes, key=lambda row: {"high": 3, "medium": 2, "low": 1}.get(row["severity"], 0), default=None)
    theme = (f"{driver}." + (f" Theme: {top['name']}." if top else "")
             + f" {ranking[0]['currency']} strongest and {ranking[-1]['currency']} weakest on the ranking.")
    return {"state": "ok", "theme": theme, "driver": driver, "hierarchy": tiers}


async def active_situations() -> dict:
    rows = _group_situations(await situations.get_situation_episodes(active=True))
    return {"state": "ok" if rows else "empty", "rows": rows,
            "message": "No active situations."}


def _situation_evidence(situation_id: str, evidence: dict) -> str:
    current = evidence.get("current", evidence) if isinstance(evidence, dict) else {}
    if not isinstance(current, dict):
        return "Evidence pending"
    def number(key: str, unit: str = "") -> str | None:
        value = current.get(key)
        return f"{value:+.1f}{unit}" if isinstance(value, (int, float)) else None
    lines = {
        "energy_shock": ("Brent 3m", "brent_3m_pct", "%"),
        "fr_fiscal_stress": ("OAT–Bund 10Y", "oat_bund_10y_bp", "bp"),
        "labor_deterioration": ("Unemployment gap", "unemployment_gap_pp", "pp"),
        "risk_off": ("VIX", "vix", ""),
        "bear_steepening": ("10s–30s change", "ten_s_thirty_s_1m_change_bp", "bp"),
        "yields_up_currency_down": ("2Y yield change", "two_y_10d_change_bp", "bp"),
        "intervention_risk_jpy": ("USD/JPY distance from zone", "intervention_zone_distance_pct", "%"),
    }
    if situation_id == "correlation_break":
        return "Spot and 2Y rates drivers diverging" if current.get("rates_drivers_diverging") == 1 else "Rates driver evidence pending"
    label, key, unit = lines.get(situation_id, ("Evidence", "", ""))
    value = number(key, unit)
    if situation_id == "energy_shock" and value:
        gap = number("headline_core_gap_pp", "pp")
        return f"{label} {value}; headline CPI above core by {gap}" if gap else f"{label} {value}"
    return f"{label} {value}" if value else "Evidence pending"


def _group_situations(episodes: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    now = datetime.now(UTC).date()
    for episode in episodes:
        if episode["started_at"] > now:
            continue
        key = episode["situation_id"]
        card = grouped.setdefault(key, {"name": episode["name"], "severity": episode["severity"],
            "started_at": episode["started_at"], "currencies": set(), "pairs": set(),
            "evidence": "Evidence pending", "href": None, "_latest": date.min})
        card["started_at"] = min(card["started_at"], episode["started_at"])
        scope = episode["scope_key"]
        if episode["scope"] == "pair":
            card["pairs"].add(scope)
            card["currencies"].update(scope.split("/"))
        elif scope == "GLOBAL":
            card["currencies"].update(CURRENCIES)
        else:
            card["currencies"].add(scope.split(":")[0])
        if episode["started_at"] >= card["_latest"]:
            card["evidence"] = _situation_evidence(key, episode.get("evidence") or {})
            card["_latest"] = episode["started_at"]
    rows = []
    for card in grouped.values():
        card["currencies"] = [currency for currency in CURRENCIES if currency in card["currencies"]]
        card["pairs"] = sorted(card["pairs"])
        card["href"] = (f"/pairs?pair={card['pairs'][0]}" if card["pairs"] else
                        next((f"/desks/{currency}" for currency in card["currencies"] if currency in ("USD", "EUR")), None))
        del card["_latest"]
        rows.append(card)
    return sorted(rows, key=lambda row: (-{"high": 3, "medium": 2, "low": 1}.get(row["severity"], 0), row["started_at"]))


async def moving_markets() -> dict:
    now = datetime.now(UTC)
    since = now - timedelta(hours=24)
    async with get_sessionmaker()() as session:
        raw = (await session.execute(text("""
            SELECT title AS headline, source, url, published_at AS seen_at, NULL::text AS severity
            FROM raw_news WHERE published_at >= :since AND published_at <= :now
              AND source = 'investinglive.com' AND is_gated_relevant
            ORDER BY published_at DESC LIMIT 60
        """), {"since": since, "now": now})).mappings().all()
    episodes = [row for row in await situations.get_situation_episodes(active=True)
                if row["started_at"] <= now.date()]
    seen = set()
    rows = []
    for item in sorted([dict(row) for row in raw], key=lambda row: row["seen_at"], reverse=True):
        title = item["headline"].strip()
        classification = _classify_news(title, episodes)
        if classification is None or title.lower() in seen or not since <= item["seen_at"] <= now:
            continue
        seen.add(title.lower())
        rows.append({**item, **classification})
    return {"state": "ok" if rows else "empty", "rows": rows[:6],
            "ai_paused": True,
            "message": "No high-impact items in the last 24 hours."}


async def currency_ranking() -> dict:
    board = await _board()
    by_currency = {row["currency"]: row for row in board["rows"]}
    crowding = {row["currency"]: row for row in (await positioning.get_crowding())["currencies"]}
    desk_views = {currency: await verdict.get_verdict(currency) for currency in ("USD", "EUR")}
    regimes = {"USD": await fed_regime.get_regime(), "EUR": await ecb_regime.get_regime()}
    rows = []
    for currency in CURRENCIES:
        macro = by_currency.get(currency)
        curve = await get_curve(COUNTRIES[currency], "1M")
        cot = crowding.get(currency, {}).get("leveraged_funds", {})
        view = desk_views.get(currency)
        gap_raw = view.get("why", {}).get("gap", {}).get("raw") if view else None
        gap = (gap_raw.get("direction") or gap_raw.get("gap_bp")) if isinstance(gap_raw, dict) else None
        rows.append({"currency": currency, "macro": macro["overall_score"] if macro else None,
                     "date": macro["date"] if macro else None, "cot": cot.get("percentile"),
                     "curve": curve.get("regime", {}).get("label") if curve.get("regime", {}).get("status") == "available" else None,
                     "verdict": view, "enabled": currency in desk_views, "gap": gap,
                     "cb_regime": regimes[currency].get("regime") if currency in regimes else None})
    rows.sort(key=lambda row: (row["macro"] is None, -(row["macro"] or 0)))
    return {"state": "ok" if any(row["macro"] is not None for row in rows) else "pending", "rows": rows}


async def central_bank_map() -> dict:
    regimes = {"USD": await fed_regime.get_regime(), "EUR": await ecb_regime.get_regime()}
    rows = []
    for currency in CURRENCIES:
        bank = BANKS[currency]
        meetings = await get_upcoming_meetings(bank, 1)
        regime = regimes.get(currency)
        rows.append({"currency": currency, "bank": bank, "regime": regime,
                     "meeting": datetime.fromisoformat(meetings[0]["meeting_dt"]) if meetings else None,
                     "state": "ok" if regime and regime.get("regime") != "unavailable" else "pending"})
    return {"state": "ok", "rows": rows}


async def pair_map() -> dict:
    board = await _board()
    scores = {row["currency"]: float(row["overall_score"]) for row in board["rows"]
              if row["overall_score"] is not None}
    pairs = yaml.safe_load(Path("config/pairs.yaml").read_text(encoding="utf-8"))["pairs"]
    rows = _pair_rows(scores, pairs)
    available = [abs(row["overall"]) for row in rows if row["overall"] is not None]
    concentration = round(sum(sorted(available, reverse=True)[:3]) / sum(available) * 100, 1) if sum(available) else None
    return {"state": "ok" if available else "pending", "rows": rows,
            "concentration": concentration}


async def key_events() -> dict:
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text("""
            SELECT r.released_at, i.display_name, i.country_code, c.currency_code,
                   r.estimate::float AS estimate, r.previous::float AS previous
            FROM indicator_releases r JOIN indicators i ON i.id = r.indicator_id
            JOIN countries c ON c.code = i.country_code
            WHERE r.released_at > now() AND r.released_at <= now() + interval '7 days'
              AND i.importance = 1 AND r.is_latest
            ORDER BY r.released_at, i.display_name LIMIT 16
        """))).mappings().all()
    return {"state": "ok" if rows else "empty", "rows": [dict(row) for row in rows],
            "message": "No high-impact events in the next seven days."}


PANELS = {"hero": hero, "situations": active_situations, "news": moving_markets,
          "ranking": currency_ranking, "central-banks": central_bank_map,
          "pairs": pair_map, "events": key_events}
