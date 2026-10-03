# Decisions for Step 1–3 (verbatim)

Source: the user's reply to REPORT_STEP0.md, 2026-10-03. Copied verbatim below.

> **Amendments:** the request mentions "both amendments", but no amendment text
> appears in the conversation this file was written from. Paste them under
> "Amendments" at the bottom; until then they are **not** applied.

---

```
DECISIONS FOR STEP 1–3 (proceed after reading all of them)

1. REVISIONS → option (a), plus key alignment.
   - In the same transaction as the upsert, delete event_innovation_scores
     rows (for the affected indicators) whose release_id is no longer the
     dedup winner for its (indicator_id, period key).
   - Align the dedup key with ingestion: in _RELEASES_SQL use
     COALESCE(period_start_date::text, period) instead of
     COALESCE(period_start_date::text, released_at::date::text),
     so both layers identify a print the same way.
     Before applying, run a dry-run diff and report how many prints
     change identity (expected: only period-less indicators).
   - Gap 2: in the same transaction, delete release_bundles rows for
     affected (bundle_key, release_date) whose scored membership is now
     below min_bundle_members (FK cascade or explicit delete, your call,
     but report which).

2. INCREMENTAL SCOPE → approved as you described.
   - Load full history per affected indicator + all bundle members on
     affected dates; score in memory; persist only rows on/after each
     indicator's earliest new/revised print, plus affected bundles.
   - Watermark: select rows with retrieved_at > (last_success_at − 10 min).
     retrieved_at is set before commit, so a row can commit after a run
     reads; the overlap catches it. Runs are idempotent, so overlap is safe.
   - Set the new watermark to the job's START time, not its end time.
   - If the advisory lock is held, log status "skipped" and return; the
     next run picks the rows up via the watermark. No retries needed.

3. MACRO STATE → option (a) now.
   - Keep in-transaction DROP/CREATE.
   - Fix cb_preferred_score.py:310-317: raise an exception instead of
     returning, so session_scope rolls back and old tables survive.
     Audit the other four builders for the same early-return pattern
     and fix identically.
   - pages.py: treat an EMPTY cb_preferred_score as missing, so the
     legacy fallback renders instead of an empty board.
   - Log each step's duration. If any step holds its lock > 10 s, note
     it in the final report; we revisit staging tables (option b) then.

4. EXPORTS → add export: bool = True to each builder; scheduled runs pass
   export=False; scripts keep current behaviour.

5. ENDPOINT → GET /api/admin/jobs/status (existing router + require_role).

6. JOB LOGGING → reuse ingestion_runs.
   - run_type: "job:event_innovation_incremental", "job:macro_state:<step>",
     countries_fetched = [] and rows in events_inserted (document this in a
     comment on the model).
   - Add statuses "skipped" and "timeout" in run_logger.
   - Change /api/admin/health to EXCLUDE run_type LIKE 'job:%' from the
     ingestion totals; jobs appear only in /api/admin/jobs/status.

7. TIMEOUTS → both.
   - SET LOCAL statement_timeout = '15min' inside each job transaction.
   - asyncio.wait_for at 16 min as an outer guard; on timeout log status
     "timeout".

8. INTEGRATION TEST → run it inside the container:
   docker compose exec app pytest tests/integration -k jobs
   If the DB is still unreachable, write the test anyway and give me the
   exact command; I will run it.

9. PROD ONE-OFF → approved: full rebuild WITHOUT --truncate + orphan
   cleanup over all indicators. Provide it as a script with --dry-run
   that prints: rows to insert, rows to update, orphan rows to delete,
   bundles to delete. I run dry-run first, then the real run.

OUT OF SCOPE THIS STEP (flag in report only, do not change):
- --reload and the bind-mount in docker-compose.yml
- missing index on retrieved_at (fine at ~50k rows)

Proceed with Step 1–3. Deliver files one at a time with paths, then the
migration, then test results.
```

---

## Original task constraints (from the Step 1–3 brief, still binding)

```
DO NOT
- Do not run or schedule anything with --truncate.
- Do not change scoring maths, bundle config or decay parameters.
- Do not touch rate-probability code, news_pipeline/, or the frontend.
- Do not add new dependencies.
- Do not modify existing migrations; add new ones only.
```

## Amendments

### Amendment 2 — UI replacement (2026-10-03)

- Steps 1–3 are backend only. Do not modify `pages.py`, templates, or static JS/CSS. Expose data needed by the replacement design through `app/services/` functions; route handlers must not contain raw SQL.
- Step 3.5 begins only after Steps 1–3 pass. First inventory every helper and query in `pages.py`, classifying it as needed data logic, presentation only, or unused. Write `INVENTORY_PAGES.md` and **stop for user review before moving code**.
- After review, move needed data logic to services with unit tests that pin outputs. Tag the last commit before removal (for example, `pre-redesign`). Remove old page routes, Jinja templates, static JS page modules, `main.css` page styles, the React/Babel brief builder, and Chart.js. Preserve auth, users, roles, admin and still-used JSON APIs, migrations, ingestion, processing, and all database tables and backend modules.
- Add a new minimal shell with Overview, Desks, Pairs, Calendar, News, Positioning, Central Banks, and Data navigation; Syne and DM Mono dark design tokens; ECharts as the only chart library; placeholder routes; and restyled working login/setup.
- This amendment supersedes the earlier `pages.py` empty-score fallback edit for Steps 1–3. The replacement service should handle empty primary scores when the new design consumes Macro State data.

### Dedup key fallback decision (2026-10-03)

- Use `COALESCE(period_start_date::text, period, released_at::date::text)` in release selection **and** superseded-score cleanup. The release-date fallback preserves prints when both period fields are null.
- Re-run and report the read-only identity diff before changing the key. Investigate the 23 both-null indicators and write `REPORT_NULL_PERIODS.md`; report only, with no ingestion change.
- Finish the incremental job, release-to-score integration checkpoint, and production one-off with `--dry-run`. The hard-coded FED date test failure is known and out of scope.
