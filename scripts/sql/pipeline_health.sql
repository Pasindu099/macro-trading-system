-- Step 7 Part A: read-only pipeline health. Safe on production (SELECT only).
-- psql "$DATABASE_URL" -f scripts/sql/pipeline_health.sql
SELECT 'cb_policy_documents' AS tbl, count(*) AS n_rows, max(created_at) AS newest_row,
       max(analyzed_at) AS newest_analyzed, max(doc_date)::timestamptz AS newest_doc,
       count(*) FILTER (WHERE analyzed_at IS NULL) AS unanalyzed
FROM cb_policy_documents
UNION ALL SELECT 'intelligence.news_alerts', count(*), max(detected_at), NULL, max(published_at), NULL FROM intelligence.news_alerts
UNION ALL SELECT 'raw_news', count(*), max(fetched_at), NULL, max(published_at), NULL FROM raw_news
UNION ALL SELECT 'enriched_news', count(*), max(created_at), NULL, NULL, NULL FROM enriched_news
UNION ALL SELECT 'rp_scraped_meetings', count(*), max(scraped_at), NULL, NULL, NULL FROM rp_scraped_meetings
UNION ALL SELECT 'rp_scraped_summary', count(*), max(updated_at), NULL, NULL, NULL FROM rp_scraped_summary
UNION ALL SELECT 'ois_cache', count(*), max(fetched_at), NULL, max(curve_date)::timestamptz, NULL FROM ois_cache
UNION ALL SELECT 'rate_snapshots', count(*), max(fetched_at), NULL, max(snapshot_date)::timestamptz, NULL FROM rate_snapshots
UNION ALL SELECT 'source_health', count(*), max(last_checked_at), NULL, max(last_item_at), NULL FROM source_health
ORDER BY 1;

-- Per-bank detail for CB documents and the OIS / scraper feeds.
SELECT bank, doc_type, count(*), max(doc_date), max(analyzed_at) FROM cb_policy_documents GROUP BY 1, 2 ORDER BY 1, 2;
SELECT bank, source, max(curve_date), max(fetched_at) FROM ois_cache GROUP BY 1, 2 ORDER BY 1, 2;
SELECT bank, max(scraped_at) FROM rp_scraped_meetings GROUP BY 1 ORDER BY 1;

-- Logged job runs (ingestion_runs covers job:* and EODHD sessions; news monitor, CB feed poll,
-- rateprobability scraper and OIS fetch do not log here).
SELECT run_type, max(started_at) AS last_run, max(started_at) FILTER (WHERE status = 'success') AS last_success,
       (array_agg(status ORDER BY started_at DESC))[1] AS last_status
FROM ingestion_runs WHERE started_at > now() - interval '30 days' GROUP BY 1 ORDER BY 1;
