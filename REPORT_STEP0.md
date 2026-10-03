# Step 0 — Read-only report: automatic refresh for Event Innovation and Macro State

_No code was changed. Everything below comes from reading the files cited._

---

## 1. Scheduler — `app/ingestion/scheduler.py`

- **Registration.** `Scheduler.start()` (`:94-230`) builds one `Canonicalizer` and one `IngestService`, then calls `AsyncIOScheduler.add_job(...)` with `replace_existing=True` and `misfire_grace_time`.
  - Only the gov-yield jobs set `max_instances=1, coalesce=True` (`:156-157,176-177`). The EODHD jobs rely on APScheduler's default `max_instances=1` per job id. **Nothing stops a session run and a post-release run from overlapping** (they have different ids).
- **EODHD session jobs.**
  - `SCHEDULED_SESSIONS` (`:75-79`): `scheduled_asia` 00:00, `scheduled_london` 09:00, `scheduled_ny` 14:00 UTC. Each calls `_run_scheduled_session(session_name)` (`:240-274`).
  - Inside `run_logger(session_name, countries)`, it loops over all `ALLOWED_COUNTRIES`, using a 45-day lookback and a 14-day forward window. For each country it opens **its own `session_scope()`** and calls `ingest_events`, which **commits per country** (`:265-268`), then calls `run.record_stats(stats)`.
  - The natural hook point is after the country loop, still inside `run_logger` (after `:274`). All commits are visible by then.
- **Post-release triggers.**
  - `_register_post_release_triggers()` (`:366-402`) reads `config/release_schedule.yaml` → `release_schedule:` list. Each entry has `name`, `country`, `scheduled_at_utc` "HH:MM", optional `trigger_delay_minutes` (default 15), `day_of_week`, `day_of_month` **or** `day_of_month_range`, and `lookback_days` (default 7).
  - `_build_cron_trigger` (`:409-449`) fires at the release time plus the delay. The job id is `post_release:{name}`.
  - `_run_post_release(entry)` (`:276-307`) fetches one country and commits once in one `session_scope` (`:299-302`). It returns early on an EODHD error (`:297`). The hook point is after `:307`, inside `run_logger`.
- **Startup.** `app/main.py:69-73` starts the scheduler when `settings.enable_scheduler` is set.
  - Compose runs **one** uvicorn process, but with `--reload` (`docker-compose.yml:60`), while `main.py:140` warns that reload breaks the scheduler.
  - Advisory locks also cover a manual script run during a scheduled run.

## 2. Ingestion commit point — `app/ingestion/ingest_service.py`

- `IngestService.ingest_events(session, raw_events, *, store_unmapped=True) -> IngestStats` (`:81-114`).
  - It **only flushes** (`:111`). The **commit happens in the caller's `session_scope()`** on exit (`app/db/session.py:90-108`: commit on success, rollback on exception).
  - It returns `IngestStats(inserted, updated, skipped_same, skipped_null_country, unmapped, unmapped_stored, errors)` (`:40-64`).
- **Revision semantics** (`_upsert_release`, `:205-256`):
  - It finds the `is_latest=True` row for `(indicator_id, period_start_date)`, falling back to `(indicator_id, period)` when the start date is null.
  - If `actual`, `estimate` and `previous` are all unchanged, the outcome is `"same"` and nothing is written.
  - Otherwise the old row gets `is_latest=False` (`:249-253`) and a **new row with a new `id`** is inserted, with `retrieved_at = now()` (`:254-256,318`).
  - **Consequences:**
    - A newly released print (EODHD lists the event in advance with `actual=NULL`) shows up as an "updated" new row.
    - **`retrieved_at` is a reliable "ingested or revised since" marker.** It is set in Python at row construction, just before commit.
    - There is **no index on `retrieved_at` alone**. The closest is `(indicator_id, period, retrieved_at DESC)` (migration `0001:148`). At about 50k rows a sequential scan is fine.

## 3. Event Innovation — `app/processing/event_innovation.py` and `scripts/build_event_innovation.py`

- **Entry point.** `build_event_innovation(session, *, config, country_code, date_from, truncate, dry_run) -> dict` (`:833-874`) chains `load_release_records` → `score_releases` → `build_bundles` → `persist`.
- **Dedup key** (`_RELEASES_SQL`, `:665-693`):
  - `DISTINCT ON (indicator_id, COALESCE(period_start_date::text, released_at::date::text))`, keeping the newest `retrieved_at`, then the highest `id`.
  - It filters `indicator_id IS NOT NULL AND actual IS NOT NULL`, optionally by country or `released_at::date >= date_from`. `release_date = released_at::date`.
  - The COALESCE fallback means a period-less row whose `released_at` date shifts between retrievals would dedup as **two** prints. Ingest matches period-less rows on `period` (the raw string), not `released_at`, so the two layers use **different keys** for that edge case.
