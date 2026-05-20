"""Retail positioning aggregation for the FX Outlook page.

The service merges public retail sentiment feeds into one cached snapshot:
Myfxbook community outlook, OANDA open positions, Binance long/short ratios,
and IG client sentiment when its public page exposes parseable values.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = int(os.getenv("RETAIL_SENTIMENT_CACHE_TTL", "300"))
HISTORY_DIR = Path(os.getenv("RETAIL_SENTIMENT_HISTORY_DIR", "data/retail_sentiment_history"))
TIMEOUT_SECONDS = 12.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

MYFXBOOK_LOGIN_URL = "https://www.myfxbook.com/api/login.json"
MYFXBOOK_OUTLOOK_URL = "https://www.myfxbook.com/api/get-community-outlook.json"
OANDA_URL = "https://www.oanda.com/cfds/labs/data/open_positions/"
BINANCE_URL = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
IG_URLS = [
    "https://www.ig.com/en/trading-strategies/_how-to-trade-using-ig-client-sentiment-240912",
    "https://www.ig.com/sg/trading-strategies/trading-on-sentiment--using-ig-client-sentiment-data-220930",
]
IG_URL = IG_URLS[0]

SOURCE_PRIORITY = ["myfxbook", "oanda", "binance", "ig"]

MFB_MAP = {
    "USOIL": "WTI",
    "UKOIL": "BRENT",
}

OANDA_MAP = {
    "EUR_USD": "EURUSD",
    "GBP_USD": "GBPUSD",
    "USD_JPY": "USDJPY",
    "AUD_USD": "AUDUSD",
    "USD_CAD": "USDCAD",
    "NZD_USD": "NZDUSD",
    "USD_CHF": "USDCHF",
    "EUR_GBP": "EURGBP",
    "EUR_JPY": "EURJPY",
    "GBP_JPY": "GBPJPY",
    "AUD_JPY": "AUDJPY",
    "CAD_CHF": "CADCHF",
    "EUR_NOK": "EURNOK",
    "EUR_CHF": "EURCHF",
    "USD_NOK": "USDNOK",
    "USD_SEK": "USDSEK",
    "USD_MXN": "USDMXN",
    "USD_CNH": "USDCNH",
    "NZD_CAD": "NZDCAD",
    "NZD_CHF": "NZDCHF",
    "XAU_USD": "XAUUSD",
    "XAG_USD": "XAGUSD",
    "BCO_USD": "BRENT",
    "WTICO_USD": "WTI",
    "CORN_USD": "CORN",
    "SUGAR_USD": "SUGAR",
    "NATGAS_USD": "NATGAS",
    "SPX500_USD": "SPX500",
    "NAS100_USD": "NAS100",
    "DE30_EUR": "DE30",
    "UK100_GBP": "UK100",
    "JP225_USD": "JAP225",
    "BTC_USD": "BTCUSD",
    "ETH_USD": "ETHUSD",
}

IG_MAP = {
    "EUR/USD": "EURUSD",
    "GBP/USD": "GBPUSD",
    "USD/JPY": "USDJPY",
    "AUD/USD": "AUDUSD",
    "USD/CAD": "USDCAD",
    "NZD/USD": "NZDUSD",
    "USD/CHF": "USDCHF",
    "EUR/GBP": "EURGBP",
    "EUR/JPY": "EURJPY",
    "GBP/JPY": "GBPJPY",
    "Wall Street": "DOW30",
    "Germany 40": "DE30",
    "US 500": "SPX500",
    "US Tech 100": "NAS100",
    "Japan 225": "JAP225",
    "Gold": "XAUUSD",
    "Silver": "XAGUSD",
    "Brent Crude Oil": "BRENT",
    "Oil - US Crude": "WTI",
    "Copper": "COPPER",
    "Natural Gas": "NATGAS",
    "Bitcoin": "BTCUSD",
    "Ethereum": "ETHUSD",
}

BINANCE_SYMBOLS = {
    "BTCUSDT": "BTCUSD",
    "ETHUSDT": "ETHUSD",
    "SOLUSDT": "SOLUSDT",
    "BNBUSDT": "BNBUSDT",
    "XRPUSDT": "XRPUSDT",
}

CATEGORY_MAP = {
    "EURUSD": "forex",
    "GBPUSD": "forex",
    "USDJPY": "forex",
    "AUDUSD": "forex",
    "USDCAD": "forex",
    "NZDUSD": "forex",
    "USDCHF": "forex",
    "EURGBP": "forex",
    "EURJPY": "forex",
    "GBPJPY": "forex",
    "AUDJPY": "forex",
    "CADCHF": "forex",
    "NOKSEK": "forex",
    "USDNOK": "forex",
    "USDSEK": "forex",
    "USDMXN": "forex",
    "USDCNH": "forex",
    "USDRUB": "forex",
    "NZDCAD": "forex",
    "NZDCHF": "forex",
    "NZDAGG": "forex",
    "EURNOK": "forex",
    "EURCHF": "forex",
    "GBPNOK": "forex",
    "NAS100": "indices",
    "DE30": "indices",
    "JAP225": "indices",
    "SCI25": "indices",
    "VIX": "indices",
    "SPX500": "indices",
    "DOW30": "indices",
    "UK100": "indices",
    "WTI": "commodities",
    "BRENT": "commodities",
    "XAUUSD": "commodities",
    "XAGUSD": "commodities",
    "COPPER": "commodities",
    "SUGAR": "commodities",
    "COTTON": "commodities",
    "XPDUSD": "commodities",
    "NATGAS": "commodities",
    "CORN": "commodities",
    "BTCUSD": "crypto",
    "ETHUSD": "crypto",
    "SOLUSDT": "crypto",
    "BNBUSDT": "crypto",
    "XRPUSDT": "crypto",
}

_cache: dict[str, Any] = {
    "data": None,
    "fetched_at": 0.0,
    "myfxbook_session": None,
    "sources": {},
}
_lock = asyncio.Lock()


def _symbol(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def classify_sentiment(net: float) -> str:
    if net < -20:
        return "Strong Bullish Contrarian"
    if net < -8:
        return "Bullish Contrarian"
    if net < 8:
        return "Neutral"
    if net < 20:
        return "Bearish Contrarian"
    return "Strong Bearish Contrarian"


def _category_for(symbol: str) -> str:
    if symbol in CATEGORY_MAP:
        return CATEGORY_MAP[symbol]
    if symbol.endswith("USD") and len(symbol) == 6:
        return "forex"
    return "forex"


def merge_sources(*source_dicts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for source_data in reversed(source_dicts):
        for name, data in source_data.items():
            source = data.get("source", "ig")
            current_source = merged.get(name, {}).get("source", "ig")
            if name not in merged or SOURCE_PRIORITY.index(source) < SOURCE_PRIORITY.index(current_source):
                merged[name] = data

    assets: list[dict[str, Any]] = []
    for name, data in merged.items():
        long_pct = round(float(data["long"]), 1)
        short_pct = round(float(data["short"]), 1)
        net = round(long_pct - short_pct, 1)
        assets.append(
            {
                "name": name,
                "category": _category_for(name),
                "long": long_pct,
                "short": short_pct,
                "net": net,
                "positions": int(data.get("positions") or 0),
                "source": data.get("source", "unknown"),
                "sentiment": classify_sentiment(net),
                "crowding": min(100, round(abs(net) * 2.2)),
                "avg_long": data.get("avg_long"),
                "avg_short": data.get("avg_short"),
            }
        )

    assets.sort(key=lambda item: (abs(item["net"]), item["positions"]), reverse=True)
    return assets


async def myfxbook_login(username: str, password: str) -> str | None:
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(
                MYFXBOOK_LOGIN_URL,
                params={"email": username, "password": password},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        log.warning("Myfxbook login error: %s", exc)
        return None

    if payload.get("error"):
        log.warning("Myfxbook login failed: %s", payload.get("message"))
        return None
    return payload.get("session")


async def fetch_myfxbook(session_id: str | None = None) -> dict[str, dict[str, Any]]:
    params = {"session": session_id} if session_id else None
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(MYFXBOOK_OUTLOOK_URL, params=params)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        log.warning("Myfxbook sentiment fetch failed: %s", exc)
        return {}

    if payload.get("error"):
        log.warning("Myfxbook API error: %s", payload.get("message", "unknown"))
        return {}

    results: dict[str, dict[str, Any]] = {}
    for item in payload.get("symbols", []):
        raw_name = _symbol(str(item.get("name", "")))
        if not raw_name:
            continue
        name = MFB_MAP.get(raw_name, raw_name)
        try:
            long_pct = float(item.get("longPercentage", 50))
            short_pct = float(item.get("shortPercentage", 50))
            positions = int(float(item.get("longPositions", 0))) + int(
                float(item.get("shortPositions", 0))
            )
        except (TypeError, ValueError):
            continue
        results[name] = {
            "long": long_pct,
            "short": short_pct,
            "positions": positions,
            "avg_long": item.get("avgLongPrice") or item.get("longAvgPrice"),
            "avg_short": item.get("avgShortPrice") or item.get("shortAvgPrice"),
            "source": "myfxbook",
        }
    return results


async def fetch_oanda() -> dict[str, dict[str, Any]]:
    try:
        async with httpx.AsyncClient(
            headers={**HEADERS, "Referer": "https://www.oanda.com/"},
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            response = await client.get(OANDA_URL)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        log.warning("OANDA sentiment fetch failed: %s", exc)
        return {}

    instruments = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(instruments, list):
        instruments = {
            str(item.get("instrument") or item.get("name") or ""): item
            for item in instruments
            if isinstance(item, dict)
        }
    if not isinstance(instruments, dict):
        return {}

    results: dict[str, dict[str, Any]] = {}
    for raw_key, value in instruments.items():
        if not isinstance(value, dict):
            continue
        name = OANDA_MAP.get(str(raw_key), _symbol(str(raw_key)))
        try:
            long_v = float(value.get("long", value.get("longUnits", 0)))
            short_v = float(value.get("short", value.get("shortUnits", 0)))
        except (TypeError, ValueError):
            continue
        total = long_v + short_v
        if total <= 0:
            continue
        results[name] = {
            "long": round(long_v / total * 100, 1),
            "short": round(short_v / total * 100, 1),
            "positions": int(total),
            "source": "oanda",
        }
    return results


async def fetch_binance_crypto() -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT_SECONDS) as client:
        for binance_symbol, name in BINANCE_SYMBOLS.items():
            try:
                response = await client.get(
                    BINANCE_URL,
                    params={"symbol": binance_symbol, "period": "5m", "limit": 1},
                )
                response.raise_for_status()
                payload = response.json()
                if not payload:
                    continue
                ratio = float(payload[0]["longShortRatio"])
            except Exception as exc:
                log.debug("Binance sentiment fetch failed for %s: %s", binance_symbol, exc)
                continue
            long_pct = round(ratio / (1 + ratio) * 100, 1)
            results[name] = {
                "long": long_pct,
                "short": round(100 - long_pct, 1),
                "positions": 0,
                "source": "binance",
            }
    return results


async def fetch_ig() -> dict[str, dict[str, Any]]:
    html = ""
    last_error: Exception | None = None
    async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
        for url in IG_URLS:
            try:
                response = await client.get(url)
                response.raise_for_status()
                html = response.text
                break
            except Exception as exc:
                last_error = exc
    if not html:
        log.warning("IG sentiment fetch failed: %s", last_error)
        return {}

    results: dict[str, dict[str, Any]] = {}
    for label, symbol in IG_MAP.items():
        escaped = re.escape(label)
        match = re.search(
            escaped + r".{0,500}?(\d+(?:\.\d+)?)\s*%.{0,120}?(\d+(?:\.\d+)?)\s*%",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            continue
        try:
            long_pct = float(match.group(1))
            short_pct = float(match.group(2))
        except ValueError:
            continue
        if 0 <= long_pct <= 100 and 0 <= short_pct <= 100:
            results[symbol] = {
                "long": round(long_pct, 1),
                "short": round(short_pct, 1),
                "positions": 0,
                "source": "ig",
            }
    return results


async def fetch_all_sources(myfxbook_session: str | None = None) -> dict[str, Any]:
    mfb, oanda, binance, ig = await asyncio.gather(
        fetch_myfxbook(myfxbook_session),
        fetch_oanda(),
        fetch_binance_crypto(),
        fetch_ig(),
    )
    assets = merge_sources(ig, binance, oanda, mfb)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "myfxbook": len(mfb),
            "oanda": len(oanda),
            "binance": len(binance),
            "ig": len(ig),
        },
        "count": len(assets),
        "assets": assets,
    }


def _save_history_sync(assets: list[dict[str, Any]], timestamp: str) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    for asset in assets:
        symbol = asset["name"]
        record = {
            "ts": timestamp,
            "long": asset["long"],
            "short": asset["short"],
            "net": asset["net"],
            "source": asset["source"],
            "positions": asset.get("positions", 0),
        }
        with (HISTORY_DIR / f"{symbol}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")


async def _save_history(assets: list[dict[str, Any]], timestamp: str) -> None:
    await asyncio.to_thread(_save_history_sync, assets, timestamp)


def _load_history_sync(symbol: str, days: int) -> list[dict[str, Any]]:
    path = HISTORY_DIR / f"{symbol}.jsonl"
    if not path.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    records: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                ts = datetime.fromisoformat(str(record["ts"]).replace("Z", "+00:00"))
                if ts >= cutoff:
                    records.append(record)
    except Exception as exc:
        log.warning("Retail sentiment history load failed for %s: %s", symbol, exc)
    return records


async def load_history(symbol: str, days: int = 90) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_load_history_sync, _symbol(symbol), days)


async def refresh_retail_sentiment(force: bool = False) -> dict[str, Any]:
    now = time.time()
    async with _lock:
        cached = _cache.get("data")
        if cached and not force and (now - float(_cache["fetched_at"])) < CACHE_TTL_SECONDS:
            return cached

        result = await fetch_all_sources(_cache.get("myfxbook_session"))
        _cache["data"] = result
        _cache["fetched_at"] = now
        _cache["sources"] = result.get("sources", {})
        if result.get("assets"):
            asyncio.create_task(_save_history(result["assets"], result["timestamp"]))
        return result


async def get_retail_sentiment(
    *,
    category: str = "all",
    sort: str = "rank",
    limit: int = 100,
    force: bool = False,
) -> dict[str, Any]:
    data = await refresh_retail_sentiment(force=force)
    assets = list(data.get("assets", []))

    if category != "all":
        assets = [asset for asset in assets if asset.get("category") == category]

    if sort == "az":
        assets.sort(key=lambda asset: asset["name"])
    elif sort == "long":
        assets.sort(key=lambda asset: asset["long"], reverse=True)
    elif sort == "short":
        assets.sort(key=lambda asset: asset["short"], reverse=True)
    elif sort == "net":
        assets.sort(key=lambda asset: abs(asset["net"]), reverse=True)
    else:
        assets.sort(key=lambda asset: (abs(asset["net"]), asset["positions"]), reverse=True)

    if limit > 0:
        assets = assets[:limit]

    return {
        "timestamp": data.get("timestamp"),
        "cache_age_s": round(time.time() - float(_cache.get("fetched_at", 0)), 1),
        "sources": data.get("sources", {}),
        "count": len(assets),
        "assets": assets,
    }


async def get_retail_sentiment_symbol(symbol: str) -> dict[str, Any] | None:
    symbol = _symbol(symbol)
    data = await refresh_retail_sentiment()
    asset = next((item for item in data.get("assets", []) if item["name"] == symbol), None)
    if not asset:
        return None
    history = await load_history(symbol, days=90)
    return {
        "symbol": symbol,
        "snapshot": asset,
        "history": history,
        "history_points": len(history),
        "timestamp": data.get("timestamp"),
    }


async def configure_myfxbook(username: str, password: str) -> bool:
    session = await myfxbook_login(username, password)
    if not session:
        return False
    async with _lock:
        _cache["myfxbook_session"] = session
    await refresh_retail_sentiment(force=True)
    return True


def source_status() -> dict[str, Any]:
    data = _cache.get("data") or {}
    sources = data.get("sources", {})
    return {
        "sources": [
            {
                "name": "myfxbook",
                "url": MYFXBOOK_OUTLOOK_URL,
                "requires_auth": True,
                "authenticated": bool(_cache.get("myfxbook_session")),
                "symbols_fetched": sources.get("myfxbook", 0),
                "coverage": "FX, commodities",
            },
            {
                "name": "oanda",
                "url": OANDA_URL,
                "requires_auth": False,
                "authenticated": True,
                "symbols_fetched": sources.get("oanda", 0),
                "coverage": "FX, indices, commodities, crypto",
            },
            {
                "name": "binance",
                "url": BINANCE_URL,
                "requires_auth": False,
                "authenticated": True,
                "symbols_fetched": sources.get("binance", 0),
                "coverage": "Crypto futures",
            },
            {
                "name": "ig",
                "url": IG_URL,
                "requires_auth": False,
                "authenticated": True,
                "symbols_fetched": sources.get("ig", 0),
                "coverage": "FX, indices, commodities, crypto",
            },
        ],
        "last_refresh": data.get("timestamp"),
        "cache_ttl_s": CACHE_TTL_SECONDS,
        "cache_age_s": round(time.time() - float(_cache.get("fetched_at", 0)), 1)
        if _cache.get("fetched_at")
        else None,
    }
