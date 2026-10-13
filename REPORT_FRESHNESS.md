# Freshness Part A — 13 Oct 2026 (UTC)
| Source | Newest row | Running job; `/api/admin/jobs/status` last run/error |
| --- | --- | --- |
| FX spot | 12 Oct | Yield job runs FX; source status absent from endpoint; fetch success, 205 rows on 12 Oct. |
| Government yields | 12 Oct | `government_yields_incremental` registered; source status absent from endpoint; catch-up success. |
| FRED daily | 9 Oct | `fred_series_daily` registered; `job:fred_series` success 12 Oct, no error. |
| EODHD calendar | 13 Oct | Three daily sessions registered; status absent from endpoint; `scheduled_ny` success 12 Oct. |
| COT | 6 Oct | Friday/Monday jobs registered; `job:cot_weekly` success 12 Oct after ZIP 403 fallback to [CFTC Data Hub](https://publicreporting.cftc.gov/resource/gpe5-46if.json). |
| investingLive raw news | 12 Oct 18:32 | `collect_investinglive` registered in news pipeline; status absent from endpoint; no stored error. |
- Cause: missed in-memory schedules during downtime; startup catch-up and hourly watchdog added; Overview news source key corrected. Derived catch-up succeeded 12 Oct: rates → event innovation → Macro State (latest 9 Oct) → situations → dominance (latest complete 11 Oct).

## Part B — guard
- `config/freshness.yaml` defines all eight limits; `app/services/freshness.py` calculates weekday and calendar ages. Live 13 Oct check: FRED daily 9 Oct and Macro State 9 Oct are stale; other six sources are within limits.
- All pages load the global source strip; every Overview and desk panel renders source-specific as-of dates and visible stale reasons. Situation episodes show evidence-stale dates; stale inputs skip episode replacement. `/api/admin/jobs/status` now includes `data_freshness`.
