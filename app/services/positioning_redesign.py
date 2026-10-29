"""Step 18 live page contexts; calculations stay in the Positioning services."""
from datetime import date
from app.services import positioning as pos, positioning_page as legacy, positioning_values as values
from app.services.takeaways.positioning import inputs

LOOKBACK_LABELS = {"52w":"52 weeks","3y":"3 years","5y":"5 years"}
SOURCES = legacy.SOURCES
TITLES = {"summary":"Positioning","crowding":"Where are the big traders betting?",
    "flows":"Who is adding to or cutting their bets?","squeeze":"Squeeze watch: who might be forced out?",
    "price":"Who is betting, and how much?","extremes":"What happened the last times they were this extreme?",
    "retail":"Institutions vs retail"}


async def _context(panel, params):
    data = await inputs(panel,params)
    ctx = {"state":"ok","data":data,"takeaway_data":data}
    if panel == "summary":
        metadata = await legacy.summary(params.get("lookback","3y"))
        ctx.update(metadata)
    elif panel == "crowding":
        for row in data["bars"]:
            for group in row["groups"].values():
                if group:
                    p = group.get("percentile")
                    group.update({"left":min(50,p) if p is not None else 50,"width":abs(p-50) if p is not None else 0})
    elif panel == "flows":
        rows = [r for r in data["rows"] if r["change_usd"] is not None]
        peak = max([abs(r["change_usd"]) for r in rows] or [1]) or 1
        for row in rows:
            row.update({"left":50 if row["change_usd"] >= 0 else 50-abs(row["change_usd"])/peak*50,
                        "width":abs(row["change_usd"])/peak*50})
        data["rows"] = sorted(rows,key=lambda r:r["change_usd"],reverse=True)
    elif panel == "price":
        groups = [{"name":data["group_label"],"rows":data["history"]}]
        if data["units"] == "usd" and params.get("group","leveraged_funds") == "leveraged_funds":
            slow = await values.history(params.get("instrument",params.get("ccy","JPY")),group="asset_manager",
                                        units="usd",range_key=params.get("range_key","3Y"))
            groups.append({"name":"Long-term investors","rows":slow["history"]})
        ctx["chart"] = {"units":data["units"],"groups":groups,"rows":data["history"]}
        ctx["no_history"] = not any(r["net"] is not None for r in data["history"])
    elif panel == "extremes":
        ctx["chart"] = data.get("episodes",[])
    return ctx


async def summary(**params): return await _context("summary",params)
async def crowding(**params): return await _context("crowding",params)
async def flows(**params): return await _context("flows",params)
async def squeeze(**params): return await _context("squeeze",params)
async def price(**params): return await _context("price",params)
async def extremes(**params): return await _context("extremes",params)
async def retail(**params): return await _context("retail",params)

PANELS = {"summary":summary,"crowding":crowding,"flows":flows,"squeeze":squeeze,"price":price,"extremes":extremes,"retail":retail}
