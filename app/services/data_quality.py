"""Read-only source health and a dated, thirty-day data-quality inventory."""
from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text

from app.log_redaction import redact
from app.services import freshness, job_status, news_collector, situations
from app.services.situation_rules import condition, load_config

SOURCE_JOBS = {
    "fx_spot": ("job:fx_spot",), "government_yields": ("job:government_yields_incremental",),
    "fred_daily": ("job:fred_series",), "calendar": ("scheduled_asia", "scheduled_london", "scheduled_ny"),
    "news": ("job:news_collect:investinglive",), "cot": ("job:cot_weekly",),
    "myfxbook_retail": ("job:myfxbook_retail",), **freshness.DERIVED_JOBS,
}
SCHEDULE_IDS = {"fred_daily": ("fred_series_daily",), "calendar": ("scheduled_asia", "scheduled_london", "scheduled_ny", "calendar_fast_actuals"),
                "fx_spot": ("fx_spot", "scheduled_asia", "scheduled_london", "scheduled_ny"), "cot": ("cot_weekly", "cot_weekly_retry"),
                "news": ("news_collect_investinglive",), "macro_state": ("macro_state_daily",),
                "dominance": ("dominance_daily",), "verdicts": ("macro_state_daily", "situations_daily")}


def safe_error(value) -> str | None:
    if not value:
        return None

    def clean(item):
        if isinstance(item, dict):
            return {key: "***" if re.search(r"password|token|secret|authorization|api.?key|session", str(key), re.I)
                    else clean(val) for key, val in item.items()}
        if isinstance(item, list):
            return [clean(val) for val in item]
        return redact(re.sub(r"(://)[^/\s@]+@", r"\1***@", str(item)))

    return redact(json.dumps(clean(value), ensure_ascii=True))[:2000]


def health_status(stale: bool, run_status: str | None, *, enabled: bool = True, manual: bool = False) -> str:
    if not enabled or run_status == "disabled":
        return "disabled"
    if manual or run_status == "manual_import":
        return "manual import"
    if run_status in {"failed", "error", "timeout", "partial"}:
        return "error"
    return "stale" if stale else "fresh"


async def source_health(session, *, scheduler=None, now: datetime | None = None) -> dict:
    """Pass app.state.scheduler to read live next-run times; no scheduler is started here."""
    statuses = await freshness.get_data_freshness(session, now=now)
    jobs = {str(row["run_type"]): row for row in await job_status.get_job_status(session)}
    collectors = await job_status.get_source_status(session)
    rates = (await session.execute(text("""
        SELECT errors, next_scheduled_run FROM government_yield_ingestion_status
        WHERE job_name='government_yields_incremental'
    """))).mappings().first()
    manual = await freshness.manual_import_rows(session)
    engine = getattr(scheduler, "_scheduler", scheduler)
    schedules = {job.id: getattr(job, "next_run_time", None) for job in engine.get_jobs()} if engine else {}
    rows = []
    used = set()
    aliases = {"calendar": "eodhd_calendar_sessions", "news": "investinglive_collection"}
    for key, item in statuses.items():
        names = SOURCE_JOBS.get(key, ())
        candidates = [jobs[name] for name in names if name in jobs]
        used.update(names)
        job = max(candidates, key=lambda r: (r.get("last_run") or {}).get("started_at") or datetime.min.replace(tzinfo=UTC), default={})
        collector = collectors.get(aliases.get(key, key), {})
        run = collector.get("last_run") or job.get("last_run") or {}
        ids = SCHEDULE_IDS.get(key, (key, key + "_daily", key + "_incremental"))
        upcoming = [schedules[name] for name in ids if schedules.get(name)]
        if key == "government_yields" and rates and rates["next_scheduled_run"]:
            upcoming.append(rates["next_scheduled_run"])
        rows.append({"source": key, "label": item["label"],
                     "last_update": item.get("updated_at") or job.get("last_success") or run.get("finished_at"),
                     "newest_data_date": item.get("as_of"),
                     "status": health_status(item["stale"], run.get("status")),
                     "last_error": safe_error(rates["errors"] if key == "government_yields" and rates else job.get("last_error")),
                     "next_scheduled_run": min(upcoming) if upcoming else None})
    for key, job in jobs.items():
        if key in used:
            continue
        run = job.get("last_run") or {}
        stamp = job.get("last_success")
        rows.append({"source": key, "label": key.removeprefix("job:"), "last_update": stamp,
                     "newest_data_date": None,
                     "status": health_status(not stamp or (now or datetime.now(UTC)) - stamp > timedelta(days=2), run.get("status")),
                     "last_error": safe_error(job.get("last_error")),
                     "next_scheduled_run": schedules.get(key.removeprefix("job:"))})
    for item in manual:
        rows.append({"source": item["source"], "label": item["label"], "last_update": item.get("as_of"),
                     "newest_data_date": item.get("as_of"), "status": "manual import", "last_error": None,
                     "next_scheduled_run": None})
    checks = {r.source_name: r for r in (await session.execute(text("SELECT * FROM source_health"))).all()}
    for key, spec in news_collector.load_config()["sources"].items():
        feeds = [row for name, row in checks.items() if name.startswith(key + ":")]
        last = max((r.last_checked_at for r in feeds), default=None)
        newest = max((r.last_item_at for r in feeds if r.last_item_at), default=None)
        stale = not newest or (now or datetime.now(UTC)) - newest > timedelta(hours=6)
        rows.append({"source": "news:" + key, "label": spec["label"], "last_update": last,
                     "newest_data_date": newest,
                     "status": health_status(stale, "failed" if any(not r.is_healthy for r in feeds) else None,
                                             enabled=bool(spec.get("enabled"))),
                     "last_error": "Collector reported unhealthy" if any(not r.is_healthy for r in feeds) else None,
                     "next_scheduled_run": schedules.get("news_collect_" + key) if spec.get("enabled") else None})
    counts = {status: sum(r["status"] == status for r in rows)
              for status in ("fresh", "stale", "manual import", "disabled", "error")}
    return {"rows": rows, "counts": counts}


