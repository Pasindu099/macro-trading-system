# Step 13 newswire status — 19 October 2026

## Part C follow-up

- Decisions recorded in `DECISIONS_STEP13_NEWS.md`. Al Jazeera remains enabled and unlicensed for redistribution (`config/news_sources.yaml`).
- Unlinked geopolitics is retained as `Geopolitics`, `Low`, `no_fx_link=true`; the default `/news` feed hides it until the checkbox is selected. Active situation links and narrow tier-1 shock phrases promote the item (`app/services/news_tagging.py:209`, `app/api/routes/shell.py:86`).
- Migration `0040_news_no_fx_link` applied locally after a 42,463,127-byte backup at `backups/news-step13-before-0040.dump`.
- Existing rows reprocessed: 415 formerly dropped geopolitical rows became kept; 0 newly dropped; 342 existing item tags updated. Current statuses: kept 2,835; merged 2; dropped 2,051. `no_fx_link` items: Al Jazeera 344, AP News 94, investingLive 7.
- Event Log test now expects the stored `8.25` move.
- Focused tests: 45 passed. Full suite with local Postgres/Redis: 715 passed, 7 skipped. An initial isolated-container run lacked DB connectivity; the connected rerun was green.
- Parity: new app collector began on 19 October; 48 hours of *collector overlap* have not elapsed. Old collectors remain enabled. Run `scripts/news_parity.py --hours 48` after the overlap window.

## Part D — headline actuals

- Migration `0041_newswire_actuals` applied locally after a 42,512,543-byte backup at `backups/news-step13-before-0041.dump`.
- Explicit investingLive actual-vs-expected headlines match a unique calendar release by country, indicator and ±30 minutes. Co-released variants use forecast values; exact duplicate release rows collapse to the newest ID. Ambiguous matches remain unfilled (`app/services/news_actuals.py:1`).
- A provisional actual is versioned with `source_status=newswire`; EODHD confirmation creates a new version even when its actual agrees. Disagreements stay in `newswire_actuals` as discrepancies. Scheduled EODHD polls cannot erase a provisional print (`app/ingestion/ingest_service.py:207`). Event Innovation reruns after provisional writes.
- Last 30 days locally: 90 tagged investingLive data items; 5 parsed explicit values, all 5 matched (100% of parsed, 5.6% of all tagged data items). The remaining 85 need broader headline syntax support; they were left unfilled.
- Focused tests: 15 passed. Full suite: 720 passed, 7 skipped.

## Part E — “Why it matters”

- `app/services/news_context.py:1` returns a line only when supporting data exist: actual vs consensus and CB tracking, speaker role/voter/focus, situation evidence, or named commodity drivers. It omits unverified tone and generic filler.
- `/news` shows the line below the headline when present; `app/services/news.py:1` gathers the underlying data through stored joins and existing CB/situation services.
- Focused tests: 21 passed. Live local feed smoke returned contextual and empty-line cases. Full suite: 724 passed, 7 skipped.
