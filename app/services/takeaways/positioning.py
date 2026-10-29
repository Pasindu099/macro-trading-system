"""Pure, descriptive readings and deterministic panel inputs."""
from datetime import date
from app.services import positioning as pos, positioning_step18 as v2, positioning_values as values
from app.services.cot_legacy import specs

MISSING = "The inputs needed for a current reading are unavailable."


def summary(data):
    cards = [c for c in data.get("cards",[]) if not c.get("stale")]
    return f"The leading squeeze-watch entry is {cards[0]['currency']}. {cards[0]['headline']}" if cards else "No heavily one-sided hedge fund bet is recorded in the available data."


def crowding(data):
    bets = [(r["currency"],r.get("groups",{}).get("leveraged_funds")) for r in data.get("bars",[]) if not r.get("stale")]
    bets = sorted([(c,g) for c,g in bets if g and g.get("net_usd") is not None],key=lambda item:abs(item[1]["net_usd"]),reverse=True)
    if not bets: return MISSING
    names = " and ".join(f"{c} ({values.money(g['net_usd'])})" for c,g in bets[:2])
    text = f"Hedge funds hold their largest reported dollar bets in {names}."
    opposite = next((r for r in data.get("simple",[]) if r["status"] == "They disagree" and not r.get("stale")),None)
    return text + (f" On {opposite['currency']}, hedge funds and asset managers sit on opposite sides of their recent ranges." if opposite else "")


def flows(data):
    rows = [r for r in data.get("rows",[]) if r.get("change_usd") is not None and r.get("net") is not None]
    if not rows: return MISSING
    parts = []
    for r in sorted(rows,key=lambda r:abs(r["change_usd"]),reverse=True)[:2]:
        action,ccy = r["action"],r["currency"]
        side = "long" if r["net"] > 0 else "short" if r["net"] < 0 else "flat"
        words = (f"added to their {ccy} {side} bet" if action == "adding to the bet" else
                 f"cut their {ccy} {side} bet" if action == "cutting the bet" else
                 f"switched sides on {ccy}" if action == "switched sides" else
                 f"closed their {ccy} bet" if action == "closed the bet" else
                 f"opened a {ccy} {side} bet" if action == "opened the bet" else f"left their {ccy} bet unchanged")
        parts.append(words+f" (${abs(r['change_usd'])/1e9:.2f}bn)")
    return ("This month" if data.get("window") == "4W" else "This week")+" hedge funds "+" and ".join(parts)+"."


def squeeze(data):
    cards = [c for c in data.get("cards",[]) if not c.get("stale")]
    return cards[0]["headline"] if cards else "No heavily one-sided hedge fund bet is recorded in the available data."


def price(data):
    rows = data.get("history",[])
    if len(rows) < 5: return MISSING
    a,b = rows[-5],rows[-1]
    if (date.fromisoformat(b["report_date"])-date.fromisoformat(a["report_date"])).days != 28:
        return "Four consecutive weeks of positioning are unavailable."
    if any(r.get(k) is None for r in (a,b) for k in ("net","price")) or not a["price"]: return MISSING
    change,move = b["net"]-a["net"],100*(b["price"]/a["price"]-1)
    amount = f"{abs(change):.1f} percentage points of open interest" if data.get("units") == "pct_oi" else f"${abs(change):.2f}bn"
    action = "increased" if change > 0 else "reduced" if change < 0 else "left unchanged"
    direction = "rose" if move > 0 else "fell" if move < 0 else "was unchanged"
    text = f"Over four weeks {data.get('group_label','hedge funds').lower()} {action} their net position in {data.get('instrument','this market')} by {amount}, while the price {direction} {abs(move):.1f}%."
    return text + (" The price and bet changes meet the divergence rule." if data.get("divergence") else "")


def extremes(data):
    return data.get("message") or data.get("reading") or MISSING


def retail(data):
    if data.get("retail_state") != "ok":
        return "Retail comparisons are unavailable because no usable Myfxbook snapshot is configured or stored."
    pairs = data.get("pairs",[])
    usable = [r for r in pairs if r.get("cot_implied") is not None and r.get("retail_long_pct") is not None]
    if not usable: return MISSING
    opposite = [r for r in usable if r.get("signal") == "opposite sides"]
    text = f"On {len(opposite)} of {len(pairs)} pairs, retail traders are on the opposite side of hedge funds."
    if len(usable) < len(pairs):
        text = text[:-1] + f"; {len(pairs)-len(usable)} pairs lack usable comparisons."
    if opposite: text += f" The clearest case is {max(opposite,key=lambda r:abs(r['cot_implied']))['pair']}."
    return text


NOW = {"summary":summary,"crowding":crowding,"flows":flows,"squeeze":squeeze,"price":price,"extremes":extremes,"retail":retail}


async def inputs(panel, params):
    lookback = params.get("lookback","3y")
    if panel in {"summary","squeeze"}:
        result = await v2.squeeze_watch(lookback)
        result.update(state="available",stale=bool(result["cards"] and all(c.get("stale") for c in result["cards"])))
        if not result["cards"] and not any(len(g.get("leveraged_funds",[])) >= 3 for g in (await pos.load_positions()).values()):
            result["state"] = "unavailable"
        return result
    if panel == "crowding":
        result = await v2.big_traders(lookback)
        result.update(state="available",stale=all(c["state"] in {"stale","unavailable"} for c in result["simple"]))
        return result
    if panel == "price":
        instrument = params.get("instrument",params.get("ccy","JPY"))
        result = await values.history(instrument,group=params.get("group","leveraged_funds"),units=params.get("units","usd"),range_key=params.get("range_key","3Y"))
        if instrument in pos.CURRENCIES and params.get("group","leveraged_funds") == "leveraged_funds":
            divergence = await pos.get_divergence(lookback)
            result["divergence"] = any(r["currency"] == instrument and r.get("flag") == "divergence" for r in divergence["currencies"])
        return result
    if panel == "extremes": return await v2.extremes(params.get("ccy","JPY"))
    if panel == "retail":
        result = await pos.get_institutions_vs_retail(lookback)
        result["state"] = "available" if result.get("retail_state") == "ok" else "unavailable"
        if result["state"] == "unavailable": result["message"] = retail(result)
        return result
    data,spots = await pos.get_flows(params.get("window","1W")),await pos.load_spot()
    rows = []
    for item in data["currencies"]:
        g,c = item["leveraged_funds"],item["currency"]
        day = date.fromisoformat(item["report_date"]) if item.get("report_date") else None
        rate = spots[c].on(day) if c in spots and day else None
        change,net = g.get("change"),g.get("net")
        rows.append({"currency":c,"net":net,"change_usd":change*specs()["contracts"][c]["size"]*rate if change is not None and rate is not None else None,
                     "action":v2.bet_change(net,net-change) if net is not None and change is not None else "unavailable"})
    return {"state":"available","rows":rows,"window":data["window"]}
