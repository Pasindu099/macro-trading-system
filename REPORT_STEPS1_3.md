# Steps 1–3 completion report — 2026-10-03

## Changed backend areas

- `app/processing/event_innovation.py`: approved three-level dedup key, indicator-scoped full-history loading, and affected-indicator discovery.
- `app/services/event_innovation_jobs.py`: incremental planning, superseded-score and stale-bundle cleanup, advisory-locked run with overlap watermark and timeout.
- `app/services/event_innovation_rebuild.py` and `scripts/rebuild_event_innovation.py`: non-truncating production reconciliation and read-only preview counts.
- `app/ingestion/scheduler.py`: post-commit Event Innovation hooks and daily 22:30 UTC Macro State chain.
- `app/processing/{macro_dataset,macro_features,cb_preferred_score,macro_indices,currency_stance}.py`: scheduled export opt-out, transaction timeout, and empty-output rollback. `app/services/macro_state_jobs.py` runs the dependency chain with per-step logging.
- `app/services/job_lock.py`, `app/services/job_status.py`, `app/ingestion/run_logger.py`, `app/api/routes/admin.py`, `app/db/session.py`, `app/db/models.py`: advisory locks, job run statuses and endpoint, health exclusion, cancellation rollback, and watermark model.
- Migration: `migrations/versions/2026_10_03_0021_add_job_watermarks.py` (adds `job_watermarks`). No existing migration was changed.
- Tests: `tests/unit/test_event_innovation_jobs.py`, `tests/integration/test_jobs_release_score.py`, `tests/integration/test_jobs_status.py`.

No page, template, or static UI file was changed. Scoring formulas, bundle configuration, decay parameters, rate-probability code, and `news_pipeline/` were left unchanged.

## Verification

- Three-level identity diff: **0** changed row keys, 17,734 prints before and after, **0** collapsed prints. `REPORT_NULL_PERIODS.md` documents all 23 indicators with both period fields null and the separate ingestion matching issue.
- Unit suite: **260 passed, 7 skipped, 1 known out-of-scope failure** (`tests/unit/test_rate_probability.py:182`, hard-coded FED date).
- `docker compose exec app pytest tests/integration -k jobs`: **2 passed**. A transaction-scoped fixture release produced a score; a revision replaced the prior score. The status endpoint displayed a logged job run with its row count.
- Full local Macro State chain exited successfully. Steps over the 10-second lock threshold: feature layer **85.93s**, CB-preferred score **154.86s**, macro indices **33.47s**, currency stance **154.17s**. Processed dataset did not trigger the threshold warning. These durations support revisiting staging tables later, as the approved decision anticipated.
- Local compose database was upgraded to migration `0021_job_watermarks` for integration checks.
- Local one-off preview: 483 score inserts, 17,251 score updates on conflict, 57 orphan score deletes, 12 bundle inserts, 677 bundle updates on conflict, 1 stale bundle delete. These are compose data counts, not a production forecast.

## Production commands

After deploying the commit and running the migration:

```bash
docker compose exec app alembic upgrade head
docker compose exec app python -m scripts.rebuild_event_innovation --dry-run --key-diff
docker compose exec app python -m scripts.rebuild_event_innovation
docker compose exec app pytest tests/integration -k jobs
```

The production operator runs the dry-run first, reviews its counts, then runs the real command. Neither command uses truncation. The full rebuild and incremental job use the same advisory lock.

## Deferred operational notes

- Compose still runs uvicorn with `--reload` and bind-mounts the repo; this was explicitly out of scope.
- No standalone index was added to `indicator_releases.retrieved_at`; the approved estimate was about 50,000 rows and this was explicitly deferred.
- The null-period ingestion matching issue is report-only for this step. See `REPORT_NULL_PERIODS.md`.
