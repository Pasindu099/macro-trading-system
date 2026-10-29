"""Data contexts for the redesigned Positioning sections; no predictive model."""
from datetime import date, timedelta
from statistics import median

from app.services import positioning as pos
from app.services.positioning_values import money, notional, price_name


def side(percentile):
    return None if percentile is None else "on" if percentile >= 65 else "against" if percentile <= 35 else None


def extreme(percentile):
    return percentile is not None and (percentile <= 15 or percentile >= 85)


def bet_change(net, prior):
    if net is None or prior is None:
        return "unavailable"
    if not net:
        return "closed the bet" if prior else "no bet"
    if not prior:
        return "opened the bet"
    if net*prior < 0:
        return "switched sides"
    return "adding to the bet" if abs(net) > abs(prior) else "cutting the bet" if abs(net) < abs(prior) else "bet unchanged"


def simple_card(currency, fast, slow):
    a, b = side((fast or {}).get("percentile")), side((slow or {}).get("percentile"))
    is_extreme = extreme((fast or {}).get("percentile")) or extreme((slow or {}).get("percentile"))
    if not fast or not slow or fast.get("percentile") is None or slow.get("percentile") is None:
        status, rank = "Inputs unavailable", 4
    elif a and b and a != b:
        status, rank = "They disagree", 0
    elif a and a == b:
        status, rank = "Everyone betting " + a, 1 if is_extreme else 2
    elif a:
        status, rank = "Hedge funds only", 1 if is_extreme else 2
    elif b:
        status, rank = "Slow money only", 1 if is_extreme else 2
    else:
        status, rank = "No strong bets", 3
    dates = [row["report_date"] for row in (fast, slow) if row]
    stale = bool(dates and (date.today()-date.fromisoformat(min(dates))).days > 10)
    mismatch = len(set(dates)) > 1
    def position_label(value):
        return "near the top" if value == "on" else "near the bottom" if value == "against" else "near the middle"
    sentence = (f"Fast money sits {position_label(a)} and slow money {position_label(b)} of their own recent ranges."
                if fast and slow else "One or both trader groups have no available readings.")
    if stale or mismatch:
        sentence = "These readings are stale or have different report dates; a current comparison is unavailable."
        status, rank = "Inputs stale", 4
    return {"currency": currency, "status": status, "rank": rank, "extreme": is_extreme,
            "state": "stale" if stale or mismatch else "unavailable" if rank == 4 else "available",
            "sentence": sentence, "stale": stale or mismatch, "groups": {"fast_money": fast, "slow_money": slow},
            "watch": "Opposite sides show disagreement between trader groups." if rank == 0 else "An extreme reading describes an unusual bet, not the next price move."}


async def big_traders(lookback="3y"):
    years = pos._years(lookback)
    positions, spots = await pos.load_positions(), await pos.load_spot()
    simple, bars = [], []
    for ccy in pos.CURRENCIES:
        groups = {}
        for category in ("leveraged_funds", "asset_manager", "dealer"):
            weeks = positions.get(ccy, {}).get(category, [])
            if not weeks:
                groups[category] = None
                continue
            snap = pos.category_snapshot(weeks, years)
            value = notional(ccy, weeks[-1], spots.get(ccy))
            snap.update({"net_usd": value, "net_usd_bn": value/1e9 if value is not None else None,
                         "money": money(value), "side": side(snap["percentile"]), "extreme": extreme(snap["percentile"]),
                         "direction": "long" if snap["net"] > 0 else "short" if snap["net"] < 0 else "flat",
                         "bet_change": bet_change(snap["net"], weeks[-2].net if len(weeks) > 1 else None)})
            groups[category] = snap
        card = simple_card(ccy, groups["leveraged_funds"], groups["asset_manager"])
        simple.append(card)
        bars.append({"currency": ccy, "groups": groups, "watch": card["watch"], "stale": card["stale"]})
    simple.sort(key=lambda c: (c["rank"], -max([abs((g or {}).get("percentile", 50)-50) for g in c["groups"].values() if g and g.get("percentile") is not None] or [0]), c["currency"]))
    return {"lookback": lookback, "default_view": "simple", "simple": simple, "bars": bars,
            "group_notes": {"asset_manager": "Long-term investors manage money for clients such as pension funds.",
                            "dealer": "Dealers often hold the other side of client trades."}}


