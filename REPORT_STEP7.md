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
