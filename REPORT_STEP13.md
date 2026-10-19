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

## Part F — episode links and cross-asset history

- Migration `0042_news_episode_assets` applied locally. A 42,529,597-byte database backup was saved at `backups/news-step13-0042.dump` before the history backfill (after the schema migration).
- `news_item_episodes` links a tagged item to every matching situation episode whose inclusive UTC date span contains publication; 797 historical links added. Daily situation refresh now preserves episode IDs for unchanged starts (`app/services/situations.py:328`).
- `cross_asset_daily_snapshots` holds one row per UTC observation date and series, with value, source and provider series/symbol. Daily job refreshes seven days; the backfill loads FX and computed DXY from EODHD, FRED rates/commodities/index series, and EODHD metals (`app/services/news_assets.py:1`, `scripts/backfill_news_assets.py:1`).
- Historical backfill finished successfully: 14 series, 90,652 daily observations. Coverage by series (first..last date, rows):

| Series | Coverage | Rows |
|---|---|---:|
| XAU/USD | 2000-01-03..2026-10-18 | 7,401 |
| XAG/USD | 2000-01-03..2026-10-18 | 7,953 |
| Brent | 2000-01-04..2026-10-14 | 6,487 |
| WTI | 2000-01-04..2026-10-14 | 6,338 |
| Computed DXY | 2002-05-06..2026-10-18 | 6,354 |
| USD/JPY | 2000-01-03..2026-10-19 | 7,230 |
| USD/CHF | 2000-01-03..2026-10-19 | 7,056 |
| EUR/USD | 2000-01-03..2026-10-18 | 7,244 |
| VIX | 2000-01-03..2026-10-16 | 6,772 |
| US 2Y | 2000-01-03..2026-10-15 | 6,700 |
| US 10Y | 2000-01-03..2026-10-15 | 6,700 |
| US real 10Y | 2003-01-02..2026-10-15 | 5,951 |
| US 10Y breakeven | 2003-01-02..2026-10-16 | 5,952 |
| S&P 500 | 2016-10-17..2026-10-16 | 2,514 |

- Focused tests: 13 passed. Full suite: 726 passed, 7 skipped. SQLAlchemy emitted connection cleanup warnings after pytest, but all tests passed.
- 48-hour collector overlap is still pending; `scripts/news_parity.py --hours 48` remains deferred and old collectors stay enabled. Stopped after F; Part G untouched.

## Part G — News page

- `/news` now shows collector check status and daily kept/dropped/merged counts, High items from the last six hours, filtered tagged headlines, and today's releases/speakers/blackouts/currency mentions (`app/api/routes/shell.py:109`, `app/web/templates/news/page.html:1`).
- Search, currency, type, impact, active situation, arbitrary past UTC start/end dates and the opt-in unlinked-geopolitics filter query durable `news_items`. The page uses a private 60-second browser cache, responsive layout and shared freshness navigation. Beat/miss uses amber/blue; no intraday reaction is invented (`app/services/news.py:195`, `app/web/static/css/news.css:1`).
- The USD/EUR desk News panel and Overview "What's moving markets" now use `news.tagged_feed`; source labels and stored context are retained (`app/services/desk_panels.py:695`, `app/services/overview.py:288`).
- Focused tests: 93 passed. Historical date-range route and combined database filter smoke passed. Full suite: 727 passed, 7 skipped.
