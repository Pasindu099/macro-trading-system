# Progress — automatic refresh for Event Innovation and Macro State

Legend: `[x]` done · `[~]` in progress (note says what is half-done) · `[ ]` not started.
Decisions: `DECISIONS_STEP1.md`. Each completed sub-task is committed as `step1: <sub-task>`.

## Step 0 — Read-only report
- [x] 0a. REPORT_STEP0.md (scheduler, ingest commit point, event innovation, macro-state builders, ingestion_runs)

## Step 1 — Incremental Event Innovation
- [x] 1a. Migration `0021_job_watermarks` + `JobWatermark` ORM model
- [x] 1b. Three-level key diff run inside compose: 0 changed identities; 17,734 prints both ways; 0 collapses. Null-period ingestion findings in `REPORT_NULL_PERIODS.md`.
- [x] 1c. `_RELEASES_SQL` now uses the approved three-level key, preserving release-date identity when both period fields are null.
- [x] 1d. `load_release_records(indicator_ids=...)` filter + affected-indicator query (`retrieved_at > since`, earliest release date per indicator)
- [x] 1e. Incremental planner loads full history for affected indicators and bundle partners, then writes the affected suffix and partners on affected bundle dates.
- [x] 1f. Revision cleanup deletes non-winner scores for affected indicators in the same transaction, using the three-level key.
- [x] 1g. Affected bundles below `min_bundle_members` are explicitly deleted; the FK sets stale score `bundle_id` null.
- [x] 1h. `score_new_releases(session, since) -> int` implemented.
- [x] 1i. Watermark uses a 10-minute overlap and advances to job START time in the same transaction as scores.
- [x] 1j. Scheduler hooks run after each EODHD session and post-release ingestion commit (no fixed timer).
- [x] 1k. `scripts/rebuild_event_innovation.py` fully reconciles without `--truncate`; `--dry-run --key-diff` prints score inserts/updates, orphan deletes, bundle inserts/updates/deletes. Compose preview: 483 score inserts, 17,251 score updates, 57 orphans, 1 stale bundle.
- [x] 1l. `scripts/build_event_innovation.py` takes the same advisory lock as the incremental job.

## Step 2 — Daily Macro State rebuild
- [x] 2a. `cb_preferred_score.py:310-317`: raise instead of returning inside `session_scope`; script still prints the error. The other four builders have no early returns; each now raises when its primary output table is empty.
- [x] 2b. `export: bool = True` on the 5 builders (scheduled runs must pass `export=False`)
- [x] 2c. `statement_timeout` passed through to each builder's `session_scope`
- [x] 2d. Superseded by Amendment 2: no `pages.py` changes in Steps 1–3. The new data service must handle empty primary scores when Step 3.5 moves needed data logic.
- [x] 2e. Pipeline runner: processed_dataset → feature_layer → cb_preferred_score → macro_indices → currency_stance; later steps stop on failure; each step logged to `ingestion_runs` with rows and duration; warn if a step holds its lock > 10 s
- [x] 2f. Daily scheduler job at 22:30 UTC

## Step 3 — Safety
- [x] 3a. `run_logger`: `skipped` / `timeout` statuses + `record_rows`; `IngestionRun` doc comment on job usage
- [x] 3b. `session_scope(statement_timeout=...)` (transaction-local `set_config`, the parameterised `SET LOCAL`)
- [x] 3c. Per-job PostgreSQL advisory lock helper (`pg_try_advisory_lock` on a dedicated connection)
- [x] 3d. `asyncio.wait_for` 16-min outer guard is active for Macro State and Event Innovation; `session_scope` rolls back on cancellation.
- [x] 3e. `GET /api/admin/jobs/status` (last run, last success, rows written, last error, watermark; `require_role("admin")`)
- [x] 3f. `/api/admin/health` excludes `run_type LIKE 'job:%'`

