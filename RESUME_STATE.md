# Resume state — 2026-10-03

## Verified complete

- Migration `0021_job_watermarks` and `JobWatermark` ORM model exist and match the approved start-time watermark schema (`97e624a`).
- `run_logger` supports `skipped`, `timeout`, and analytics row counts in `events_inserted`; `IngestionRun` documents job conventions (`e0ab879`).
- `session_scope(statement_timeout=...)` applies a transaction-local PostgreSQL timeout through `set_config` (`cbae5b4`).
- The Step 0 report, decisions, and progress tracker were committed (`1bcfccf`).

## Partial and missing

- Step 1 has no incremental scoring implementation yet. The release dedup SQL still uses `released_at::date` as its fallback, has no indicator filter, and `persist` only upserts scores and bundles. The pre-change identity diff requires a database and must be reported before changing the key.
- No revision-orphan cleanup, stale-bundle cleanup, watermark read/write, scheduler hooks, advisory lock, or production one-off script is present.
- Step 2 builders have no scheduled `export=False` path or daily chain. `cb_preferred_score` still has the early return that can commit empty tables.
- Step 3 has the logging and session timeout pieces above, but no advisory lock, outer 16-minute guard, jobs status endpoint, or health exclusion.
- Requested unit and integration checkpoints for jobs are absent.

## Working tree

- `context.md` is modified and `PROJECT_AUDIT.md` is untracked. They are unrelated documentation work and have not been incorporated into this task.
- Untracked fixed-income screenshots, yield/FX backfill checkpoints, and indicator-correlation outputs are unrelated generated data. Their completeness cannot be inferred from the files alone. None appears to contradict `DECISIONS_STEP1.md`; all will be left untouched.

## Unit test result

`pytest tests/unit -q` was unavailable on the system PATH. The equivalent command `.\venv\Scripts\python.exe -m pytest tests/unit -q` ran: **258 passed, 7 skipped, 1 failed**. The failure is the pre-existing hard-coded FED meeting date in `tests/unit/test_rate_probability.py:182` (expected `2026-06-17`, actual `2026-10-28`).

At the initial assessment Docker was stopped; it was started later in this session.

## Database identity diff (run after Docker started)

`docker compose exec app python -m scripts.diff_event_innovation_keys` returned:

- 887 rows with a changed key across 23 indicators.
- 17,734 old deduplicated prints versus 17,281 with the approved expression: **453 fewer prints**.
- All 887 changed rows have `period_start_date IS NULL` and `period IS NULL`; none has a start date.

The approved two-part `COALESCE` would collapse all such rows of each indicator into one null-key group. The key edit is paused pending the user's choice of a fallback for this edge case.
