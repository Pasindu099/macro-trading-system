"""Read model for the G10 pair screener."""

from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.services import freshness, overview, positioning, rates_drivers, situations, yield_spreads

CONFIG_PATH = Path("config/pairs.yaml")
DEFAULT_SORT = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["default_sort"]


def pair_config() -> list[dict]:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["pairs"]


def sort_rows(rows: list[dict], column: str = DEFAULT_SORT, descending: bool = True) -> list[dict]:
    columns = {"pair", "last", "week_change", "month_change", "overall_score", "policy_gap", "cb_bias",
               "data_implied", "priced", "spread_2y", "spread_change_20d", "driver",
               "cot_implied", "retail_long_pct", "warnings"}
    column = column if column in columns else DEFAULT_SORT
    def value(row: dict):
        item = row.get(column)
        if column == "overall_score" and item is not None:
            item = abs(item)
        if column == "warnings":
            item = len(item or [])
        if isinstance(item, (int, float)):
            return -item if descending else item
        return str(item).lower() if not descending else tuple(-ord(c) for c in str(item).lower())
    return sorted(rows, key=lambda row: (row.get(column) is None, value(row)))


def filter_rows(rows: list[dict], group: str = "all", search: str = "") -> list[dict]:
    search = search.upper().replace("/", "").strip()
    return [row for row in rows if overview._pair_in_group(row["pair"], group)
            and search in row["pair"].replace("/", "")]


async def _spot_history() -> dict[str, list[tuple]]:
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text("""
            SELECT DISTINCT ON (pair, observation_date) pair, observation_date, close_value::float AS close
            FROM fx_spot_observations
            WHERE quality_status = 'valid' AND NOT is_outlier AND observation_date <= current_date
            ORDER BY pair, observation_date DESC, ingested_at DESC, id DESC
        """))).mappings().all()
    result: dict[str, list[tuple]] = {}
    for row in rows:
        result.setdefault(row["pair"], []).append((row["observation_date"], row["close"]))
    return result


async def screener() -> dict:
    config = pair_config()
    ranking = await overview.currency_ranking()
    currency = {row["currency"]: row for row in ranking["rows"]}
    from app.services import policy_model
    models = {ccy: await policy_model.latest(bank) for ccy, bank in
              {"EUR": "ECB", "GBP": "BOE", "AUD": "RBA", "NZD": "RBNZ", "USD": "FED",
               "CAD": "BOC", "CHF": "SNB", "JPY": "BOJ"}.items()}
    retail = {row["pair"]: row for row in (await positioning.get_institutions_vs_retail())["pairs"]}
    from app.services import myfxbook
    retail_snapshots = {row["pair"]: row for row in await myfxbook.latest()}
    episodes = await situations.get_situation_episodes(active=True)
    spots = await _spot_history()
    from app.services.positioning_values import pair_value, money
    cot_positions = await positioning.load_positions()
    cot_spots = await positioning.load_spot()
    async with get_sessionmaker()() as session:
        source_freshness = await freshness.get_data_freshness(session)
    rows = []
    for item in config:
        pair, base, quote = item["pair"], item["base"], item["quote"]
        history = spots.get(pair, [])
        last = history[0][1] if history else None
        def change(days: int):
            previous = next((value for day, value in history[1:]
                             if (history[0][0] - day).days >= days), None) if history else None
            return round(100 * (last / previous - 1), 2) if last is not None and previous else None
        base_view, quote_view = currency.get(base, {}), currency.get(quote, {})
        a, b = base_view.get("score"), quote_view.get("score")
        score = round(a - b, 2) if a is not None and b is not None else None
        base_model, quote_model = models[base], models[quote]
        numeric_gap = all(view.get("status") == "available" and all(
            isinstance(view.get(key), (int, float)) for key in ("model_rate", "market_rate_3m", "gap_bp"))
            for view in (base_model, quote_model))
        gap = round(base_model["gap_bp"] - quote_model["gap_bp"], 1) if numeric_gap else None
        data_implied = round((base_model["model_rate"] - quote_model["model_rate"]) * 100, 1) if numeric_gap else None
        priced = round((base_model["market_rate_3m"] - quote_model["market_rate_3m"]) * 100, 1) if numeric_gap else None
        spread = await yield_spreads.get_spread(pair, "2Y", 21)
        spread_rows = spread.get("rows", [])
        spread_now = spread_rows[-1]["spread_bp"] if spread_rows else None
        spread_delta = round(spread_now - spread_rows[0]["spread_bp"], 1) if len(spread_rows) >= 21 else None
        driver = await rates_drivers.get_pair_drivers(pair, "2Y")
        position = retail.get(pair, {})
        legs = [ccy for ccy in (base,quote) if ccy != "USD"]
        common_dates = set.intersection(*[{w.report_date for w in cot_positions.get(ccy,{}).get("leveraged_funds",[])} for ccy in legs])
        cot_day = max(common_dates, default=None)
        cot_usd = pair_value(base,quote,cot_day,cot_positions,cot_spots) if cot_day else None
        snap = retail_snapshots.get(base + quote, {})
        warnings = [{"name": episode["name"], "severity": episode["severity"]}
                    for episode in episodes if episode["scope_key"] in (base, quote, pair, "GLOBAL")]
        rows.append({"pair": pair, "href": "/pairs/" + base + quote, "pip_size": item["pip_size"],
                     "last": last, "week_change": change(7), "month_change": change(30),
                     "overall_score": score, "partial": bool(base_view.get("partial") or quote_view.get("partial")),
                     "policy_gap": gap, "data_implied": data_implied, "priced": priced,
                     "cb_bias": (f"{base}: {base_view.get('gap', 'pending')} · {quote}: {quote_view.get('gap', 'pending')}"
                                 if base_view.get("gap") != "pending" or quote_view.get("gap") != "pending" else None),
                     "gap_tooltip": "; ".join(f"{ccy}: {view['gap']}" for ccy, view in
                                               ((base, base_view), (quote, quote_view)) if view.get("gap") != "pending"),
                     "spread_2y": spread_now, "spread_change_20d": spread_delta,
                     "driver": driver.get("status") if driver.get("status") != "unavailable" else None,
                     "driver_as_of": driver.get("as_of"), "cot_implied": position.get("cot_implied"),
                     "retail_long_pct": position.get("retail_long_pct"),
                     "cot_usd_bn": cot_usd/1e9 if cot_usd is not None else None, "cot_money": money(cot_usd),
                     "cot_notional_as_of": str(cot_day) if cot_day else None,
                     "cot_notional_basis": f"implied from {base} and {quote} futures" if "USD" not in (base,quote) else "single futures market; pair direction",
                     "retail_long_positions": snap.get("long_positions"),
                     "retail_short_positions": snap.get("short_positions"),
                     "opposite_sides": position.get("signal") == "opposite sides", "warnings": warnings})
    return {"rows": rows, "freshness": source_freshness, "default_sort": DEFAULT_SORT}
