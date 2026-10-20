"""Dated, display-only Taylor-rule snapshots; no verdict or trading signal input."""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml
from sqlalchemy import text

from app.db.session import get_sessionmaker, session_scope
from app.processing.xlsx_reader import read_sheet
from app.services import source_identity
from app.services.cb_summary import bank_config

CONFIG = Path("config/policy_model.yaml")
BANKS = ("FED", "ECB", "BOE", "BOJ", "BOC", "RBA", "RBNZ", "SNB")


def config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def implied_rate(neutral: float, inflation: float, target: float, output_gap: float) -> tuple[float, float, float]:
    """Central rule and sensitivity envelope across three coefficient sets and r* +/-0.5pp."""
    central = neutral + inflation + .5 * (inflation - target) + .5 * output_gap
    scenarios = [r + inflation + a * (inflation - target) + b * output_gap
                 for r in (neutral - .5, neutral, neutral + .5)
                 for a, b in ((.5, .5), (1.0, .5), (.5, 1.0))]
    return round(central, 4), round(min(scenarios), 4), round(max(scenarios), 4)


def _input(value: float | None, as_of: date | None, source: str, confidence: str,
           observed_at: datetime, **extra: Any) -> dict:
    return {"value": value, "as_of": as_of.isoformat() if as_of else None,
            "source": source, "confidence": confidence, "observed_at": observed_at.isoformat(), **extra}


def parse_nyfed(content: bytes, cfg: dict, observed_at: datetime) -> dict[str, dict]:
    rows = read_sheet(content, "HLW Estimates")
    expected = cfg["nyfed_vintage"]["last_period"]
    if expected not in (rows[2][0] or ""):
        raise ValueError(f"NY Fed workbook vintage differs from configured {expected}")
    last = next((r for r in reversed(rows) if len(r) > 12 and r[0] and all(r[i] not in (None, "NA") for i in (10, 11, 12))), None)
    if last is None:
        raise ValueError("NY Fed r-star values missing")
    quarter = int(expected[-1])
    period = date(int(expected[:4]), quarter * 3, 1) + timedelta(days=32)
    period = period.replace(day=1) - timedelta(days=1)
    source = cfg["nyfed_vintage"]["source_url"]
    published = date.fromisoformat(str(cfg["nyfed_vintage"]["published_at"]))
    values = {}
    for key, index in (("nyfed_us", 10), ("nyfed_ca", 11), ("nyfed_ea", 12)):
        value = float(last[index])
        if not -5 <= value <= 10:
            raise ValueError(f"NY Fed {key} outside plausible bounds")
        values[key] = _input(value, period, source, "medium", observed_at,
                             published_at=published.isoformat(), vintage=expected)
    return values


def parse_oecd(content: str, cfg: dict, year: int, observed_at: datetime) -> dict[str, dict]:
    expected = cfg["oecd_vintage"]["dataflow_version"]
    source = cfg["oecd_vintage"]["source_url"]
    published = date.fromisoformat(str(cfg["oecd_vintage"]["published_at"]))
    values = {}
    for row in csv.DictReader(io.StringIO(content)):
        if row["MEASURE"] != "GAP" or row["FREQ"] != "A" or row["TIME_PERIOD"] != str(year):
            continue
        if not row["DATAFLOW"].endswith(f"({expected})"):
            raise ValueError("OECD output-gap vintage changed")
        value = float(row["OBS_VALUE"])
        if not -20 <= value <= 20:
            raise ValueError(f"OECD output gap outside plausible bounds for {row['REF_AREA']}")
        values[row["REF_AREA"]] = _input(value, published, source, "medium", observed_at,
                                          reference_year=year, vintage=cfg["oecd_vintage"]["name"])
    return values


async def _inflation(country: str, canonical: str | None, now: datetime) -> dict | None:
    if canonical is None:
        return None
    async with get_sessionmaker()() as session:
        row = (await session.execute(text("""SELECT r.actual::float AS value, r.released_at, r.retrieved_at
            FROM indicator_releases r JOIN indicators i ON i.id=r.indicator_id
            WHERE i.country_code=:country AND i.canonical_name=:canonical AND r.actual IS NOT NULL
              AND r.released_at<=:now AND r.retrieved_at<=:now
            ORDER BY r.released_at DESC,r.retrieved_at DESC,r.id DESC LIMIT 1"""),
            {"country": country, "canonical": canonical, "now": now})).mappings().first()
    if row is None:
        return None
    return _input(float(row["value"]), row["released_at"].date(), f"indicator:{country}:{canonical}", "high", now,
                  released_at=row["released_at"].isoformat(), retrieved_at=row["retrieved_at"].isoformat())


async def _market(bank: str, today: date, observed_at: datetime) -> dict | None:
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text("""SELECT curve_date,tenor_days,rate::float AS rate FROM ois_cache
            WHERE bank=:bank AND curve_date=(SELECT max(curve_date) FROM ois_cache
                WHERE bank=:bank AND curve_date<=:today) ORDER BY tenor_days"""),
            {"bank": bank, "today": today})).mappings().all()
    below = next((r for r in reversed(rows) if r["tenor_days"] <= 90), None)
    above = next((r for r in rows if r["tenor_days"] >= 90), None)
    if below is None or above is None:
        return None
    if below["tenor_days"] == above["tenor_days"]:
        value = float(below["rate"])
    else:
        weight = (90 - below["tenor_days"]) / (above["tenor_days"] - below["tenor_days"])
        value = float(below["rate"]) + weight * (float(above["rate"]) - float(below["rate"]))
    return _input(round(value, 4), below["curve_date"], f"ois_cache:{bank}:90d", "medium", observed_at,
                  horizon_days=90, tenor_bracket=[below["tenor_days"], above["tenor_days"]])