- **Scoring** (`score_releases`, `:522-546`):
  - Records are grouped by `indicator_id` and sorted by `(release_date, release_id)`. Each print is scaled against **only the prior surprises of the same indicator**: point-in-time EWMA RMS of winsorized priors, clipped at `max_abs_normalized`.
  - `scored = importance <= scored_importance_max AND bucket assigned AND normalized is not None` (`:575-576`).
  - **Implication for incremental runs:** a print's score depends on the indicator's full prior history. So the full history of each affected indicator has to be loaded, and a date-windowed load gives **wrong scales**. The script docstring says the same (`build_event_innovation.py:10-12`).
  - Rows **earlier** than an indicator's earliest new or revised print cannot change. Rows after it can, if a revision changes a prior surprise.
- **Bundles** (`build_bundles`, `:595-657`):
  - Membership comes only from `config/bundle_config.yaml`, keyed on `(bundle_key, country, release_date)` via a `(country, canonical_name)` lookup. Below `min_bundle_members` scored members, no bundle is made.
  - The bucket comes from the heaviest member. The score is the weighted average (`bundle_score`).
  - Every member released that day gets `row.bundle_key` set, scored or not.
  - **Incremental implication:** rebuilding a bundle needs **all** members on that date, not only the new rows.
- **Persist** (`:726-830`):
  - `release_bundles` upserts on `(bundle_key, release_date)` with `RETURNING id`.
  - `event_innovation_scores` upserts **`ON CONFLICT (release_id)`**. That is the existing natural key.
  - `truncate=True` runs `TRUNCATE TABLE release_bundles CASCADE` (`:739-742`), which also wipes `event_innovation_scores` through the FK.
  - **Gap 1 (important for checkpoint 2):** a revision gets a **new `release_id`**, so the upsert inserts a second row. The superseded release's score row is **never removed** by non-truncate runs, and the panel query (`services/event_innovation_feed.py:146-154`) does not filter it out. Only `--truncate` clears these orphans today. **The existing key cannot satisfy "revised release updates its score instead of inserting a second row".**
  - **Gap 2:** a bundle whose membership drops below the minimum after a revision keeps its old `release_bundles` row.
- **Script `--truncate`.**
  - It wipes both tables, then does a full rebuild. It refuses to combine with `--country` (`:72-75`).
  - Other flags: `--config`, `--country`, `--date-from`, `--dry-run`.
  - The script makes one `session_scope`, so everything happens in **one transaction**. A failure rolls back, including the truncate.
  - A **full rebuild without `--truncate`** is safe and idempotent for live rows. It just doesn't remove superseded rows (gap 1).

## 4. Macro State — the builders behind `processed.*` tables the pages read

**Pages read only four tables:**
- `processed.cb_preferred_score` and `cb_preferred_rankings` (primary).
- `processed.currency_stance` and `currency_stance_rankings` (legacy fallback).

These are read in `_build_currency_stance_dashboard` (`pages.py:868-945`) and `_build_fundamental_currency_meter` (`pages.py:1034-1075`).

- **The fallback triggers only on an exception**, meaning a missing table, not an empty one. An empty `cb_preferred_score` renders an empty board.

**Dependency chain:**

| Order | Script (already a thin CLI wrapper) | Importable function | Reads | Writes (DROP + CREATE) |
|---|---|---|---|---|
| 1 | `scripts/build_processed_dataset.py` | `macro_dataset.build_processed_dataset(output_dir)` (`:64`) | `indicator_releases`, `indicators`, `countries`, `ingestion_runs` | `indicator_metadata`, `macro_observations`, `data_quality_issues`, `dataset_profile` (`:103-106`) |
| 2 | `scripts/build_feature_layer.py` | `macro_features.build_feature_layer(output_dir)` (`:29`) | `macro_observations`, `indicator_metadata` | `indicator_feature_map`, `indicator_features`, `headline_targets`, `lag_analysis_results`, `multicollinearity_flags`, `modeling_feature_base` (`:63-68`) |
| 3a | `scripts/build_cb_preferred_score.py` | `cb_preferred_score.build_cb_preferred_score(output_dir, config)` (`:300`) | `indicator_features` | `cb_preferred_score`, `cb_preferred_rankings`, `cb_preferred_components` (`:604-606`) |
| 3b | `scripts/build_macro_indices.py` | `macro_indices.build_macro_indices(...)` (`:37`) | `lag_analysis_results`, `multicollinearity_flags`, `indicator_features`, `indicator_feature_map` | `theme_indices`, `theme_index_components`, `low_confidence_relationships`, `relationship_weights` (`:72-75`) |
| 4 | `scripts/build_currency_stance.py` | `currency_stance.build_currency_stance_layer(output_dir, config)` (`:35`) | `theme_indices` | `currency_stance`, `currency_stance_rankings` (`:67-68`) |

Dependencies: 3a needs only step 2. 3b needs step 2. Step 4 needs 3b.

