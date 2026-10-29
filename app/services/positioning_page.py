"""Step 14 C: panel contexts for /positioning. Descriptive wording only, no forecasts.

Colour roles (template/CSS): amber = long / building long, blue = short / building short (positioning,
not a price call); green/red only for price moves.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.services import myfxbook, positioning as pos

CATEGORY_LABELS = {"leveraged_funds": "Leveraged funds", "asset_manager": "Asset managers", "dealer": "Dealers"}
PRICE_LABELS = {"USD": "DXY (computed)", "EUR": "EUR/USD", "GBP": "GBP/USD", "AUD": "AUD/USD", "NZD": "NZD/USD",
                "CAD": "CAD (inverse USD/CAD)", "CHF": "CHF (inverse USD/CHF)", "JPY": "JPY (inverse USD/JPY)"}
LOOKBACK_LABELS = {"52w": "52 weeks", "1y": "1 year", "3y": "3 years", "5y": "5 years"}
BAND_LABELS = {">90": "Above 90th (extreme long)", "85-90": "85th–90th", "10-15": "10th–15th",
               "<10": "Below 10th (extreme short)"}
MIN_EPISODES = 5
SPARK_MIN_DAYS = 7
HOW_TO_USE = ("How to use it: COT is weekly and published three days after its Tuesday as-of date, so it is risk "
              "context, not a timing tool. It covers CME futures only, a sample of a market that trades mostly OTC. "
              "Extremes can persist for months; the pattern to watch is extreme positioning plus price starting to "
              "move against it.")


def desk_link(ccy: str) -> dict[str, str | None]:
    from app.services.desks import get_desk

    return ({"href": f"/desks/{ccy.lower()}", "label": f"Open {ccy} desk →"} if get_desk(ccy)
            else {"href": None, "label": f"{ccy} desk pending"})


def kfmt(value: float | int | None, sign: bool = True) -> str:
    if value is None:
        return "—"
    k = value / 1000
    return (f"{k:+.1f}k" if sign else f"{k:.1f}k").replace("-", "−")


def arrow(value: float | int | None) -> str:
    return "" if not value else ("▲" if value > 0 else "▼")


def pct_cell(percentile: float | None) -> dict[str, Any]:
    if percentile is None:
        return {"label": "—", "kind": "none", "alpha": 0}
    p = round(percentile)
    crowded = "long" if percentile >= pos.CROWDED_LONG else "short" if percentile <= pos.CROWDED_SHORT else None
    return {"label": f"{p}" + (f" · crowded {crowded}" if crowded else ""),
            "kind": "long" if percentile >= 50 else "short", "crowded": crowded,
            "alpha": round(abs(percentile - 50) / 50 * 0.75 + 0.08, 2)}


def crowd_read(lf: dict[str, Any] | None, squeeze: dict[str, Any] | None) -> str:
    if not lf or lf.get("percentile") is None:
        return "No leveraged-funds history"
    p = lf["percentile"]
    crowding = pos.crowding_label(p)
    if crowding in {"crowded_long", "crowded_short"}:
        side = "long" if crowding == "crowded_long" else "short"
        status = (squeeze or {}).get("status")
        if status == "high":
            return f"Crowded {side}; {side}s being cut while price moves against them"
        if status == "medium" and (squeeze or {}).get("reducing"):
            return f"Crowded {side}; {side}s cut two weeks running"
        if status == "medium":
            return f"Crowded {side}; price moving against the crowd"
        if status == "working":
            return f"Crowded {side}; price still moving with the crowd"
        return f"Crowded {side}"
    if p >= 65:
        return "Leaning long"
    if p <= 35:
        return "Leaning short"
    return "Neutral"


async def summary(lookback: str, **_: Any) -> dict[str, Any]:
    data = await pos.get_summary(lookback)
    release = data["release"]
    if release["as_of"] is None:
        return {"state": "pending", "message": "No COT report stored yet."}
    return {"state": "ok", "text": data["summary"]["text"], "release": release, "how_to_use": HOW_TO_USE,
            "as_of": date.fromisoformat(release["as_of"]), "released": date.fromisoformat(release["released"]),
            "next": date.fromisoformat(release["next_release"]) if release["next_release"] else None}


async def crowding(lookback: str, **_: Any) -> dict[str, Any]:
    years = pos._years(lookback)
    positions = await pos.load_positions()
    if not positions:
        return {"state": "pending", "message": "No COT history stored yet."}
    squeeze = {row["currency"]: row.get("leveraged_funds") for row in (await pos.get_squeeze(lookback))["currencies"]}
    rows = []
    for ccy in ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"):
        by_cat = positions.get(ccy, {})
        snaps = {cat: pos.category_snapshot(by_cat[cat], years) if by_cat.get(cat) else None for cat in CATEGORY_LABELS}
        lf = snaps["leveraged_funds"]
        rows.append({"ccy": ccy, "cells": {cat: pct_cell(s["percentile"] if s else None) for cat, s in snaps.items()},
                     "net": kfmt(lf["net"]) if lf else "—", "chg": lf["change_1w"] if lf else None,
                     "chg_label": kfmt(lf["change_1w"]) if lf else "—", "arrow": arrow(lf["change_1w"]) if lf else "",
                     "read": crowd_read(lf, squeeze.get(ccy))})
    return {"state": "ok", "rows": rows, "lookback_label": LOOKBACK_LABELS[lookback]}


def flow_kind(change: int, net: int | None) -> str:
    if change > 0:
        return "short-covering" if net is not None and net < 0 else "long-building"
    return "long-liquidation" if net is not None and net > 0 else "short-building"


async def flows(lookback: str, window: str = "1W", **_: Any) -> dict[str, Any]:
    data = await pos.get_flows(window)
    vals = [(row["currency"], row["leveraged_funds"]["change"], row["leveraged_funds"]["net"])
            for row in data["currencies"] if row["leveraged_funds"]["change"] is not None]
    if not vals:
        return {"state": "pending", "message": "Not enough COT weeks for flows."}
    vals.sort(key=lambda item: item[1], reverse=True)
    peak = max(abs(v) for _, v, _ in vals) or 1
    rows = [{"ccy": c, "label": kfmt(v), "arrow": arrow(v), "kind": "long" if v >= 0 else "short",
             "left": 50 if v >= 0 else round(50 - abs(v) / peak * 50, 2), "width": round(abs(v) / peak * 50, 2)}
            for c, v, _ in vals]
    biggest = max(vals, key=lambda item: abs(item[1]))
    building = sum(1 for _, v, n in vals if flow_kind(v, n) in {"long-building", "short-building"})
    read = (f"Read: the largest {window} flow is {flow_kind(biggest[1], biggest[2])} in {biggest[0]} "
            f"({kfmt(biggest[1])} contracts). {building} of {len(vals)} currencies saw positions added, "
            f"{len(vals) - building} saw positions reduced.")
    return {"state": "ok", "rows": rows, "window": window, "read": read}


async def squeeze(lookback: str, **_: Any) -> dict[str, Any]:
    data = await pos.get_squeeze(lookback)
    cards = []
    for row in data["currencies"]:
        lf = row.get("leveraged_funds") or {}
        if lf.get("status") not in {"high", "medium", "working"}:
            continue
        side = "long" if lf["crowding"] == "crowded_long" else "short"
        move = lf.get("spot_move_2w_pct")
        nets = lf.get("net_last_3w") or []
        flow = (f"{side.title()}s cut two weeks running" if lf.get("reducing")
                else f"Net change last week {kfmt(nets[-1] - nets[-2])}" if len(nets) >= 2 else "—")
        status_label = {"high": "Squeeze risk: high", "medium": "Squeeze risk: medium",
                        "working": "Crowded, but still working for them"}[lf["status"]]
        read = {"high": "Extreme positioning, price moving against it, and the crowd reducing: all three are present.",
                "medium": "Extreme positioning with one of the two other ingredients (price against, crowd reducing).",
                "working": "The other squeeze ingredients do not both meet the threshold; "
                                    "while that holds."}[lf["status"]]
        cards.append({"ccy": row["currency"], "position": f"Crowded {side}", "status": lf["status"],
                      "status_label": status_label, "pct": f"{pos.ordinal(lf['percentile'])} percentile",
                      "move": move, "move_label": f"{row['currency']} {move:+.2f}% over 2W" if move is not None else "—",
                      "flow": flow, "read": read, "link": desk_link(row["currency"]),
                      "rank": pos.SQUEEZE_RANK[lf["status"]]})
    cards.sort(key=lambda card: card["rank"], reverse=True)
    if not cards:
        return {"state": "empty", "message": "No crowded currency among leveraged funds this week "
                                             "(all between the 15th and 85th percentile)."}
    return {"state": "ok", "cards": cards}


def chart(history: list[dict[str, Any]], prices: list[float | None], flagged: list[bool]) -> dict[str, Any]:
    """Server-side SVG geometry (600 x 220): net bars around the midline, price polyline, divergence spans."""
    n = len(history)
    bw = 600 / n
    peak = max(abs(h["net"]) for h in history) * 1.1 or 1
    long_path, short_path = [], []
    for i, h in enumerate(history):
        height = abs(h["net"]) / peak * 105
        x, w = i * bw + bw * 0.15, bw * 0.7
        if h["net"] >= 0:
            long_path.append(f"M{x:.1f} {110 - height:.1f}h{w:.1f}v{height:.1f}h-{w:.1f}Z")
        else:
            short_path.append(f"M{x:.1f} 110h{w:.1f}v{height:.1f}h-{w:.1f}Z")
    valid = [p for p in prices if p is not None]
    points, lo, hi = "", None, None
    if valid:
        lo, hi = min(valid), max(valid)
        span = (hi - lo) or 1
        points = " ".join(f"{i * bw + bw / 2:.1f},{210 - (p - lo) / span * 200:.1f}"
                          for i, p in enumerate(prices) if p is not None)
    spans, start = [], None
    for i, flag in enumerate([*flagged, False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            spans.append({"x": round(start * bw, 1), "w": round((i - start) * bw, 1)})
            start = None
    return {"long": "".join(long_path), "short": "".join(short_path), "price": points, "spans": spans,
            "pos_max": kfmt(peak, sign=False), "px_max": hi, "px_min": lo}


async def price(lookback: str, ccy: str = "JPY", **_: Any) -> dict[str, Any]:
    years = pos._years(lookback)
    weeks = (await pos.load_positions([ccy])).get(ccy, {}).get("leveraged_funds", [])
    if len(weeks) < 8:
        return {"state": "pending", "message": f"Not enough leveraged-funds history for {ccy}."}
    spot = (await pos.load_spot()).get(ccy)
    dates, nets = [w.report_date for w in weeks], [float(w.net) for w in weeks]
    pctl = pos.rolling_percentiles(dates, nets, years)
    start = max(4, len(weeks) - 52)
    history, prices, flagged = [], [], []
    for i in range(start, len(weeks)):
        move = pos.pct_move(spot.on(dates[i - 4]), spot.on(dates[i])) if spot else None
        flag = pos.divergence_flag(pctl[i], weeks[i].net - weeks[i - 4].net, move,
                                   pos.change_4w_std(weeks[:i + 1], years))["flag"] == "divergence"
        history.append({"date": dates[i], "net": weeks[i].net})
        prices.append(spot.on(dates[i]) if spot else None)
        flagged.append(flag)
    base = next((v for v in prices if v is not None), None)
    indexed = [v / base * 100 if v is not None and base else None for v in prices]
    geo = chart(history, indexed, flagged)
    first, last = history[0], history[-1]
    price_move = pos.pct_move(next((p for p in prices if p is not None), None), prices[-1])
    move4 = pos.pct_move(prices[-5], prices[-1]) if len(prices) >= 5 else None
    chg4 = weeks[-1].net - weeks[-5].net
    read = (f"Read: over {len(history)} weeks leveraged funds went from {kfmt(first['net'])} to {kfmt(last['net'])} "
            f"contracts" + (f" while {PRICE_LABELS[ccy]} moved {price_move:+.1f}%." if price_move is not None else ".")
            + (f" Last 4 weeks: net {kfmt(chg4)}, price {move4:+.1f}%; " if move4 is not None else " ")
            + ("divergence flagged (positioning and price disagree at an extreme)." if flagged[-1]
               else "no divergence flagged."))
    return {"state": "ok" if spot else "partial", "ccy": ccy, "geo": geo, "read": read,
            "price_label": f"{PRICE_LABELS[ccy]}, indexed to 100", "first_date": first["date"], "last_date": last["date"],
            "message": None if spot else f"No price series for {ccy}; positioning only."}


async def extremes(lookback: str, ccy: str = "JPY", **_: Any) -> dict[str, Any]:
    data = await pos.get_extremes(ccy)
    if data.get("status") == "unavailable":
        return {"state": "unavailable", "message": data["reason"]}
    weeks = (await pos.load_positions([ccy])).get(ccy, {}).get("leveraged_funds", [])
    now_pct = pos.category_snapshot(weeks, 3)["percentile"] if weeks else None
    rows = []
    for band, stats in data["bands"].items():
        in_band, _ = pos.EXTREME_BANDS[band]
        rows.append({"label": BAND_LABELS[band], "episodes": stats["episodes"],
                     "enough": stats["episodes"] >= MIN_EPISODES, "current": now_pct is not None and in_band(now_pct),
                     "m4": stats["avg_move_4w_pct"], "m8": stats["avg_move_8w_pct"],
                     "against": stats["against_crowd_after_8w"], "adverse": stats["max_adverse_move_8w"]})
    return {"state": "ok", "ccy": ccy, "rows": rows, "now_pct": now_pct, "min_episodes": MIN_EPISODES}


def sparkline(points: list[dict[str, Any]]) -> str | None:
    if len(points) < SPARK_MIN_DAYS:
        return None
    n = len(points)
    return " ".join(f"{i / (n - 1) * 80:.1f},{20 - float(p['long_pct']) / 100 * 20:.1f}" for i, p in enumerate(points))


async def retail(lookback: str, **_: Any) -> dict[str, Any]:
    data = await pos.get_institutions_vs_retail(lookback)
    history = await myfxbook.daily_all(30) if data["retail_state"] == "ok" else {}
    rows = []
    for row in data["pairs"]:
        implied = row["cot_implied"]
        rows.append({**row, "href": "/pairs",
                     "cot_label": "—" if implied is None else f"{row['cot_side'].capitalize()} ({implied:+.0f})",
                     "kind": "long" if implied and implied > pos.IMPLIED_BIAS else
                             "short" if implied and implied < -pos.IMPLIED_BIAS else "neutral",
                     "retail_label": "—" if row["retail_long_pct"] is None else f"{row['retail_long_pct']:.0f}% long",
                     "spark": sparkline(history.get(row["pair"].replace("/", ""), []))})
    order = {"opposite sides": 0, "same side": 1, "no signal": 2}
    rows.sort(key=lambda r: (order[r["signal"]], -abs(r["cot_implied"] or 0)))
    return {"state": "ok", "rows": rows, "retail_state": data["retail_state"], "retail_message": data["retail_message"]}


PANELS = {"summary": summary, "crowding": crowding, "flows": flows, "squeeze": squeeze,
          "price": price, "extremes": extremes, "retail": retail}
TITLES = {"summary": "Positioning", "crowding": "1 · Crowding map", "flows": "2 · Flows: who is building, who is unwinding?",
          "squeeze": "3 · Squeeze watch", "price": "4 · Positioning vs price", "extremes": "5 · What happened after past extremes?",
          "retail": "6 · Institutions vs retail"}
SOURCES = {"summary": ("cot",), "crowding": ("cot",), "flows": ("cot",), "squeeze": ("cot", "fx_spot"),
           "price": ("cot", "fx_spot"), "extremes": ("cot", "fx_spot"), "retail": ("cot", "myfxbook_retail")}