def evaluate(bank: str, inputs: dict, cfg: dict, today: date) -> dict:
    missing = []
    limits = cfg["freshness_days"]
    for name in ("neutral_rate", "inflation", "target", "output_gap", "market"):
        entry = inputs.get(name)
        if entry is None or entry.get("value") is None:
            missing.append(name)
        elif name in limits and (date.fromisoformat(entry["as_of"]) > today or
                                 (today - date.fromisoformat(entry["as_of"])).days > limits[name]):
            missing.append(f"{name} stale")
    if missing:
        return {"bank": bank, "status": "unavailable", "missing_inputs": missing, "inputs": inputs,
                "model_rate": None, "range_low": None, "range_high": None,
                "market_rate_3m": None, "gap_bp": None, "confidence": None}
    neutral, inflation, target, gap, market = (float(inputs[k]["value"]) for k in
                                                 ("neutral_rate", "inflation", "target", "output_gap", "market"))
    central, low, high = implied_rate(neutral, inflation, target, gap)
    confidence = "low" if any(v["confidence"] == "low" for v in inputs.values()) else "medium" if any(
        v["confidence"] == "medium" for v in inputs.values()) else "high"
    return {"bank": bank, "status": "available", "missing_inputs": [], "inputs": inputs,
            "model_rate": central, "range_low": low, "range_high": high,
            "market_rate_3m": market, "gap_bp": round(100 * (central - market), 2), "confidence": confidence}


async def refresh(today: date | None = None) -> dict[str, dict]:
    today = today or date.today()
    now = datetime.now(timezone.utc)
    cfg = config()
    oecd, nyfed = {}, {}
    async with httpx.AsyncClient(timeout=40, headers=source_identity.headers()) as client:
        try:
            url = f"{cfg['oecd_url']}?startPeriod={today.year}&endPeriod={today.year}&format=csvfile"
            response = await client.get(url)
            response.raise_for_status()
            oecd = parse_oecd(response.text, cfg, today.year, now)
        except (httpx.HTTPError, ValueError, KeyError):
            pass
        try:
            response = await client.get(cfg["nyfed_url"])
            response.raise_for_status()
            nyfed = parse_nyfed(response.content, cfg, now)
        except (httpx.HTTPError, ValueError, KeyError):
            pass
    banks = bank_config()
    results = {}
    for bank in BANKS:
        entry = cfg["banks"][bank]
        neutral = nyfed.get(entry["neutral"])
        if entry["neutral"] in cfg.get("neutral_fallbacks", {}):
            fallback = cfg["neutral_fallbacks"][entry["neutral"]]
            neutral = _input(float(fallback["value"]), date.fromisoformat(str(fallback["published_at"])),
                             fallback["source_url"], fallback["confidence"], now, basis=fallback["basis"])
        target_value = banks[bank]["inflation_target"].get("value")
        inputs = {"neutral_rate": neutral,
                  "inflation": await _inflation(entry["country"], entry["inflation"], now),
                  "target": _input(float(target_value), today, "config/central_banks.yaml", "high", now)
                  if target_value is not None else None,
                  "output_gap": oecd.get(entry["oecd"]), "market": await _market(bank, today, now)}
        result = evaluate(bank, inputs, cfg, today)
        result.update({"model_version": cfg["model_version"], "data_date": today, "computed_at": now})
        async with session_scope() as session:
            await session.execute(text("""INSERT INTO policy_model_daily
                (bank,data_date,model_version,status,missing_inputs,inputs,model_rate,range_low,range_high,
                 market_rate_3m,gap_bp,confidence,computed_at)
                VALUES (:bank,:data_date,:model_version,:status,CAST(:missing_inputs AS json),CAST(:inputs AS json),:model_rate,:range_low,:range_high,
                        :market_rate_3m,:gap_bp,:confidence,:computed_at)
                ON CONFLICT (bank,data_date) DO UPDATE SET model_version=EXCLUDED.model_version,
                status=EXCLUDED.status,missing_inputs=EXCLUDED.missing_inputs,inputs=EXCLUDED.inputs,
                model_rate=EXCLUDED.model_rate,range_low=EXCLUDED.range_low,range_high=EXCLUDED.range_high,
                market_rate_3m=EXCLUDED.market_rate_3m,gap_bp=EXCLUDED.gap_bp,confidence=EXCLUDED.confidence,
                computed_at=EXCLUDED.computed_at"""), {
                    **result, "missing_inputs": json.dumps(result["missing_inputs"], default=str),
                    "inputs": json.dumps(result["inputs"], default=str),
                })
        results[bank] = result
    return results


async def latest(bank: str) -> dict:
    if bank not in BANKS:
        raise ValueError(f"Unknown bank {bank}")
    async with get_sessionmaker()() as session:
        row = (await session.execute(text("""SELECT bank,data_date,model_version,status,missing_inputs,inputs,
            model_rate::float AS model_rate,range_low::float AS range_low,range_high::float AS range_high,
            market_rate_3m::float AS market_rate_3m,gap_bp::float AS gap_bp,confidence,computed_at
            FROM policy_model_daily WHERE bank=:bank ORDER BY data_date DESC LIMIT 1"""),
            {"bank": bank})).mappings().first()
    return dict(row) if row else {"bank": bank, "status": "unavailable", "missing_inputs": ["model snapshot"]}
