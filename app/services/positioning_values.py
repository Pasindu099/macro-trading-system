"""Report-date dollar values and comparable single-market open-interest views."""
from datetime import date, timedelta

from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.services import positioning as pos
from app.services.cot_legacy import specs

GROUPS = ("leveraged_funds", "asset_manager", "legacy_noncommercial")
GROUP_LABELS = {"leveraged_funds": "Hedge funds", "asset_manager": "Long-term investors",
                "legacy_noncommercial": "Large speculators (as on most sites)"}
RANGES = {"1Y": 52, "3Y": 156, "5Y": 260, "2010": None}


def money(value):
    return "unavailable" if value is None else f"${abs(value)/1e9:.2f}bn {'long' if value > 0 else 'short' if value < 0 else 'flat'}"


def notional(currency: str, week: pos.Week, spot: pos.SpotSeries | None):
    rate = spot.on(week.report_date) if spot else None
    size = specs()["contracts"][currency]["size"]
    return None if rate is None else week.net * size * rate


def pair_value(base, quote, date_, positions, spots, group="leveraged_funds"):
    legs = [ccy for ccy in (base, quote) if ccy != "USD"]
    values = {}
    for ccy in legs:
        week = next((w for w in positions.get(ccy, {}).get(group, []) if w.report_date == date_), None)
        values[ccy] = notional(ccy, week, spots.get(ccy)) if week else None
    if any(value is None for value in values.values()):
        return None
    # USD pairs use ONE futures market; DX is context, never subtracted.
    return -values[quote] if base == "USD" else values[base] if quote == "USD" else values[base]-values[quote]


async def load_group_positions(group: str, currencies=None):
    if group != "legacy_noncommercial":
        return await pos.load_positions(currencies)
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text('''SELECT currency,report_date,"long","short",open_interest
            FROM cot_legacy_positions WHERE report_type='legacy_futures_only' AND report_date <= current_date
            AND (CAST(:currencies AS text[]) IS NULL OR currency=ANY(:currencies)) ORDER BY currency,report_date'''),
            {"currencies": currencies})).mappings().all()
    result = {}
    for row in rows:
        result.setdefault(row["currency"], {}).setdefault(group, []).append(
            pos.Week(row["report_date"], row["long"], row["short"], row["open_interest"]))
    return result


async def pair_series(pair: str, weeks: int = 156, *, positions=None, spots=None):
    base, quote = pos.normalize_pair(pair)
    if not 1 <= weeks <= 1000:
        raise ValueError("weeks must be between 1 and 1000")
    positions = positions if positions is not None else await pos.load_positions([base, quote])
    spots = spots if spots is not None else await pos.load_spot()
    legs = [ccy for ccy in (base, quote) if ccy != "USD"]
    market = await load_pair_spot(f"{base}/{quote}") if "USD" not in (base,quote) else None
    output = {}
    for group in ("leveraged_funds", "asset_manager"):
        dates = sorted(day for day in set.intersection(*[{w.report_date for w in positions.get(ccy, {}).get(group, [])} for ccy in legs]) if date(2010,1,1) <= day <= date.today())
        rows = []
        for day in dates[-weeks:]:
            value = pair_value(base, quote, day, positions, spots, group)
            price = market.on(day) if market is not None else pair_price(base, quote, day, spots)
            rows.append({"report_date": day.isoformat(), "net_usd_bn": value/1e9 if value is not None else None, "price": price})
        output[group] = {"label": GROUP_LABELS[group], "history": rows}
    return {"pair": f"{base}/{quote}", "weeks": weeks, "groups": output,
            "implied": "USD" not in (base, quote),
            "label": f"implied from {base} and {quote} futures" if "USD" not in (base, quote) else f"{legs[0]} futures, pair direction",
            "broad_dollar_context": "DX is separate broad dollar context; it is not subtracted from USD pairs"}


def pair_price(base, quote, day, spots):
    a = 1 if base == "USD" else spots[base].on(day) if base in spots else None
    b = 1 if quote == "USD" else spots[quote].on(day) if quote in spots else None
    return a/b if a is not None and b else None


async def load_pair_spot(pair):
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text("""SELECT DISTINCT ON (observation_date) observation_date,close_value::float AS value
            FROM fx_spot_observations WHERE pair=:pair AND quality_status='valid' AND NOT is_outlier
            AND observation_date <= current_date ORDER BY observation_date,ingested_at DESC,id DESC"""), {"pair": pair})).all()
    return pos.SpotSeries([(r.observation_date,r.value) for r in rows])


