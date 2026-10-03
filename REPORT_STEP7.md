# Step 7 — Pipeline health, indicators, rate-probability methodology

## Part A: pipeline health (local, 2026-10-03)

| Pipeline | Last success / newest row | Verdict |
| --- | --- | --- |
| CB policy docs (scraper → ingester → analyzer) | 0 rows | **Never run**: not scheduled; manual admin routes only (`public.py:1669`). The analyzer needs OpenAI. |
| News monitor → `intelligence.news_alerts` | 2026-09-24 13:24 (424 rows) | **Failing**: scheduled every 5/15/30 min; OpenAI scoring fails with "no credits remaining" (429). Reuters RSS has a DNS error; AP via rsshub returns 403. Not logged to `ingestion_runs`. |
| news_pipeline `raw_news` / `enriched_news` | raw 2026-10-03 17:40 (3,535) / enriched 2026-09-24 13:35 (871) | Collection OK. **Enrichment failing**: same OpenAI credit error; `poll_and_enrich` overlaps; Yahoo market context returns 429. |
| CB feed poll (in-memory cache, no table) | Every 10 min, 6/8 OK | RBA and RBNZ RSS return HTTP errors. Not persisted. |
| rateprobability.com scraper → `rp_scraped_*` | 2026-08-21 20:01 | **Failing**: scheduled 00:30/08:30/16:30; `api/*/latest` returns 403 for all 8 banks. |
| OIS fetch → `ois_cache` / `rate_snapshots` | FED/ECB/BOE 2026-10-03; BOC/BOJ/RBA/RBNZ/SNB 2026-07-22 | Scheduled 06:00. The 5 proxy fetchers return 403 because the URL is doubled (`r.jina.ai/http://r.jina.ai/http://https://…`). The job still logs `'ok'` because it falls back to the cache. |

- Prod comparison: `scripts/sql/pipeline_health.sql` (SELECT only). It returns row counts and newest timestamps for all eight tables; per-bank CB docs, OIS and scraper detail; and the last run, last success and status for each `ingestion_runs` type over 30 days. Run with `psql "$DATABASE_URL" -f scripts/sql/pipeline_health.sql`.

## Part B: missing indicators

