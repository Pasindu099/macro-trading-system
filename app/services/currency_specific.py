"""Source-backed currency-specific desk evidence for Step 16 Part E."""

from __future__ import annotations

import csv
import io
import math
from datetime import date, timedelta

import httpx
from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.services.desk_panels import fx_series, yield_series
from app.services.situations import get_situation_episodes

SNB_URL = "https://data.snb.ch/api/cube/snbgwdchfsgw/data/csv/en?dimSel=D0(GI)"
SIGHT_LABEL = ("intervention proxy — sight deposits also move with repos, swaps and "
               "banknotes; not confirmed intervention")


def latest(series: dict[date, float] | list[tuple[date, float]], day: date, max_days: int = 7):
    rows = list(series.items()) if isinstance(series, dict) else series
    valid = [(d, v) for d, v in rows if d <= day and (day - d).days <= max_days]
    return max(valid) if valid else None


def change(series: dict[date, float] | list[tuple[date, float]], day: date,
           max_days: int = 7):
    rows = sorted((d, v) for d, v in (series.items() if isinstance(series, dict) else series)
                  if d <= day)
    if len(rows) < 2 or (day - rows[-1][0]).days > max_days:
        return None
    return rows[-1][1] - rows[-2][1]


def shock_type(oil: float | None, equities: float | None, yields: float | None,
               vix: float | None) -> str:
    if oil is None or oil <= 0:
        return "unclassified"
    if (equities is not None and equities < 0) or (vix is not None and vix > 0):
        return "supply-driven"
    if equities is not None and equities > 0 and yields is not None and yields > 0:
        return "demand-driven"
    return "unclassified"


def correlation(left: dict[date, float], right: dict[date, float], minimum: int = 12):
    days = sorted(set(left) & set(right))
    pairs = [(left[b] - left[a], right[b] - right[a]) for a, b in zip(days, days[1:])]
    pairs = pairs[-20:]
    if len(pairs) < minimum:
        return None
    xs, ys = zip(*pairs)
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in pairs)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    return round(cov / math.sqrt(vx * vy), 3) if vx and vy else None


def parse_sight_deposits(payload: str, day: date) -> dict:
    lines = payload.lstrip("\ufeff").splitlines()
    publication = next((line.split(";")[1].strip('"')[:10] for line in lines
                        if line.startswith('"PublishingDate";')), None)
    if publication is None or date.fromisoformat(publication) > day:
        return {"state": "unavailable", "reason": "SNB publication date unavailable or future"}
    records = []
    for row in csv.reader(io.StringIO("\n".join(lines)), delimiter=";"):
        if len(row) == 3 and row[1] == "GI" and row[2]:
            try:
                observed = date.fromisoformat(row[0])
                if observed <= day:
                    records.append((observed, float(row[2])))
            except ValueError:
                continue
    if not records:
        return {"state": "unavailable", "reason": "No released weekly SNB observation"}
    observed, value = max(records)
    return {"state": "ok" if (day - observed).days <= 14 else "stale",
            "date": observed, "value_chf_millions": value, "published_at": publication,
            "frequency": "weekly", "label": SIGHT_LABEL, "source_url": SNB_URL}


async def _fred(day: date) -> dict[str, dict[date, float]]:
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text("""SELECT series_id,observation_date,value::float AS value
            FROM fred_observations WHERE series_id IN ('DCOILBRENTEU','SP500','VIXCLS')
            AND observation_date BETWEEN :start AND :day AND realtime_start <= :day"""),
            {"start": day - timedelta(days=65), "day": day})).mappings().all()
    out = {k: {} for k in ("DCOILBRENTEU", "SP500", "VIXCLS")}
    for row in rows:
        out[row["series_id"]][row["observation_date"]] = row["value"]
    return out


async def _us_surprise(day: date) -> dict:
    async with get_sessionmaker()() as session:
        row = (await session.execute(text("""SELECT sum(s.surprise_normalized)::float AS value,
            count(*) AS releases FROM event_innovation_scores s
            JOIN indicators i ON i.id=s.indicator_id
            WHERE i.country_code='US' AND s.scored AND s.release_date BETWEEN :start AND :day"""),
            {"start": day - timedelta(days=30), "day": day})).mappings().first()
    return {"state": "ok", "value": row["value"], "releases": row["releases"], "window_days": 30} if row and row["releases"] else {"state": "unavailable", "reason": "US scored surprises"}


