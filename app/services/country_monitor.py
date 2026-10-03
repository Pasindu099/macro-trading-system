"""Country comparison data for desks with configured members."""

from __future__ import annotations

from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.services.yield_spreads import get_spread


def relative_tone(value: float | None, ez_value: float | None, *, inverted: bool = False,
                  inflation: bool = False) -> str:
    if value is None or ez_value is None:
        return "unavailable"
    delta = value - ez_value
    if abs(delta) < 1e-9:
        return "neutral"
    if inflation:
        return "hotter" if delta > 0 else "cooler"
    return "weaker" if (delta > 0 if inverted else delta < 0) else "stronger"


async def get_country_monitor(desk: dict) -> dict:
    members = desk.get("members", [])
    if not members:
        return {"status": "unavailable", "reason": "No country monitor members configured"}
    country_codes = [member["country"] for member in members]
    canonicals = list({name for item in desk.get("key_data", []) for name in item["series"].values() if name})
    async with get_sessionmaker()() as session:
        result = await session.execute(text("""
            SELECT DISTINCT ON (i.country_code, i.canonical_name)
                   i.country_code, i.canonical_name, r.actual::float AS value,
                   r.released_at::date AS release_date
            FROM indicators i JOIN indicator_releases r ON r.indicator_id = i.id
            WHERE i.country_code = ANY(:countries) AND i.canonical_name = ANY(:canonicals)
              AND r.is_latest AND r.actual IS NOT NULL
            ORDER BY i.country_code, i.canonical_name, r.released_at DESC, r.id DESC
        """), {"countries": country_codes, "canonicals": canonicals})
        latest = {(r.country_code, r.canonical_name): (r.value, r.release_date) for r in result}
        fiscal = await session.execute(text("""
            SELECT DISTINCT ON (country_code) country_code, year,
                   balance_pct_gdp::float AS balance_pct_gdp
            FROM country_fiscal_observations WHERE country_code = ANY(:countries)
            ORDER BY country_code, year DESC
        """), {"countries": [m["code"] for m in members]})
        balances = {r.country_code: {"year": r.year, "deficit_pct_gdp": -r.balance_pct_gdp}
                    for r in fiscal}
    data = []
    ez = next((m for m in members if m["code"] == "EZ"), None)
    for item in desk.get("key_data", []):
        ez_name = item["series"].get("EZ")
        ez_value = latest.get((ez["country"], ez_name), (None, None))[0] if ez and ez_name else None
        values = {}
        for member in members:
            canonical = item["series"].get(member["code"])
            value, release_date = latest.get((member["country"], canonical), (None, None)) if canonical else (None, None)
            values[member["code"]] = {
                "canonical": canonical, "value": value, "release_date": release_date,
                "tone": relative_tone(value, ez_value, inverted=item["id"] == "unemployment",
                                      inflation=item["id"] in {"hicp", "core_hicp"}),
            }
        data.append({"id": item["id"], "label": item["label"], "values": values})
    spread = await get_spread("FR-DE", "10Y", days=1)
    last = spread.get("rows", [])[-1] if spread.get("status") == "available" else None
    return {"status": "available", "members": [m["code"] for m in members], "indicators": data,
            "budget_balance": balances, "oat_bund_10y": {
                "status": spread["status"], "reason": spread.get("reason"),
                "spread_bp": last["spread_bp"] if last else None,
                "as_of": last["obs_date"] if last else None,
            }}