def quantile(values, fraction):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    index = (len(values)-1)*fraction
    low = int(index)
    return values[low] + (values[min(low+1,len(values)-1)]-values[low])*(index-low)


async def history(instrument: str, *, group="leveraged_funds", units="usd", range_key="3Y"):
    if group not in GROUPS or units not in {"usd", "pct_oi"} or range_key not in RANGES:
        raise ValueError("Invalid positioning history option")
    single = instrument.upper() in pos.CURRENCIES
    base, quote = (instrument.upper(), "USD") if single else pos.normalize_pair(instrument)
    leg = base if quote == "USD" else quote if base == "USD" else None
    cross = not single and leg is None
    market = await load_pair_spot(f"{base}/{quote}") if cross else None
    positions = await load_group_positions(group, [base, quote])
    spots = await pos.load_spot()
    effective_units = "usd" if cross else units
    legs = [leg] if leg else [base, quote]
    if single and base == "USD":
        legs = ["USD"]
    dates = sorted(set.intersection(*[{w.report_date for w in positions.get(ccy, {}).get(group, [])} for ccy in legs]))
    dates = [day for day in dates if day >= date(2010,1,1)]
    if RANGES[range_key]:
        dates = dates[-RANGES[range_key]:]
    rows = []
    flipped = not single and base == "USD"
    for day in dates:
        if cross:
            dollars = pair_value(base, quote, day, positions, spots, group)
            row = {"report_date": day.isoformat(), "net": dollars/1e9 if dollars is not None else None,
                   "long": None, "short": None, "open_interest": None, "net_pct_oi": None}
        else:
            ccy = legs[0]
            week = next(w for w in positions[ccy][group] if w.report_date == day)
            long, short = (week.short, week.long) if flipped else (week.long, week.short)
            price = spots[ccy].on(day) if ccy in spots else None
            factor = 100/week.open_interest if effective_units == "pct_oi" and week.open_interest else (
                specs()["contracts"][ccy]["size"]*price/1e9 if effective_units == "usd" and price is not None else None)
            row = {"report_date": day.isoformat(), "net": (long-short)*factor if factor is not None else None,
                   "long": long*factor if factor is not None else None, "short": short*factor if factor is not None else None,
                   "open_interest": week.open_interest, "net_pct_oi": (long-short)/week.open_interest*100 if week.open_interest else None}
        row["price"] = market.on(day) if cross else spots[base].on(day) if single and base in spots else pair_price(base, quote, day, spots)
        rows.append(row)
    low, high = quantile([r["net"] for r in rows], .2), quantile([r["net"] for r in rows], .8)
    for row in rows:
        row.update({"p20": low, "p80": high})
    net = rows[-1]["net"] if rows else None
    band = "unavailable" if net is None else "normal range" if low == high else "bottom 20%" if low is not None and net <= low else "top 20%" if high is not None and net >= high else "normal range"
    amount = f"{abs(net):.1f}% of all open contracts" if effective_units == "pct_oi" and net is not None else f"${abs(net):.2f}bn" if net is not None else "unavailable"
    current = (f"{GROUP_LABELS[group]} are net {'long' if net > 0 else 'short' if net < 0 else 'flat'} {instrument.upper()} by {amount}. "
               f"This sits in the {band} over the selected range.") if net is not None else "Positioning or matching report-date prices are unavailable."
    stale = bool(dates and (date.today()-dates[-1]).days > 10)
    if stale:
        current = f"Stored readings are stale as of {dates[-1]}; a current reading is unavailable."
    return {"instrument": instrument.upper(), "group": group, "group_label": GROUP_LABELS[group], "units": effective_units,
            "range": range_key, "history": rows, "state": "stale" if stale else "available" if net is not None else "unavailable", "reading": current,
            "stale": stale, "report_date": str(dates[-1]) if dates else None,
            "report_type": "legacy_futures_only" if group == "legacy_noncommercial" else "tff_futures_only",
            "percentile_note": ("Traders rarely held a larger long bet in this range." if net is not None and net > 0 and band == "top 20%" else
                "Traders rarely held a larger short bet in this range." if net is not None and net < 0 and band == "bottom 20%" else
                "The bet has rarely been this small in this range." if net is not None and ((net > 0 and band == "bottom 20%") or (net < 0 and band == "top 20%")) else None),
            "explanation": "Crosses combine two futures markets, so a single open-interest percentage is unavailable; showing US dollars instead." if cross and units == "pct_oi" else None,
            "report_note": "Many sites show Legacy non-commercials; this report keeps them separate from TFF hedge funds and asset managers."}
