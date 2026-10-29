"""Official CFTC Legacy futures-only non-commercials; never merged into TFF."""
import asyncio
from functools import lru_cache
from datetime import date, timedelta
from pathlib import Path

import httpx
import yaml
from sqlalchemy import text

from app.db.session import session_scope
from app.ingestion.run_logger import run_logger
from app.services.cot_positions import contracts_by_code, expected_report_date, load_cot_config
from app.services.job_lock import job_lock
from app.services.source_identity import headers


@lru_cache(maxsize=1)
def specs():
    return yaml.safe_load(Path("config/cot_contract_specs.yaml").read_text(encoding="utf-8"))


def parse_rows(raw_rows: list[dict], codes: dict[str, str]) -> list[dict]:
    result = []
    for row in raw_rows:
        code = str(row["cftc_contract_market_code"]).strip()
        if code not in codes:
            continue
        def count(name):
            value = row.get(name)
            if value is None:
                raise ValueError(f"Missing Legacy field: {name}")
            return int(str(value).replace(",", ""))
        result.append({"report_date": date.fromisoformat(row["report_date_as_yyyy_mm_dd"][:10]),
            "contract_code": code, "currency": codes[code], "long": count("noncomm_positions_long_all"),
            "short": count("noncomm_positions_short_all"), "spreading": count("noncomm_postions_spread_all"),
            "open_interest": count("open_interest_all")})
    return result


async def upsert(session, rows: list[dict]) -> int:
    written = 0
    sql = text('''INSERT INTO cot_legacy_positions (report_type,report_date,contract_code,currency,"long","short",spreading,open_interest)
        VALUES ('legacy_futures_only',:report_date,:contract_code,:currency,:long,:short,:spreading,:open_interest)
        ON CONFLICT (report_type,report_date,contract_code) DO UPDATE SET
            "long"=EXCLUDED."long", "short"=EXCLUDED."short", spreading=EXCLUDED.spreading,
            open_interest=EXCLUDED.open_interest, ingested_at=now()
        WHERE (cot_legacy_positions."long",cot_legacy_positions."short",cot_legacy_positions.spreading,cot_legacy_positions.open_interest)
            IS DISTINCT FROM (EXCLUDED."long",EXCLUDED."short",EXCLUDED.spreading,EXCLUDED.open_interest)''')
    for row in rows:
        written += (await session.execute(sql, row)).rowcount
    return written


async def ingest(start: date = date(2010, 1, 1), end: date | None = None) -> dict:
    end = end or date.today()
    if start > end:
        raise ValueError("Start exceeds end")
    codes = contracts_by_code(load_cot_config())
    where_codes = ",".join("'" + code + "'" for code in codes)
    params = {"$where": f"report_date_as_yyyy_mm_dd >= '{start.isoformat()}T00:00:00' AND report_date_as_yyyy_mm_dd <= '{end.isoformat()}T23:59:59' AND cftc_contract_market_code in ({where_codes})",
        "$select": "report_date_as_yyyy_mm_dd,cftc_contract_market_code,open_interest_all,noncomm_positions_long_all,noncomm_positions_short_all,noncomm_postions_spread_all",
        "$order": "report_date_as_yyyy_mm_dd,cftc_contract_market_code", "$limit": 5000}
    seen = written = requests = offset = 0
    async with httpx.AsyncClient(timeout=120, headers=headers(), follow_redirects=True) as client:
        while True:
            response = await client.get(specs()["legacy"]["url"], params={**params, "$offset": offset})
            response.raise_for_status()
            raw = response.json()
            rows = parse_rows(raw, codes)
            async with session_scope(statement_timeout="5min") as session:
                written += await upsert(session, rows)
            seen += len(rows)
            requests += 1
            if len(raw) < 5000:
                break
            offset += len(raw)
    return {"rows_seen": seen, "written": written, "requests": requests}


async def run_cot_legacy_weekly(*, retry: bool = False, today: date | None = None):
    today = today or date.today()
    expected = expected_report_date(today)
    async with job_lock("cot_legacy_weekly") as acquired:
        async with run_logger("job:cot_legacy_weekly") as run:
            if not acquired:
                run.mark_skipped()
                return {"status": "skipped"}
            async with session_scope() as session:
                latest = (await session.execute(text("SELECT max(report_date) FROM cot_legacy_positions"))).scalar_one()
            if retry and latest and latest >= expected:
                run.mark_skipped()
                return {"status": "skipped"}
            result = await asyncio.wait_for(ingest((latest or expected) - timedelta(days=14), today), 10*60)
            run.record_rows(result["written"])
            if not result["rows_seen"]:
                run.errors.append("No Legacy reports returned for the requested interval")
            return result