async def squeeze_watch(lookback="3y", data=None):
    data = data if data is not None else await pos.get_squeeze(lookback)
    cards = []
    for row in data["currencies"]:
        item = row.get("leveraged_funds") or {}
        if item.get("crowding") not in {"crowded_long", "crowded_short"}:
            continue
        ccy = row["currency"]
        side_ = "on" if item["crowding"] == "crowded_long" else "against"
        status = item["status"]
        move, typical, nets = item.get("spot_move_2w_pct"), item.get("typical_2w_move_pct"), item.get("net_last_3w", [])
        price_text = (f"{price_name(ccy)} is moving against them" if item.get("against_crowd") else
                      f"the move in {price_name(ccy)} does not meet the threshold" if item.get("against_crowd") is False else f"evidence for {price_name(ccy)} is unavailable")
        flow_text = ("they have started backing out" if item.get("reducing") else
                     "they have not started backing out" if item.get("reducing") is False else "two consecutive weekly changes are unavailable")
        headline = f"Hedge funds are heavily betting {side_} {ccy}, {price_text}, and {flow_text}."
        stale = (date.today()-date.fromisoformat(item["report_date"])).days > 10
        label = ("Squeeze risk: " + status if status in {"high", "medium"} else
                 "Squeeze assessment unavailable" if item.get("against_crowd") is None or item.get("reducing") is None else
                 "Crowded, but still working for them")
        if stale:
            headline, label = "The stored bets are stale, so a current squeeze assessment is unavailable.", "Inputs stale"
        changes = [nets[i]-nets[i-1] for i in (1,2)] if len(nets) == 3 else []
        oi = item.get("open_interest_last_3w", [])
        changes_pct = [100*(nets[i]/oi[i]-nets[i-1]/oi[i-1]) for i in (1,2)] if len(oi) == 3 and all(oi) else []
        price_evidence = (f"{ccy} {move:+.2f}% in two weeks; " +
            ("at least" if abs(move) >= typical*.5 else "less than") + f" half its usual move ({typical*.5:.2f}%)"
            if move is not None and typical is not None else "Matching price history is unavailable")
        cards.append({"currency": ccy, "status": status, "label": label, "headline": headline,
            "stale": stale,
            "percentile": item["percentile"], "report_date": item["report_date"], "price_date": item.get("price_date"),
            "price_basis": price_name(ccy), "checklist": [
                {"label": "Big one-sided bet", "checked": True, "evidence": f"Heavy bet {side_} {ccy}, {item['percentile']:.0f} of 100"},
                {"label": f"{price_name(ccy).capitalize()} moving against them", "checked": item.get("against_crowd"), "move_2w_pct": move,
                 "evidence": price_evidence,
                 "typical_2w_move_pct": typical, "threshold_pct": typical*.5 if typical is not None else None},
                {"label": "They are backing out", "checked": item.get("reducing"), "changes": changes,
                 "changes_pct_oi": changes_pct, "evidence": (f"Bet changes {changes_pct[0]:+.2f} and {changes_pct[1]:+.2f} percentage points of open contracts; " +
                    ("shrinking" if item.get("reducing") else "not shrinking") if changes_pct else "Two weekly bet changes are unavailable")}],
            "link": "/desks/"+ccy, "rank": pos.SQUEEZE_RANK[status]})
    cards.sort(key=lambda c: (-c["rank"], -abs(c["percentile"]-50)))
    return {"cards": cards, "rule": "Price against the crowd must reach half the standard deviation of two-week returns in the selected lookback."}


def episode_entries(percentiles, predicate):
    inside = False
    result = []
    for i, value in enumerate(percentiles):
        if value is None:
            continue
        now_inside = predicate(value)
        if now_inside and not inside:
            result.append(i)
        inside = now_inside
    return result


def band_comparisons(weeks, spot):
    dates = [w.report_date for w in weeks]
    percentiles = pos.rolling_percentiles(dates,[w.net for w in weeks],3)
    bands = {}
    for key,(predicate,direction) in pos.EXTREME_BANDS.items():
        moves4, moves8 = [], []
        for i in episode_entries(percentiles,predicate):
            if dates[i]+timedelta(weeks=8) > dates[-1]:
                continue
            base = spot.on(dates[i]) if spot else None
            m4 = pos.pct_move(base,spot.on(dates[i]+timedelta(weeks=4))) if spot else None
            m8 = pos.pct_move(base,spot.on(dates[i]+timedelta(weeks=8))) if spot else None
            if m8 is not None:
                moves8.append(m8)
                if m4 is not None:
                    moves4.append(m4)
        enough = len(moves8) >= 5
        bands[key] = {"episodes":len(moves8),"too_few":not enough,
            "median_move_4w_pct":median(moves4) if enough and moves4 else None,
            "median_move_8w_pct":median(moves8) if enough else None,
            "against_crowd_after_8w":100*sum(direction*v < 0 for v in moves8)/len(moves8) if enough else None}
    return bands


