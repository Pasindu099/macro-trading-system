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
- [~] 3.5b. Services now cover Macro State, rates, country/public data, News, bank research admin, knowledge figures/documents, rate probability, analytics/overview data, and Central Banks monitor/policy/projections. Remaining: finish raw field cleanup and pin representative outputs before removing old routes.
- [x] 3.5b-query. Country histories, macro monitor, and projection actuals batched in separate commits with focused tests.
- [x] 3.5c. Tagged the last commit before removal as `pre-redesign` (`cc8e4fc`).
- [x] 3.5d. Removed old page routes, Jinja page templates, static page modules, old CSS, React/Babel brief builder, and Chart.js. Auth and JSON APIs remain.
- [~] 3.5e. New shell routes, navigation (including Event Log), Syne + DM Mono tokens, dark palette, and ECharts are in place. Final auth styling and checkpoint report pending.

## Tests / checkpoints
- [x] T1. Unit planner writes the affected suffix and affected bundle partners; repeated plan is identical.
- [x] T2. Integration confirms a revised release replaces its score, leaving one row.
- [x] T3. Unit confirms concurrent run marks `skipped`.
- [x] T4. Integration (compose DB): fixture release → score and revision cleanup pass; `/api/admin/jobs/status` displays a logged job run and rows written.
- [x] T5. Unit suite: 260 passed, 7 skipped, with only the known out-of-scope `test_rate_probability.py:182` failure.

## Delivery
- [x] `REPORT_STEPS1_3.md`: files changed, migration, tests, >10 s lock durations, out-of-scope flags, and production commands.
