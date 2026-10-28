# Step 17 - Data services (2026-10-28)

| Part | Delivered | Container verification |
|---|---|---|
| A | Indicator catalogue, revision-aware history, persisted surprises, bounded CSV stream | Focused: 9 passed. Full: 859 passed, 7 skipped, 1 warning (172.23s). |
| B | Source health and thirty-day quality inventory | Focused: 13 passed. Full: 872 passed, 7 skipped, 1 warning (95.80s). |
| C | Nine browsable/exportable research dataset families, provenance and redistribution policy | Focused + CSV regression: 24 passed. Full: 887 passed, 7 skipped, 1 warning (83.68s). |
| D | Data landing/country/indicator/source/dataset pages; charts, filters, CSV and states | Focused: 14 + 2 passed. Full: 892 passed, 7 skipped, 1 warning (59.91s). |
| E | Desk/calendar/monitor links, permanent legacy redirects, release smoke checks | Focused: 19 passed. Full: 905 passed, 7 skipped, 1 warning (64.84s). |

- A: `app/services/data_library.py`; every mapped indicator, including empty histories; 11 country codes; CN flagged calendar-only. Macro State unchanged.
- Revision chains retain every actual print; first/latest values, revision size and actual-change count. Null surprises stay null when no persisted score exists.
- Surprise source: direct `event_innovation_scores` join. Application callers changed: **0**; repository scan confirms no runtime imports of deprecated `release_ledger`. Old module retained.
- `app/services/data_exports.py`: connection-owned streaming CSV, 50,000-row SQL and iterator caps, download headers, spreadsheet formula escaping.
- Tests: `tests/integration/test_data_library.py`; rollback-only database fixtures.
- B: `app/services/data_quality.py`; freshness/job/collector/manual-import composition, five explicit statuses, live scheduler next-run dates when supplied, persisted rates schedule fallback, recursively redacted errors.
- Thirty-day outliers, holiday-aware daily FX/yield/FRED gaps, actual revisions by country, top 20 unmapped names, parser coverage on investingLive Data releases, unavailable model snapshots and current situation inputs.
- Situation unavailability uses current deterministic features; historical unavailable situation states were not persisted by the existing job, so no retrospective states are invented.
- Tests: `tests/integration/test_data_quality.py`; live read-only inventory and health queries, rollback-only quality fixtures, manual/disabled/error states and scheduler/error-redaction checks.
- Outliers are dated by observation date (the existing flag has no flag-time audit column); revisions by retrieval date; gaps exclude market holidays and pre-coverage dates.
- C: `app/services/research_datasets.py:19` registers cross-asset, FX, government yields, yield spreads, COT, Myfxbook retail, model rates, situation episodes and dominance snapshots.
- Coverage reports first/last dates, row counts and actual sources per series. Filters: dates, series and currency; pagination; valid/latest market observations; model inputs/confidence and full episode evidence retained.
- Cross-asset metadata lists all 14 requested assets; FX lists 28 pairs + USD/SEK + computed DXY. Empty series remain visible in expected-series metadata; no backfills or external fetches.
- `config/research_datasets.yaml:2`: set `show_unlicensed_sources: false` to exclude restricted datasets from catalogue, reads and CSV. Restricted inputs make derived datasets personal use. FRED/third-party and central-bank rights are conservatively restricted; CFTC public reports are flagged redistributable.
- Shared CSV exports serialize nested inputs/evidence as JSON; 50,000-row SQL and iterator caps. Tests verify escaping, caps, filters, deduplication, named FR-DE/EUR selection and policy enforcement.
- Tests: `tests/integration/test_research_datasets.py`; every dataset SQL query runs in a read-only transaction; synthetic filter/export fixtures roll back.
- Part commits: A `29bcfa5`; B `c31cd89`; C `99ef32d`; D `a854b69`; E is tagged `v2.7-data`.
- D: `app/api/routes/data.py`, `app/web/templates/data/`, `app/web/static/css/data.css`, `app/web/static/js/data.js`; shared nav/freshness, 60 s cache, 500-row table pages, first-print/revised ECharts and persisted-surprise bars.
- Catalogue now includes revision-aware last-24-print sparklines. Personal-use policy is checked before cached content; empty/unavailable/error/loading states remain explicit.
- Browser: 12 view/viewport checks at 1440/390, zero page overflow or JS errors; chart toggle and CSV verified. Screenshots/results: `data/screenshots/step17-data/`; runner: `tests/browser/check_data.cjs`.
- D full-suite first attempt found two stale placeholder-heading assertions (890 passed); updated those assertions, focused checks passed, final full suite green.
- Requested `DECISIONS_STEP17_DATA.md` is absent; D/E follow the full decisions supplied in chat. Existing quality limitations above remain unchanged.
- E: `app/api/routes/data.py` adds 301 country/indicator redirects with query preservation; desk key-data links respect EUR member selection; country monitor links DE/FR; calendar Full history targets the selected component's country/canonical.
- Final browser rerun: 12 view/viewport checks plus desk/member links and 301 navigation, zero page overflow or JS errors; chart toggle/CSV passed. Temporary preview container/file removed.
- `DEPLOY_RUNBOOK.md`: Step 17 authenticated smoke checks, migration head, cache/CSV checks and personal-use exclusion switch. Stack app health 200; PostgreSQL/Redis healthy.
- D/E complete. No LLM, external data fetch, backfill or production deployment used. Tag is local; nothing pushed.
- Verification used the shared working tree; pre-existing whiteboard/untracked changes were preserved and excluded from the D/E commits.