- **Transaction behaviour today.** Each builder runs **inside one `session_scope()`**: DROP, CREATE, INSERT, summary and CSV export, then commit.
  - Postgres DDL is transactional, so **a failure inside a builder already rolls back and leaves the old tables intact**.
  - But DROP takes an **ACCESS EXCLUSIVE lock** for the whole rebuild, so page requests that read those tables **block** until it commits. The duration is unmeasured because there was no DB access.
- **Bug: `cb_preferred_score.py:310-317`** returns `{"error": ...}` from **inside** `session_scope` when features or scores are empty. `session_scope` commits on a clean exit, so the DROP and CREATE are **committed with empty tables**. The page then shows an empty board (no fallback, see above).
- **Side effects:**
  - Every builder writes CSV, JSON and README exports to `data/<layer>/`. In prod that is the bind-mounted repo directory.
  - The builders `print()` progress.
  - Other `scripts/build_*.py` files (EDA, validation, modelling workbench, policy signals, production strength, etc.) feed **no page**. They are out of scope.

## 5. `ingestion_runs` — `models.py:205-247`, written via `app/ingestion/run_logger.py`

- **Columns:**
  - `id`, `started_at`, `finished_at`
  - `run_type` (free text, no CHECK constraint)
  - `countries_fetched text[]`
  - `events_inserted`, `events_updated`, `api_calls_used`
  - `errors jsonb`
  - `status` (free text: `running` / `success` / `partial` / `failed`)
  - There is **no generic "rows written" column**.
- **`run_logger(run_type, countries)`** commits a `running` row at start. On exit it finalises with `success`, `partial` (when item errors exist) or `failed`, plus `{"fatal": {...}}`, and re-raises.
  - It is reusable as-is for jobs: rows go in `events_inserted` and errors in `errors`. A `skipped` status would need a small addition.
- **Readers:**
  - `/api/admin/health` (`admin.py:122-193`) counts **all** runs and failed runs in the last 24h, so job rows will show up in those totals. The per-country "last successful run" filters on `countries_fetched` and is unaffected if job rows pass `[]`.
  - `/api/admin/ingestion-runs` (`admin.py:219-257`) filters by `run_type`.
- **Admin router prefix is `/api/admin`** (`admin.py:43`), with role checks via `require_role`.

---

## Decisions needed before Step 1

1. **Revised releases (checkpoint 2).** `ON CONFLICT (release_id)` can't do this, because a revision has a new id.
   - **(a) Recommended:** in the same transaction, delete score rows for the affected indicators whose `release_id` is no longer the dedup winner for its `(indicator_id, period key)`. No schema change, and it also clears today's orphans when run over all indicators.
   - (b) Add a `period_key` column plus a unique `(indicator_id, period_key)` in the new migration and upsert on that. This changes the `event_innovation_scores` schema.
2. **Incremental scope.** Load the **full history** of each affected indicator, plus the other members of any bundle it belongs to on the affected dates. Score it all in memory. **Persist only rows on or after each indicator's earliest new or revised print**, plus the affected bundles. Checkpoint 1 then means "never writes rows before the watermark-affected point"; it does not mean "touches only the new rows". OK?
3. **Macro State atomicity.** The builders already roll back on failure. Pick one:
   - **(a) Minimal:** keep the in-transaction DROP/CREATE, fix the `cb_preferred_score` early-return commit, and add `lock_timeout`/`statement_timeout`. Pages may block for the duration of a rebuild.
   - **(b) Recommended:** build into `processed_staging.*`, then swap with `ALTER TABLE ... SET SCHEMA` / `RENAME` in one short transaction. That means parameterising table names across 5 modules (~3.7k lines of SQL), a larger change.
4. **Scheduled-run exports.** Skip the CSV/JSON writes to `data/` in scheduled mode (add `export: bool = True` so the scripts keep current behaviour)?
5. **Endpoint path.** Use `/api/admin/jobs/status`, consistent with the existing router (recommended), or the literal `/admin/jobs/status`?
6. **Job logging.** Reuse `ingestion_runs` with `run_type` values like `job:event_innovation_incremental` and `job:macro_state:<step>`, rows in `events_inserted`, and new status values `skipped` / `timeout`. Accept that they count toward `/api/admin/health` totals?
7. **Timeout.** `asyncio.wait_for` alone leaves the Postgres statement running after cancellation. Also set `SET LOCAL statement_timeout = '15min'` inside each job transaction?
8. **Integration checkpoint 4.** The compose DB is not reachable from this machine (host `macro_dashboard_postgres` does not resolve). Should I run it inside the container (`docker compose exec app pytest ...`), or will you run it?
9. **Prod one-off.** With 1(a), the post-deploy backfill can be a full rebuild **without** `--truncate` plus orphan cleanup over all indicators. That also fixes the stale prod bundle membership.