def missing_business_days(first: date, present: set[date], since: date, today: date, market: str) -> list[date]:
    day = max(first, since)
    missing = []
    while day <= today:
        if day.weekday() < 5 and day not in freshness.market_holidays(day.year, market) and day not in present:
            missing.append(day)
        day += timedelta(days=1)
    return missing


async def unavailable_situations(today: date) -> list[dict]:
    cfg = load_config()
    history = await situations.load_history()
    pairs = sorted(pair for pair in history["fx"] if (pair, "2Y") in history["spreads"])
    rows = []
    for key, spec in cfg["situations"].items():
        for scope in situations.scopes(spec, pairs):
            evidence = situations.features(key, scope, today, history, tuple(cfg["intervention_zone_jpy"]))
            if condition(spec["trigger"], evidence) is None:
                rows.append({"kind": "situation", "series": key, "scope": scope, "date": today,
                             "missing_inputs": [name for name, value in evidence.items() if value is None]})
    return rows


async def quality_log(session, *, today: date | None = None) -> dict:
    today = today or datetime.now(UTC).date()
    since = today - timedelta(days=29)
    params = {"since": since, "today": today}
    outliers, gaps = [], []
    for table, column, market in (("fx_spot_observations", "observation_date", "'FX'"),
                                   ("government_yield_observations", "market_observation_date", "country_code"),
                                   ("fred_observations", "observation_date", "'US'")):
        symbol = "series_id" if table == "fred_observations" else "provider_symbol"
        if table != "fred_observations":
            flagged = (await session.execute(text(f"""
                SELECT {symbol} AS series, {column} AS date FROM {table}
                WHERE is_outlier AND {column} BETWEEN :since AND :today ORDER BY {column} DESC
            """), params)).mappings().all()
            outliers.extend({"source": table, **dict(row)} for row in flagged)
        valid = "true" if table == "fred_observations" else "quality_status='valid' AND NOT is_outlier"
        coverage = (await session.execute(text(f"""
            SELECT {symbol} AS series, {market} AS market, min({column}) AS first,
                   array_agg(DISTINCT {column}) FILTER (WHERE {column} >= :since) AS days
            FROM {table} WHERE {column} <= :today AND {valid} GROUP BY 1, 2
        """), params)).mappings().all()
        for row in coverage:
            missing = missing_business_days(row["first"], set(row["days"] or []), since, today, row["market"])
            if missing:
                gaps.append({"source": table, "series": row["series"], "count": len(missing), "dates": missing})
    revisions = (await session.execute(text("""
        WITH chain AS (
            SELECT i.country_code, r.actual, r.retrieved_at,
                   lag(r.actual) OVER (PARTITION BY r.indicator_id,
                       COALESCE(r.period_start_date::text,r.period,r.released_at::date::text)
                       ORDER BY r.retrieved_at,r.id) AS prior
            FROM indicator_releases r JOIN indicators i ON i.id=r.indicator_id
            WHERE r.actual IS NOT NULL AND r.released_at <= now()
        ) SELECT country_code, count(*) AS count FROM chain
        WHERE retrieved_at::date BETWEEN :since AND :today AND prior IS NOT NULL AND actual IS DISTINCT FROM prior
        GROUP BY country_code ORDER BY country_code
    """), params)).mappings().all()
    unmapped = (await session.execute(text("""
        SELECT COALESCE(raw_payload->>'type', raw_payload->>'event', 'Unknown') AS name, count(*) AS count
        FROM indicator_releases WHERE indicator_id IS NULL AND retrieved_at::date BETWEEN :since AND :today
        GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 20
    """), params)).mappings().all()
    parser = (await session.execute(text("""
        SELECT count(*) AS total, count(a.news_item_id) AS processed,
               count(*) FILTER (WHERE a.status IN ('unmatched','newswire','confirmed','discrepancy')) AS parsed
        FROM news_items n LEFT JOIN newswire_actuals a ON a.news_item_id=n.id
        WHERE n.source='investinglive' AND n.news_type='Data' AND n.published_at::date BETWEEN :since AND :today
    """), params)).mappings().one()
    models = (await session.execute(text("""
        SELECT bank AS series, data_date AS date, status, missing_inputs FROM policy_model_daily
        WHERE data_date BETWEEN :since AND :today AND status='unavailable' ORDER BY data_date DESC, bank
    """), params)).mappings().all()
    unavailable = [{"kind": "model", **dict(row)} for row in models]
    unavailable.extend(await unavailable_situations(today))
    return {"since": since, "through": today, "outliers": outliers, "gaps": gaps,
            "revisions": [dict(row) for row in revisions], "unmapped_events": [dict(row) for row in unmapped],
            "headline_parser": {**dict(parser), "coverage": parser["parsed"] / parser["total"] if parser["total"] else None},
            "unavailable_inputs": unavailable}