## Step 3.5 — UI replacement (Amendment 2)
- [x] 3.5a. `INVENTORY_PAGES.md` classifies every top-level and nested helper, all 33 routes, and direct query families. **STOP for user review before moving code.**
- [x] 3.5b. Services cover Macro State, rates, country/public data, News, bank research admin, knowledge figures/documents, rate probability, analytics/overview data, and Central Banks monitor/policy/projections. Public data functions return raw values and representative outputs are pinned by unit tests. Rates retains live EODHD calls as requested.
- [x] 3.5b-query. Country histories, macro monitor, and projection actuals batched in separate commits with focused tests.
- [x] 3.5c. Tagged the last commit before removal as `pre-redesign` (`cc8e4fc`).
- [x] 3.5d. Removed old page routes, Jinja page templates, static page modules, old CSS, React/Babel brief builder, and Chart.js. Auth and JSON APIs remain.
- [x] 3.5e. New shell routes, navigation (including Event Log), Syne + DM Mono dark tokens, ECharts only, and working restyled login/setup/users pages.

## Tests / checkpoints
- [x] T1. Unit planner writes the affected suffix and affected bundle partners; repeated plan is identical.
- [x] T2. Integration confirms a revised release replaces its score, leaving one row.
- [x] T3. Unit confirms concurrent run marks `skipped`.
- [x] T4. Integration (compose DB): fixture release → score and revision cleanup pass; `/api/admin/jobs/status` displays a logged job run and rows written.
- [x] T5. Unit suite: 260 passed, 7 skipped, with only the known out-of-scope `test_rate_probability.py:182` failure.
- [x] T6. Step 3.5 unit suite: 285 passed, 7 skipped, with only the same known FED date failure; 285 pass when that one test is deselected.
- [x] T7. Step 3.5 integration suite: 22 passed; `-k jobs`: 2 passed.

## Delivery
- [x] `REPORT_STEPS1_3.md`: files changed, migration, tests, >10 s lock durations, out-of-scope flags, and production commands.
- [x] `REPORT_STEP3_5.md`: recovery tag, extracted services, new API endpoints, deleted files, transitional helper inventory, and test results.

## Step 4 — Rates data layer
- [x] 4a. `REPORT_STEP4_COVERAGE.md`: 50 yield country/tenor series, 14 FX pairs, backfill/status gaps, and policy-rate source. The user approved proceeding with unavailable outputs for missing FR/30Y/spot data and a permanent 2Y-vs-10Y regime.
- [x] 4b. `config/pairs.yaml`: 28 market-convention pairs, pip sizes, benchmarks, FR−DE spread, and explicit unavailable 30Y fallbacks. Focused tests: 2 passed. Full suite: 291 passed, 7 skipped, 1 deselected, 18 integration failures from unauthenticated test clients.
- [x] 4c. Captured fixed-window live outputs; rates research, yield differentials, and repricing now read stored observations. Comparison and above-threshold provider revisions are in `REPORT_STEP4.md`. Focused tests passed; full suite: 292 passed, 18 existing auth-related integration failures.
- [x] 4d. Migration `0022` outlier columns and reversing-spike flagging job; raw rows retained. Local run flagged 13 yields, 0 FX. Focused tests passed; full suite 294 passed with the same 18 auth-related integration failures.
- [x] 4e. Migration `0022` yield spreads, common-date/limited forward-fill alignment, and 2Y/10Y/available-30Y builds. Local rebuild: 45,200 rows; 28 names each for 2Y and 10Y. Focused tests passed.
- [x] 4f. Curve metrics, 2Y/10Y regimes, inversion, and un-inversion service functions. Focused tests: 8 passed; full suite: 304 passed with the same 18 auth-related integration failures.
- [x] 4g. Daily-change driver correlations and status service. Local 2Y check: 14 pairs available, 14 unavailable with reason. Focused tests: 3 passed; full suite: 307 passed with the same 18 auth-related integration failures.
- [x] 4h. Post-ingest `job:rates_derived` and four service-backed JSON endpoints. Full suite: 329 passed, 7 skipped, known FED date test deselected; jobs integration: 2 passed.
- [x] 4i. `REPORT_STEP4_SOURCING.md`: availability, 20 yield and 14 FX backfills, 54 Part I API calls, 60,476 derived spreads, six available 10s30s curves, FR−DE 2Y/10Y and all 14 new pairs' drivers verified.
- [x] 4t. Focused unit and integration checkpoints, `REPORT_STEP4.md`, final full suite: 333 passed, 7 skipped, known FED date test deselected.