async def panel(currency: str, day: date | None = None) -> dict:
    day = day or date.today()
    currency = currency.upper()
    if currency not in {"GBP", "JPY", "CAD", "CHF"}:
        raise ValueError(f"Unsupported currency {currency}")
    since = day - timedelta(days=65)
    if currency == "GBP":
        curve = await yield_series("UK", ["2Y", "10Y", "30Y"], since)
        fx = (await fx_series(["GBP/USD"]))["GBP/USD"]
        long_move, pound_move = change(curve.get("30Y", {}), day), change(fx, day)
        return {"currency": currency, "as_of": day,
                "gilt_curve": {t: latest(curve.get(t, {}), day) for t in ("2Y", "10Y", "30Y")},
                "fiscal_stress": {"state": "possible risk-premium shock" if long_move is not None and pound_move is not None and long_move > 0 and pound_move < 0 else "not flagged" if long_move is not None and pound_move is not None else "unavailable",
                                  "30y_change_pp": long_move, "gbpusd_change": pound_move}}
    if currency == "JPY":
        jp = await yield_series("JP", ["2Y", "10Y", "30Y"], since)
        us = await yield_series("US", ["2Y"], since)
        fx = (await fx_series(["USD/JPY"]))["USD/JPY"]
        fred = await _fred(day)
        spot = dict(fx)
        spread = {d: us["2Y"][d] - jp["2Y"][d] for d in set(us.get("2Y", {})) & set(jp.get("2Y", {}))}
        links = {"VIX": correlation(spot, fred["VIXCLS"]),
                 "equities": correlation(spot, fred["SP500"]),
                 "US–JP 2Y spread": correlation(spot, spread)}
        present = {k: v for k, v in links.items() if v is not None}
        driver = max(present, key=lambda k: abs(present[k])) if present else "unavailable"
        situations = await get_situation_episodes(active=True)
        risk = [s for s in situations if s["situation_id"] == "intervention_risk_jpy"]
        return {"currency": currency, "as_of": day,
                "jgb_curve": {t: latest(jp.get(t, {}), day) for t in ("2Y", "10Y", "30Y")},
                "mof_intervention_risk": risk, "rolling_correlations": links,
                "trading_on": driver, "window": "20 daily changes"}
    if currency == "CAD":
        fred = await _fred(day)
        ca = await yield_series("CA", ["2Y"], since)
        us = await yield_series("US", ["2Y"], since)
        usd_cad = (await fx_series(["USD/CAD"]))["USD/CAD"]
        moves = {"oil": change(fred["DCOILBRENTEU"], day),
                 "equities": change(fred["SP500"], day),
                 "vix": change(fred["VIXCLS"], day),
                 "ca_2y": change(ca.get("2Y", {}), day)}
        return {"currency": currency, "as_of": day,
                "brent": latest(fred["DCOILBRENTEU"], day),
                "usd_cad": latest(usd_cad, day),
                "terms_of_trade": {"state": "unavailable", "reason": "Canadian export/import price index series not configured"},
                "oil_shock": {"classification": shock_type(moves["oil"], moves["equities"], moves["ca_2y"], moves["vix"]), "changes": moves,
                              "note": "A supply-driven oil rise is not automatically CAD-positive."},
                "us_surprise_index": await _us_surprise(day),
                "two_year_yields": {"US": latest(us.get("2Y", {}), day), "CA": latest(ca.get("2Y", {}), day)}}
    fx = (await fx_series(["EUR/CHF", "USD/CHF"]))
    fred = await _fred(day)
    chf_strength = {d: -v for d, v in fx["USD/CHF"]}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"{SNB_URL}&fromDate={(day - timedelta(days=42)).isoformat()}")
            response.raise_for_status()
        deposits = parse_sight_deposits(response.text, day)
    except (httpx.HTTPError, ValueError):
        deposits = {"state": "unavailable", "reason": "SNB weekly feed", "label": SIGHT_LABEL, "source_url": SNB_URL}
    return {"currency": currency, "as_of": day, "eur_chf": latest(fx["EUR/CHF"], day),
            "sight_deposits": deposits, "chf_vs_vix_correlation": correlation(chf_strength, fred["VIXCLS"]),
            "window": "20 daily changes"}