- Cause: EODHD publishes US GDP as `GDP Growth Rate` (qoq; advance, second and third estimates for each quarter, at BEA's annualised rate), which was mapped only for UK. ISM services new orders and prices exist under both the current names (`ISM Services New Orders/Prices`) and legacy names (`ISM Non-Manufacturing New Orders/Prices`); neither was mapped. Also added the current `ISM Services Employment` and legacy `ISM Non-Manufacturing Business Activity` names, whose counterparts were already mapped. **ISM Manufacturing Production is not published by EODHD.** `Manufacturing Production` is the Fed IP series and is not substituted.
- `config/indicator_mapping.yaml` +7 entries. Re-ingest: `run_backfill.py --countries US --from 2020-01-01` (14 calls; 761 inserted, 539 updated, 0 errors). Every 6-month chunk returned exactly 1,000 events, which looks like an EODHD per-request cap. That is existing behaviour and can leave gaps; flagged for follow-up.
- New `scripts/reclassify_unmapped.py` replays stored unmapped events that now map (US: 779; deletes the unmapped copies so the calendar has no duplicates). The first run replayed older payloads over newer ones; e.g. GDP Q2 had the 26 Aug second estimate as latest instead of the 30 Sep third. The latest rows were repaired (newest release with an actual; 332 rows across the 13 replayed series), and the script now only replays periods with no mapped row.
- Result: real GDP 24 quarters (latest Q2 2026 = 2.2%), ISM services new orders 47 months, prices 48. `check_desk_indicators.py`: **17 checked, 1 not in DB** (ISM manufacturing production). The USD desk GDP card and all ISM services series are live.
- Pre-existing and left alone: 10 US series have one period where a preliminary release is still flagged latest over the final (UMich, S&P PMIs, PCE qoq, CPI m/m).
- Tests: `test_indicator_mapping_step7.py` (10). Full suite: 414 passed, 7 skipped, 1 deselected.

## Part C: stale pipelines (per decision)

- **OpenAI:** one setting, `OPENAI_MODEL=gpt-4o-mini`. `settings.py` default, compose (app and news), `.env`, the news scorer, `NEWS_SENTIMENT_MODEL` (was gpt-4.1) and the news_pipeline default all use it. This resolves audit #12. Credits check (2026-10-03 18:20): **still 429 "no credits"**.
- **CB documents:** `job:cb_documents` (`app/services/cb_documents_jobs.py`) scrapes and analyses statements, downloads Fed statement and minutes PDFs (`app/processing/cb_fed_documents.py`), then runs the ingester. Fed run (13 months): 17 documents listed (9 statements, 8 minutes), 10 downloaded; 9 reports and 31 documents stored (14 statements, 8 minutes, 9 projections), **0 analysed (credits)**. Once credits exist, the next run analyses every row with `analyzed_at` unset. The Fed view panel stays "source unavailable" until then. Fixed the scraper's Fed statement PDF URL (404). New `minutes` doc type.
- **Schedule:** from `cb_meetings.yaml`, FED and ECB run at decision +30 min and at minutes/accounts release +30 min (`minutes_offset_days` 21 / 28, kept at local clock time across DST). 42 runs registered; next are Fed minutes 7 Oct 14:30 ET and ECB accounts 8 Oct.
- **News AI off** (`NEWS_AI_ENABLED=false`): the app news monitor and news_pipeline `poll_and_enrich` are not scheduled (code kept; news_pipeline image rebuilt, 6 collector jobs). The desk News panel shows "AI notes paused".
- **rateprobability.com off** (`RATEPROBABILITY_SCRAPER_ENABLED=false`); the manual trigger returns 409. `/api/rate-probability` hides banks whose scrape is ≥ 3 days old (all 8 now hidden).
- **OIS:** the doubled proxy prefix is fixed. The remaining 403 was the proxy's Cloudflare challenge triggered by the fetchers' spoofed Chrome UA; they now send `MacroDashboard/0.1 rate-fetcher`. Re-run: **all 8 banks fresh**: FED, BOC, BOJ, RBA and SNB at 2026-10-03; ECB, BOE and RBNZ at 2026-10-01. A cache fallback older than 3 days now returns `stale`; `job:rate_probability_fetch` logs to `ingestion_runs`, so it shows in `/api/admin/jobs/status`.
- Tests: `test_step7_pipelines.py` (6). Full suite: 421 passed, 7 skipped, 1 deselected. pytest was reinstalled from the declared `dev` extras after the app container was recreated.

## Part D: rate-probability methodology (audit #6–#9)

- **#6 ZQ de-averaging (FED):** the strip is mapped back to monthly EFFR averages; meeting months use r_post = (avg×D − r_pre×d_before)/d_after; months without a meeting pin the rate; a meeting in the last 7 days takes r_post from the next contract and backs r_pre out of its own month. r_pre comes from the curve, not the stale config rate (`FED.current_rate` is still 3.75 after the 16 Sep move to 4.00). The fetcher now stores the current-month contract at tenor 0, which avoids the month-end collision.
- **#7 step path:** `get_ois_implied_rate` returns a piecewise-constant path (each post-meeting rate takes effect the day after the meeting) instead of linear interpolation. Non-FED meeting reads are unchanged, because their sources (OIS term rates, 3-month CORRA/SARON/TONA futures, monthly RBA IB) each need their own de-averaging. Flagged for a later step.
- **#8 overrides:** never served once older than 14 days; the view gets `data_state="stale_source"`. Every bank's override is dated 2026-06-26, so none is served now.
- **#9:** the FED override test reads the next meeting from the override config, with an injected clock.

| FED meeting (curve 2026-10-03, yfinance ZQ) | Before: hold / hike, implied | After: hold / hike, implied |
| --- | --- | --- |
| 28 Oct 2026 | 29.4% / 70.6%, 4.006 | 77.9% / 22.1%, 4.010 |
| 9 Dec 2026 | 31.5% / 68.5%, 4.178 | 18.3% / 81.7%, 4.214 |
| 27 Jan 2027 | 50.3% / 49.7%, 4.302 | 61.7% / 38.3%, 4.310 |

- Hand check: 28 Oct is a late-month meeting, so r_post = Nov average 3.93 and r_pre = 3.875 (from the Oct average), giving +5.5 bp. 9 Dec: r_pre 3.93, r_post 4.134, giving +20.4 bp. The old method measured October against config 3.75, which overstated the October hike.
- Tests: mid-month de-averaging, end-of-month next contract, curve-anchored r_pre, step path, 14-day override, stale_source state. The cumulative-delta test was rewritten on a valid ZQ strip. Full suite, **nothing deselected: 428 passed, 7 skipped.**

## Part E: frontend assets

- `app/web/static/vendor/`: ECharts 5.6.0 (what `echarts@5` resolved to; Apache-2.0) and HTMX 1.9.12 (0BSD), with SHA-256 values in `vendor/README.md`. `base.html` and `desk/page.html` load them from `/static/vendor/`; the only remaining external load is Google Fonts. `.gitattributes` marks the files `-text`, so line endings and checksums stay stable.
- Tests: `test_vendored_assets.py` (3) covers checksums, no external `<script>` or non-font `<link>` in any template, and the pinned references. Full suite: 431 passed, 7 skipped, nothing deselected.