def extreme_history(weeks, spot):
    dates = [w.report_date for w in weeks]
    percentiles = pos.rolling_percentiles(dates, [w.net for w in weeks], 3)
    current = percentiles[-1] if percentiles else None
    if current is None:
        return {"state": "unavailable", "message": "There is not enough covered history for a current percentile.", "episodes": []}
    if not extreme(current):
        return {"state": "not_extreme", "percentile": current, "episodes": [],
                "message": f"Hedge funds are not at an extreme right now, {current:.0f} of 100, so there are no comparable past cases."}
    direction = 1 if current >= 85 else -1
    predicate = (lambda p: p >= 85) if direction > 0 else (lambda p: p <= 15)
    episodes = []
    for i in episode_entries(percentiles, predicate):
        if dates[i] < date(2010,1,1) or dates[i]+timedelta(weeks=8) > dates[-1]:
            continue
        start = spot.on(dates[i]) if spot else None
        move4 = pos.pct_move(start, spot.on(dates[i]+timedelta(weeks=4))) if spot else None
        move8 = pos.pct_move(start, spot.on(dates[i]+timedelta(weeks=8))) if spot else None
        if move8 is not None:
            episodes.append({"date": dates[i].isoformat(), "move_4w_pct": move4, "move_8w_pct": move8,
                             "against_crowd": direction*move8 < 0, "arrow": "▲" if move8 > 0 else "▼" if move8 < 0 else ""})
    moves = [e["move_8w_pct"] for e in episodes]
    enough = len(episodes) >= 5
    return {"state": "available" if enough else "too_few", "percentile": current, "episodes": episodes if enough else [],
            "count": len(episodes), "up": sum(v > 0 for v in moves), "down": sum(v < 0 for v in moves),
            "flat": sum(v == 0 for v in moves), "median_8w_pct": median(moves) if enough else None,
            "median_rise_pct": median([v for v in moves if v > 0]) if enough and any(v > 0 for v in moves) else None,
            "worst_fall_pct": min(moves) if enough and any(v < 0 for v in moves) else None,
            "outcome_range_pct": {"min":min(moves),"max":max(moves)} if enough else None,
            "against_crowd": sum(e["against_crowd"] for e in episodes) if enough else None,
            "message": "Fewer than five covered episodes; comparisons are unavailable." if not enough else None}


async def extremes(currency):
    currency = pos._currency(currency)
    weeks = (await pos.load_positions([currency])).get(currency, {}).get("leveraged_funds", [])
    spots = await pos.load_spot()
    result = extreme_history(weeks, spots.get(currency))
    result.update({"currency": currency, "bands": band_comparisons(weeks,spots.get(currency)) if weeks else {}})
    if result["state"] == "available":
        rise = f" (typical rise {result['median_rise_pct']:+.1f}%)" if result['median_rise_pct'] is not None else ""
        fall = f" (worst {result['worst_fall_pct']:+.1f}%)" if result['worst_fall_pct'] is not None else ""
        flat = f"; in {result['flat']} it was unchanged" if result['flat'] else ""
        result["reading"] = (f"Hedge funds held a bet this heavily {'on' if result['percentile'] >= 85 else 'against'} {currency} {result['count']} times since 2010. "
            f"In {result['up']} cases {price_name(currency)} rose over eight weeks{rise}; in {result['down']} it fell{fall}{flat}.")
        result["context"] = f"In {result['against_crowd']} of {result['count']} past cases {price_name(currency)} moved against the crowd within eight weeks."
        result["comparison"] = {"median_rise_pct": result["median_rise_pct"], "worst_fall_pct": result["worst_fall_pct"],
                                "up": result["up"], "down": result["down"], "flat": result["flat"]}
    result["report_date"] = str(weeks[-1].report_date) if weeks else None
    if weeks and (date.today()-weeks[-1].report_date).days > 10:
        result.update({"state":"stale", "message":"The current positioning inputs are stale; a current comparison is unavailable.",
                       "reading":"The current positioning inputs are stale; a current comparison is unavailable."})
    return result