## Step 5 — Rates integrity + COT positioning
Decisions: `DECISIONS_STEP5.md`. Commits: `step5: <sub-task>`.
- [x] 5-0. Rates integrity: all gaps are provider revisions (re-sourced FX history, intraday last-day captures), no date shift. Daily rates job now re-fetches ≥5 business days of yields and all 28 FX pairs, then rebuilds derived data. Regression test added; full suite 336 passed.
- [x] 5a. COT state: Legacy report, deacot zip, name-matched contracts, 6h in-process cache. TFF includes DX (098662) 2010–2026 → USD direct.
- [x] 5b. Migration 0023 `cot_positions`, `config/cot_contracts.yaml` (USD = DX 098662), TFF backfill 34,960 rows 2010-01-05..2026-09-29 (idempotent), `job:cot_weekly` Fri 21:00 + Mon retry. Full suite 341 passed.
- [x] 5c. 7 USD majors backfilled to 2010-01-01 (7 history + 1 listing call, 31,354 rows); Part 0 comparison now 0 diff on all 15 series.
- [x] 5d. `positioning.py`: net/%OI, 1y/3y/5y percentiles, 1W/4W changes, crowding, squeeze, after-extremes, pair-implied. 15 unit tests.
- [x] 5e. Six `/api/positioning/*` endpoints, viewer auth; 3 unit + 1 integration test.
- [x] 5t. `REPORT_STEP5.md`; full suite 360 passed, 7 skipped, 1 deselected; integration 25 passed.

## Step 6 — USD desk
Decisions: `DECISIONS_STEP6.md`. Commits: `step6: <sub-task>`.
- [x] 6-0. USD/SEK backfilled (4,443 rows); computed DXY 4,428 rows in `job:rates_derived`; USD positioning uses DXY; `against_crowd_after_8w` + `max_adverse_move_8w`. Full suite 362 passed.
- [x] 6a. `/desks/{currency}` (USD only; others 404) from `config/desks.yaml`; Jinja page + HTMX lazy partials (`hx-trigger=revealed`) + ECharts from `data-chart`; `scripts/check_desk_indicators.py` reports 4 unmapped series.
- [x] 6b. 15 panels in mockup order: live = price, economy, direction, key data, priced, curve, positioning, catalysts, news, verdict regime; pending = situations/oil (10), fed path/gap/projections/speakers/SEP (8), scenarios/bias (10); Fed view source-unavailable (no analysed documents). 40 desk tests; full suite 402 passed.
- [x] 6c. Loading/empty/unavailable/pending/error states, 60 s panel cache, 390 px verified (0 overflow); visual-check fixes: curve `value_bp`, impact 1 = high, ref line in range.
- [x] 6t. `REPORT_STEP6.md`, screenshots in `data/screenshots/`; full suite 404 passed, 7 skipped, 1 deselected; integration 25 passed.

