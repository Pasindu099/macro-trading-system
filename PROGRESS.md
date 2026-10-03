# Progress — automatic refresh for Event Innovation and Macro State

Legend: `[x]` done · `[~]` in progress (note says what is half-done) · `[ ]` not started.
Decisions: `DECISIONS_STEP1.md`. Each completed sub-task is committed as `step1: <sub-task>`.

## Step 0 — Read-only report
- [x] 0a. REPORT_STEP0.md (scheduler, ingest commit point, event innovation, macro-state builders, ingestion_runs)

## Step 1 — Incremental Event Innovation
- [x] 1a. Migration `0021_job_watermarks` + `JobWatermark` ORM model
- [x] 1b. Read-only dedup-key identity diff implemented and run inside compose. 887 rows / 23 indicators change identity; approved key would reduce 17,734 prints to 17,281 because all changed rows have null period and start date. Detail in `RESUME_STATE.md`.
- [ ] 1c. Align `_RELEASES_SQL` dedup key with ingestion (`period` fallback; note on the null-period edge case)
- [x] 1d. `load_release_records(indicator_ids=...)` filter + affected-indicator query (`retrieved_at > since`, earliest release date per indicator)
- [ ] 1e. Incremental planner: full history for affected indicators + bundle partners, score in memory, persist only rows on/after each indicator's earliest new or revised print, plus affected bundles
- [ ] 1f. Revision cleanup: delete non-winner `event_innovation_scores` rows for affected indicators (same transaction)
- [ ] 1g. Gap 2: delete affected bundles now below `min_bundle_members` (explicit delete; FK `bundle_id` is `ON DELETE SET NULL`)
- [ ] 1h. `score_new_releases(session, since) -> int`
- [ ] 1i. Watermark: `since = last_success_at − 10 min`; new watermark = job START time, written in the same transaction as the scores
- [ ] 1j. Scheduler hooks: after each EODHD session ingest and after each post-release trigger (no fixed timer)
- [ ] 1k. Prod one-off script: full rebuild without `--truncate` + orphan cleanup over all indicators; `--dry-run` prints rows to insert / update, orphans to delete, bundles to delete; `--key-diff` for 1b
- [ ] 1l. `scripts/build_event_innovation.py` takes the same advisory lock as the incremental job

## Step 2 — Daily Macro State rebuild
- [x] 2a. `cb_preferred_score.py:310-317`: raise instead of returning inside `session_scope`; script still prints the error. The other four builders have no early returns; each now raises when its primary output table is empty.
- [x] 2b. `export: bool = True` on the 5 builders (scheduled runs must pass `export=False`)
- [x] 2c. `statement_timeout` passed through to each builder's `session_scope`
- [ ] 2d. `pages.py` `_build_currency_stance_dashboard`: empty `cb_preferred_score` → legacy fallback (the meter function already handles empty)
- [x] 2e. Pipeline runner: processed_dataset → feature_layer → cb_preferred_score → macro_indices → currency_stance; later steps stop on failure; each step logged to `ingestion_runs` with rows and duration; warn if a step holds its lock > 10 s
- [x] 2f. Daily scheduler job at 22:30 UTC

## Step 3 — Safety
- [x] 3a. `run_logger`: `skipped` / `timeout` statuses + `record_rows`; `IngestionRun` doc comment on job usage
- [x] 3b. `session_scope(statement_timeout=...)` (transaction-local `set_config`, the parameterised `SET LOCAL`)
- [x] 3c. Per-job PostgreSQL advisory lock helper (`pg_try_advisory_lock` on a dedicated connection)
- [~] 3d. `asyncio.wait_for` 16-min outer guard is active for Macro State steps; Event Innovation job still needs it
- [x] 3e. `GET /api/admin/jobs/status` (last run, last success, rows written, last error, watermark; `require_role("admin")`)
- [x] 3f. `/api/admin/health` excludes `run_type LIKE 'job:%'`

## Step 3.5
- [ ] Not started. Scope is not defined in the conversation so far

## Tests / checkpoints
- [ ] T1. Unit: only rows after the watermark are written; idempotent re-run
- [ ] T2. Unit: revised release updates its score, no second row
- [ ] T3. Unit: concurrent run → `skipped`
- [ ] T4. Integration (compose DB): fixture release → score appears; `/api/admin/jobs/status` shows the run. Local Docker daemon is not running; command to be handed over
- [ ] T5. Full suite passes (except `test_rate_probability.py:182`)

## Delivery
- [ ] Final report: files changed, migration, test results, steps holding their lock > 10 s, out-of-scope flags (`--reload`/bind-mount, `retrieved_at` index), deploy + backfill + verify commands
