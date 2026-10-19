# Step 13 newswire status — 19 October 2026

## Part C follow-up

- Decisions recorded in `DECISIONS_STEP13_NEWS.md`. Al Jazeera remains enabled and unlicensed for redistribution (`config/news_sources.yaml`).
- Unlinked geopolitics is retained as `Geopolitics`, `Low`, `no_fx_link=true`; the default `/news` feed hides it until the checkbox is selected. Active situation links and narrow tier-1 shock phrases promote the item (`app/services/news_tagging.py:209`, `app/api/routes/shell.py:86`).
- Migration `0040_news_no_fx_link` applied locally after a 42,463,127-byte backup at `backups/news-step13-before-0040.dump`.
- Existing rows reprocessed: 415 formerly dropped geopolitical rows became kept; 0 newly dropped; 342 existing item tags updated. Current statuses: kept 2,835; merged 2; dropped 2,051. `no_fx_link` items: Al Jazeera 344, AP News 94, investingLive 7.
- Event Log test now expects the stored `8.25` move.
- Focused tests: 45 passed. Full suite with local Postgres/Redis: 715 passed, 7 skipped. An initial isolated-container run lacked DB connectivity; the connected rerun was green.
- Parity: new app collector began on 19 October; 48 hours of *collector overlap* have not elapsed. Old collectors remain enabled. Run `scripts/news_parity.py --hours 48` after the overlap window.