## Step 7 — Pipeline health, missing indicators, rate-probability methodology
Decisions: `DECISIONS_STEP7.md`. Commits: `step7: <sub-task>`.
- [x] 7a. Health: CB docs never run (unscheduled); news_alerts + enrichment failing since 09-24 (OpenAI no credits); rp scraper 403 since 08-21; 5 OIS proxies 403 (doubled jina URL) since 07-22; prod SQL `scripts/sql/pipeline_health.sql`.
- [x] 7b. US GDP (`GDP Growth Rate` qoq) + ISM services new orders/prices mapped; ISM mfg production not published by EODHD; reclassify script; desk check 17/1 missing; full suite 414 passed.
- [x] 7c. OPENAI_MODEL=gpt-4o-mini everywhere; job:cb_documents (Fed 31 docs stored, 0 analysed: no credits) scheduled on decision/minutes days; news AI + rateprobability scraper off by flag; OIS all 8 fresh (prefix + spoofed-UA fix), stale status logged. Full suite 421 passed.
- [x] 7d. FED ZQ de-averaging (late-month → next contract), step path, overrides >14 d not served (stale_source), FED test from config. Oct 28 hike 70.6% → 22.1%, Dec 9 68.5% → 81.7%. Full suite 428 passed, nothing deselected.
- [x] 7e. ECharts 5.6.0 + HTMX 1.9.12 vendored (checksummed, -text); no runtime CDN scripts; full suite 431 passed.
- [x] 7f. EODHD cap confirmed (latest 1,000 per request); monthly re-fetch found 19,619 missing events; ingested 6,475 + 1,253 newer revisions, 364 older skipped, 11,232 unmapped, 0 errors.
- [x] 7t. `REPORT_STEP7.md`; full suite 434 passed, 7 skipped, nothing deselected; integration 25 passed.

## Step 8 — Fed projections, tracking, regime and gap
Decisions: `DECISIONS_STEP8.md`. Commits: `step8: <sub-task>`. Scope: Fed only.
- [x] 8-0. Log redaction (formatter + EODHD + run_logger + news_pipeline); checkpoints untracked; Fed rate from decision (4.00%); EI rebuild 25,341 rows; Macro State chain success; top-40 unmapped reported.
- [x] 8a. `config/central_banks.yaml` for 8 banks (Fed verified; others verify: true).
- [x] 8b. Migration 0024; deterministic SEP parser; 26 rounds 2020-06 → 2026-09 loaded, 0 rejected; March 2026 and December 2025 checks match.
- [x] 8c. fred_observations (0025) + job:fred_series; tracking: PCE/core running_cold, UR/GDP on_track; revisions; reaction flag none.
- [x] 8d. Regime hiking (4.00%, +25bp 16 Sep); checklists 1/3 and 0/5; gap 2026 −3bp, later years beyond strip; tilt dovish_risk.
- [x] 8e. /api/cb/{bank}/* (6 endpoints, Fed only); desk panels 2, 3, 4, 6 filled; curve policy lookup no longer depends on is_latest.
- [x] 8t. `REPORT_STEP8.md`; full suite 481 passed, 7 skipped, nothing deselected; integration 25 passed.

## Step 9 — EUR desk groundwork
Decisions: `DECISIONS_STEP9.md`. Stop after Part B for user review.
- [x] 9-0. Null-period identity uses release date; 23-indicator repair changed 544 flags, rescored 911 Event Innovation rows, repeat dry-run 0; curve policy query restored `is_latest`. Full suite: 482 passed, 7 skipped, nothing deselected.
- [x] 9a. EUR desk config (EZ/DE/FR, DE yield benchmark), canonical indicators, ECB source verification and gap report. Full suite: 482 passed, 7 skipped, nothing deselected.
- [x] 9b. EZ/DE/FR country monitor, annual Eurostat deficit store (migration 0026, 18 rows through 2025), and FR−DE 10Y. Full suite: 485 passed, 7 skipped, nothing deselected.
- [ ] 9c–f. ECB projections/tracking, €STR maths, EUR price data and desk (next session after the Part B stop).

## Step 10 — situations, verdicts, scenarios
- [x] 10a. Eight deterministic situations; migration 0027; FRED Brent/VIX/SP500; 23:50 UTC job; full-history backtest 174 episodes with the June 2024 FR sanity gap recorded. Full suite: 495 passed, 7 skipped, nothing deselected.
- [x] 10b. Weighted v1 verdict and two-sentence templated thesis for USD/EUR; missing ECB inputs explicit. Full suite: 498 passed, 7 skipped, nothing deselected.
- [x] 10c. Meeting-priced USD scenarios, unavailable ECB pricing pending Step 9 Part D, and unpriced high-severity situation tails. Full suite: 501 passed, 7 skipped, nothing deselected.
- [ ] 10d. APIs and USD/EUR desk panels; final report and current verdicts.
