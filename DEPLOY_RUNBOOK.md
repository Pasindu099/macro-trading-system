# ForexCompass v2.0 desks — operator runbook

Run these commands **on the VPS yourself** from the repository root. This runbook has not been run against production. Keep the backup path until smoke checks pass. Do not use `--truncate` or `docker compose down -v`.

## Preflight (before step 1)

- From the workstation, run `ssh root@178.104.249.87 'mkdir -p /tmp/forexcompass-deploy'` then `scp scripts/backup_db.sh scripts/restore_db.sh root@178.104.249.87:/tmp/forexcompass-deploy/`. Keep these files outside the checkout so `git switch` can replace the paths. On the VPS, confirm both files exist.
- In the VPS checkout, confirm `pwd`, `git status --short`, `docker compose -f docker-compose.yml ps`, and free disk (`df -h`). Stop if there are unexpected local edits or insufficient room for an image plus two dumps.
- The release tag must first be pushed from the workstation: `git push origin v2.0-desks`. Confirm `git ls-remote --tags origin v2.0-desks` prints its commit.
- Retain the current database credentials in a mode-600 `.env` with `POSTGRES_PASSWORD`, `AUTH_SECRET_KEY`, `EODHD_API_KEY`, and `FRED_API_KEY`; `POSTGRES_PASSWORD` must match the **existing** Postgres volume. The production Compose file reads these values. Do not print secrets.

## 1. Back up the current database

```bash
REPO_ROOT="$PWD" COMPOSE_FILE=docker-compose.yml bash /tmp/forexcompass-deploy/backup_db.sh
BACKUP=$(ls -1t backups/macro-dashboard-*.dump | head -1)
test -s "$BACKUP" && echo "$BACKUP" && du -h "$BACKUP"
git rev-parse HEAD > backups/pre-v2-revision.txt
docker inspect -f '{{.Config.Image}}' macro_dashboard_app > backups/pre-v2-image-name.txt
docker image tag "$(docker inspect -f '{{.Image}}' macro_dashboard_app)" macro-dashboard:pre-v2
```

Check: record the exact `$BACKUP` path and size; `docker compose -f docker-compose.yml exec -T postgres pg_restore -l < "$BACKUP" | tail -1` succeeds. Confirm the previous revision and image name files are nonempty. The script keeps the newest 14 local dumps.

Daily VPS cron (replace the path with the actual absolute checkout path): `15 2 * * * cd /absolute/path/to/macro-dashboard && /usr/bin/bash scripts/backup_db.sh >> /var/log/macro-dashboard-backup.log 2>&1`. Check the next day's file and size. Backups also need off-host copy for host-loss recovery.

## 2. Fetch and check out the release

```bash
git fetch --tags origin
git switch --detach v2.0-desks
git rev-parse HEAD
git status --short
```

Check: HEAD equals `git rev-parse v2.0-desks`; no tracked edits. The ignored `backups/` directory remains. Confirm `docker-compose.prod.yml` exists.

## 3. Build image and start dependencies

```bash
docker compose -f docker-compose.yml down
docker compose -f docker-compose.prod.yml build app news_pipeline
docker compose -f docker-compose.prod.yml up -d postgres redis
docker compose -f docker-compose.prod.yml ps
```

Check: Postgres and Redis are healthy; the named Postgres volume is still `macro_dashboard_postgres_data`. No `--reload`, no source-code mount. The app has one Uvicorn worker and one APScheduler instance; news_pipeline has its own separate single-worker scheduler. **Do not start app before migration.**

## 4. Apply migrations, then start services

```bash
docker compose -f docker-compose.prod.yml run --rm --no-deps app alembic current
docker compose -f docker-compose.prod.yml run --rm --no-deps app alembic upgrade head
docker compose -f docker-compose.prod.yml up -d app news_pipeline telegram_bot
docker compose -f docker-compose.prod.yml ps
curl -fsS http://127.0.0.1:8000/health
```

Check: `alembic current` ends at `0028_ecb_series_observations`; app and Postgres become healthy, `/health` returns `{"status":"ok"}`. From the last known deployed `0026_country_fiscal_observations`, migrations `0027_situation_episodes` → `0028_ecb_series_observations` apply. If the current revision is earlier, the chain is `0023_cot_positions` → `0024_cb_projections` → `0025_fred_observations` → `0026_country_fiscal_observations` → `0027_situation_episodes` → `0028_ecb_series_observations` (plus any intervening revisions shown by Alembic). Stop if the revision is unexpected.

## 5. Backfill data in dependency order

Set `dc(){ docker compose -f docker-compose.prod.yml "$@"; }` in the VPS shell. Each step is restartable or idempotent. Review dry-run output before its real command. Run long commands in a persistent terminal; check only final counts.

| Order | Commands (run in order) | Check; expected time; EODHD calls |
|---|---|---|
| Yields, including FR and 30Y | `dc exec -T app python -m scripts.backfill_government_yields --start 2023-08-21 --end "$(date -u +%F)" --dry-run --summary-only` then repeat without `--dry-run` | `requests_used` ≤72 and FR 2Y/10Y plus available 30Y rows; 5–20 min; ≤74 calls including two listings. |
| FX 28 pairs + USD/SEK | `dc exec -T app python -m scripts.backfill_fx_spot --start 2010-01-01 --end "$(date -u +%F)" --dry-run --summary-only` then repeat without `--dry-run` | 29 pairs, synthetic count where provider lacks a pair, no errors; 5–20 min; ≤60 calls including listings and dry-run history. |
| Derived rates + DXY | `dc exec -T app python -m scripts.run_rates_derived` | Spreads, curves, drivers and DXY row counts nonzero; 2–15 min; 0 calls. |
| COT | `dc exec -T app python -m scripts.backfill_cot_tff --start-year 2010` | `table_rows` and `weeks` nonzero; 5–20 min; 0 EODHD calls. |
| FRED | `dc exec -T app python -c 'import asyncio; from datetime import date; from app.services.fred import ingest_fred_series; print(asyncio.run(ingest_fred_series(start=date(2020,1,1))))'` | Eight series, including Brent/VIX/SP500, show rows; 1–5 min; 0 EODHD calls. |
| Calendar, month by month | `dc exec -T app python -m scripts.backfill_calendar_gaps` then `dc exec -T app python -m scripts.backfill_calendar_gaps --apply` | Inspect `missing`, `errors`, and `chunks_hitting_cap`; ~20–60 min **each**; ~830 calls each for Jan 2020–Nov 2026, more if a month hits the 1,000-event cap. |
| Null-period repair | `dc exec -T app python -m scripts.repair_null_period_latest --dry-run` then repeat without `--dry-run` | Review rows changed by indicator; repeat dry-run should show zero; 1–5 min; 0 calls. |
| Event Innovation | `dc exec -T app python -m scripts.rebuild_event_innovation --dry-run --key-diff` then repeat without `--dry-run --key-diff` | Counts and `rows_written` reasonable; dedup collapses zero; 5–30 min; 0 calls. |
| Macro State | `dc exec -T app python -c 'import asyncio; from app.services.macro_state_jobs import run_macro_state_chain; asyncio.run(run_macro_state_chain())'` | Five `job:macro_state:*` runs succeed with nonzero output; 5–30 min; 0 calls. |
| Dominance, two-year backfill + today's run | `dc exec -T app python -c 'import asyncio; from app.services.dominance import run_dominance_job; print(asyncio.run(run_dominance_job()))'` | On an empty snapshot table the job starts 730 days back, fills through today, and logs `job:dominance`; inspect 20-day shares and newest snapshot date; 0 EODHD calls. |
| Situations rerun | `dc exec -T app python -m scripts.backtest_situations` then `dc exec -T app python -c 'import asyncio; from app.services.situations import run_situations_job; print(asyncio.run(run_situations_job()))'` | Backtest file and episode count; daily job persists episodes after all source and Macro State inputs are loaded; 2–15 min; 0 calls. |
| Fed SEP | `dc exec -T app python -c 'import asyncio; from app.services.fed_projections import load_sep_rounds; print(asyncio.run(load_sep_rounds()))'` | Loaded rounds from 2020; inspect rejected rounds; 3–15 min; 0 EODHD calls. |
| ECB projections | `dc exec -T app python -m scripts.load_ecb_projections` | ECB MPD rounds from 2020, four variables; ~3–15 min; 0 EODHD calls. If ECB API times out, stop and retry later; do not claim populated data. |
| ECB HICP index | `dc exec -T app python -m scripts.load_ecb_hicp` | Seasonally adjusted HICP index rows for tracking; ~1–5 min; 0 EODHD calls. |
| EUR nominal EER | `dc exec -T app python -m scripts.load_eur_eer` | Daily broad EUR EER (`EXR.D.E03.EUR.EN00.A`) rows; ~1–5 min; 0 EODHD calls. EUR/USD is the labelled fallback until loaded. |

EODHD budget: ≤74 yield + ≤60 FX + ~1,660 calendar = **~1,794 calls** for this runbook, plus cap-split retries and routine scheduler traffic. This is far below 50,000/day. If calendar reports many cap splits, stop at 40,000 calls and resume the next day. FRED, Fed/CFTC and Eurostat traffic do not consume EODHD calls.

## 6. Smoke checks

Open these in an authenticated browser session (API endpoints require viewer/admin as configured):

| URL | Expected result |
|---|---|
| `/desks/USD`, `/desks/EUR` | HTTP 200; USD unchanged. EUR shows EER or labelled EUR/USD fallback, ECB projections/tracking/gap after loaders, and labelled German-yield horizon pricing without meeting odds. |
| `/api/rate-prob/ecb-horizons` | HTTP 200; method `german_yield_approximation`, 3M/6M/12M horizons and 12M 1W/1M repricing, or explicit unavailable if no recent German yields. |
| `/api/admin/jobs/status` | HTTP 200, scheduler jobs listed, recent runs visible; admin role required. |
| `/api/rates/regimes` | HTTP 200, available 2Y-vs-10Y regimes and honest unavailable tenors. |
| `/api/positioning/crowding` | HTTP 200, currency rows and COT dates. |
| `/api/cb/FED/tracking` | HTTP 200, status and source dates; unavailable is explicit if SEP/FRED data missing. |
| `/api/verdict/USD` | HTTP 200, bias, conviction, thesis, dominant driver, main risk and every `why` contribution. |

Check `dc ps` again: app and postgres healthy; `dc logs --tail 40 app` has no repeated startup errors.

## 7. Roll back if smoke checks fail

```bash
dc(){ docker compose -f docker-compose.prod.yml "$@"; }
dc stop app news_pipeline telegram_bot
bash scripts/restore_db.sh "$BACKUP"            # type the printed RESTORE confirmation
dc down                                         # preserves named volumes
git switch --detach "$(cat backups/pre-v2-revision.txt)"
docker image tag macro-dashboard:pre-v2 "$(cat backups/pre-v2-image-name.txt)"
docker compose -f docker-compose.yml up -d --no-build
docker compose -f docker-compose.yml ps
curl -fsS http://127.0.0.1:8000/health
```

Check: previous app image and revision are active; Postgres data matches the saved dump; old `/health` responds. Never delete the backup until the restored app is verified.

## P0 review release (run only after approval)

This sequence applies the P0 code, the three future-JP quarantines, the 85-row EU split, the 15 `is_latest` corrections, and the recalibrated five-stage Macro State build. No schema migration was added. The repair is idempotent: `scripts/repair_p0_macro_inputs.py` previews affected IDs, and its second dry-run should report zero future actuals and zero EU rows to split. Verify counts against `REPORT_P0_FIX.md` and `REPORT_P0_REVIEW.md`; stop if they differ materially. All commands below are for the VPS operator, from the checkout directory.

On the workstation, after reviewing the local commit, publish its branch with `git push origin HEAD:refs/heads/p0-review`. On the VPS:

```bash
set -e
dc(){ docker compose -f docker-compose.prod.yml "$@"; }
git status --short
mkdir -p backups
git rev-parse HEAD > backups/pre-p0-revision.txt
docker inspect -f '{{.Config.Image}}' macro_dashboard_app > backups/pre-p0-image-name.txt
docker image tag "$(docker inspect -f '{{.Image}}' macro_dashboard_app)" macro-dashboard:pre-p0
bash scripts/backup_db.sh
BACKUP=$(ls -1t backups/macro-dashboard-*.dump | head -1)
test -s "$BACKUP" && du -h "$BACKUP"
dc stop app news_pipeline telegram_bot
git fetch origin refs/heads/p0-review:refs/remotes/origin/p0-review
git switch --detach origin/p0-review
export APP_IMAGE_TAG=p0-review
dc build app
dc run --rm --no-deps app python -m scripts.repair_p0_macro_inputs --dry-run
dc run --rm --no-deps app python -m scripts.repair_p0_macro_inputs --apply
dc run --rm --no-deps app python -m scripts.repair_p0_macro_inputs --dry-run
dc run --rm --no-deps app python -c 'import asyncio; from app.services.macro_state_jobs import run_macro_state_chain; asyncio.run(run_macro_state_chain())'
dc up -d --no-build app news_pipeline telegram_bot
dc ps
curl -fsS http://127.0.0.1:8000/health
```

Check before `--apply`: exactly three future JP actuals and 85 EU survey rows are expected from the production clone; the repair should report 15 corrected latest flags. Each of the five `job:macro_state:*` runs must end `success`; inspect `/api/admin/jobs/status` and both desk dates/scores. The chain takes roughly 10–15 minutes on the local clone and uses zero EODHD calls. Save the printed `$BACKUP` path and the pre-P0 revision/image files. Do not use `--truncate`.

If any repair or smoke check fails, stop the app and restore the exact pre-P0 dump:

```bash
dc stop app news_pipeline telegram_bot
bash scripts/restore_db.sh "$BACKUP"  # type the requested RESTORE confirmation
git switch --detach "$(cat backups/pre-p0-revision.txt)"
PRE_APP_IMAGE=$(cat backups/pre-p0-image-name.txt)
docker image tag macro-dashboard:pre-p0 "$PRE_APP_IMAGE"
export APP_IMAGE_TAG="${PRE_APP_IMAGE#macro-dashboard:}"
dc up -d --no-build app news_pipeline telegram_bot
dc ps
curl -fsS http://127.0.0.1:8000/health
```

Check: restored DB row counts and the old desk scores match the backup, and the active app image is the saved pre-P0 image. Keep the dump until the rollback or deployment is verified.

## v2.2-calendar release (Step 11)

Adds the calendar, event panel, Event Log and CN calendar-only data. Expected Alembic path from `v2.1-overview`: `0029_dominance_snapshots` → `0030_event_reaction_stats` → `0031_event_log_entries`. Migration 0031 copies `ai_interpretation` into `ai_note_raw` and `ai_note_final`, then drops the old column. **Order matters:** seed `countries` before starting the app. The app's scheduled ingestion now includes CN and fails on a foreign key without the CN row. Commands run on the VPS from the checkout directory.

```bash
set -e
dc(){ docker compose -f docker-compose.prod.yml "$@"; }
git status --short
mkdir -p backups
git rev-parse HEAD > backups/pre-v2.2-revision.txt
docker image tag "$(docker inspect -f '{{.Image}}' macro_dashboard_app)" macro-dashboard:pre-v2.2
bash scripts/backup_db.sh
BACKUP=$(ls -1t backups/macro-dashboard-*.dump | head -1); test -s "$BACKUP" && du -h "$BACKUP"
git fetch --tags origin
git switch --detach v2.2-calendar
export APP_IMAGE_TAG=v2.2-calendar
dc build app
dc stop app news_pipeline telegram_bot
# 1. Migrations: current must be 0029_dominance_snapshots; head must be 0031_event_log_entries
dc run --rm --no-deps app alembic current
dc run --rm --no-deps app alembic upgrade head
dc run --rm --no-deps app alembic current
# 2. Countries seed (expects "Inserting country CN", 1 insert/update)
dc run --rm --no-deps app python -m scripts.seed_countries
# 3. Start services
dc up -d --no-build app news_pipeline telegram_bot
dc ps
curl -fsS http://127.0.0.1:8000/health
# 4. CN re-ingestion (45 days back, 14 forward; a few EODHD calls), then map stored CN rows
dc exec app python -m scripts.run_manual_ingest --country CN
dc exec app python -m scripts.reclassify_unmapped --country CN
# 5. First event reaction stats run (nightly afterwards at 00:20 UTC; seconds, zero EODHD calls)
dc exec app python -c 'import asyncio; from app.services.event_reactions import run_event_reaction_stats_job as j; print(asyncio.run(j()))'
```

Checks: the ingest summary prints inserted/unmapped counts with no `ForeignKeyViolation`. After reclassify, CN trade rows (Exports, Imports, Balance of Trade Yuan) are mapped. Locally this gave 20 mapped / 51 unmapped. The stats job prints a row count above 1,000 (local: 1,206), and `/api/admin/jobs/status` shows `job:event_reaction_stats` `success`. After the next Macro State chain (22:30 UTC, or run it as in the P0 section), this must return `0`:

```bash
dc exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "select count(*) from processed.macro_observations where country_code = '"'"'CN'"'"'"'
```

Smoke checks in an authenticated browser. Unauthenticated requests redirect to `/login`.

| URL | Expected result |
|---|---|
| `/calendar` | HTTP 200; defaults to today; day groups load progressively; High filter shows impact labels and amber/blue surprise chips. |
| `/calendar?preset=last_30_days&impact=high` | Past days load with actuals and σ chips; a CN day shows one "China trade balance" row with components. |
| Click any row | Event panel: header figures, why-it-matters, History chart with Revised/First print toggle, Components, Reactions headed "Release-day move (daily close, not intraday)". Below 1024 px it opens as a full-screen sheet with ×. |
| `/calendar/event-log` | HTTP 200; filters (currency, dates, type, traded); "New entry" opens the form. |
| `/calendar/event-log/new?release_id=<id>` | Linked event shown, related pairs pre-ticked, release-day moves auto-filled; save redirects to edit with "Saved." Delete asks for confirmation. |
| `/event-log` | 307 redirect to `/calendar/event-log`; nav "Calendar → Event Log" points there. |
| `/api/admin/event-log` | HTTP 200 (admin); existing notes keep `ai_interpretation` (now the final note). |

Event Log writes need the analyst role. "Generate AI note" calls OpenAI only on click. An API error (e.g. no credits) appears in the form and is not a deploy failure.

Rollback: stop services, restore the dump, then return to the saved revision and image. Do not use `alembic downgrade` if free-text Event Log entries exist: downgrading 0031 deletes them.

```bash
dc stop app news_pipeline telegram_bot
bash scripts/restore_db.sh "$BACKUP"  # type the requested RESTORE confirmation
git switch --detach "$(cat backups/pre-v2.2-revision.txt)"
export APP_IMAGE_TAG=pre-v2.2
dc up -d --no-build app news_pipeline telegram_bot
curl -fsS http://127.0.0.1:8000/health
```

## v2.3-cb-terminal release (Step 12)

Adds the Central Bank Terminal (`/central-banks`), BoE/BoC/SNB projection loaders, policy statements and statement diffs, the speech pipeline with investingLive attribution, word-list tone (v1 and v2) behind a precision gate, `/admin/cb-imports`, the desk Fed/ECB view changes and Event Log price moves. Expected Alembic path from `v2.2.x`: `0031_event_log_entries` → `0032_cb_projection_point_stats` → `0033_cb_manual_imports` → `0034_cb_speeches` → `0035_cb_sentence_scores` → `0036_event_log_moves`. Config changes: `cb_meetings.yaml` (corrected 2026–27 dates for BoE, RBA, BoC, SNB, RBNZ, Fed June 2027; BoJ Summary of Opinions dates; RBNZ MPS meetings), `central_banks.yaml` (all banks verified, blackout rules, `manual_import` sources), new `cb_officials.yaml` and `tone_lexicon.yaml`. New optional setting `CONTACT_EMAIL` goes into the identifying User-Agent (`ForexCompass/1.0 (research dashboard; <email>)`); add it to `.env` before building. No EODHD calls; official bank sites are fetched with ~1 s between requests.

```bash
set -e
dc(){ docker compose -f docker-compose.prod.yml "$@"; }
git status --short
mkdir -p backups
git rev-parse HEAD > backups/pre-v2.3-revision.txt
docker image tag "$(docker inspect -f '{{.Image}}' macro_dashboard_app)" macro-dashboard:pre-v2.3
bash scripts/backup_db.sh
BACKUP=$(ls -1t backups/macro-dashboard-*.dump | head -1); test -s "$BACKUP" && du -h "$BACKUP"
grep -q '^CONTACT_EMAIL=' .env || echo 'CONTACT_EMAIL=' >> .env   # fill in the address, or leave blank
git fetch --tags origin
git switch --detach v2.3-cb-terminal
export APP_IMAGE_TAG=v2.3-cb-terminal
dc build app
dc stop app news_pipeline telegram_bot
# 1. Migrations: current must be 0031_event_log_entries; head must be 0036_event_log_moves
dc run --rm --no-deps app alembic current
dc run --rm --no-deps app alembic upgrade head
dc run --rm --no-deps app alembic current
# 2. Meeting calendar: the startup seed only fills an empty table, so sync the corrected dates (rerun must print 0/0/0)
dc run --rm --no-deps app python -m scripts.sync_cb_meetings --dry-run
dc run --rm --no-deps app python -m scripts.sync_cb_meetings
dc run --rm --no-deps app python -m scripts.sync_cb_meetings --dry-run
# 3. Start services (registers cb_statements decision-day runs, cb_projections 07:30, cb_speeches 06:30 UTC)
dc up -d --no-build app news_pipeline telegram_bot
dc ps
curl -fsS http://127.0.0.1:8000/health
# 4. Projection rounds since 2020 (BoE, BoC, SNB; BoJ/RBA/RBNZ log manual_import) ~3 min
dc exec -T app python -m scripts.load_cb_projections | tee backups/v2.3-projections.log
# 5. Policy statements, 13 months (FED, ECB, BoE, BoJ, BoC, SNB; RBA/RBNZ log manual_import) ~2 min
dc exec -T app python -m scripts.load_cb_statements | tee backups/v2.3-statements.log
# 6. Speeches, 12 months, then investingLive comment attribution ~5-10 min
dc exec -T app python -m scripts.load_cb_speeches | tee backups/v2.3-speeches.log
# 7. First tone scoring run (lexicon_v1 and lexicon_v2; daily afterwards inside job:cb_speeches)
dc exec -T app python -c 'import asyncio; from app.services.cb_tone import score_pending; print(asyncio.run(score_pending()))'
```

Checks (local reference counts in brackets): projections BOE 17 rounds, BOC 7, SNB 27 [same], BOJ/RBA/RBNZ `manual_import`; statements FED 9, ECB 8, BOE 9, BOJ 9, BOC 9, SNB 5; speeches FED ~111, ECB ~86, BOE 50, BOJ 26, BOC 10, SNB ~9, RBA/RBNZ `manual_import`; the `comments` line depends on how much investingLive history production holds (local 143). Scoring prints the same count for both methods (local 16,853). `/api/admin/jobs/status` shows `job:cb_projections:RBA`, `:RBNZ`, `:BOJ`, `job:cb_statements:RBA`/`:RBNZ` and `job:cb_speeches:RBA`/`:RBNZ` as `manual_import`, not failed. A rerun of steps 4-6 is idempotent (no duplicate rows).

Smoke checks in an authenticated browser:

| URL | Expected result |
|---|---|
| `/central-banks` | HTTP 200; FED tab: summary (rate 4.00%, regime, next meeting, blackout chip, priced move, 2Y − policy, gap), committee balance, statement tracker with struck/new sentences, speaker table, profile, upcoming, headlines, "Search and Ask — Phase 3, pending". No panel shows "could not load". |
| `/central-banks?bank=FED&view=projections` | Revisions with median/CT/range, dots vs market, risk balance, CB Tracking, vintages, formats table. |
| `/central-banks?bank=RBNZ` and `?bank=RBA` | Statement tracker and projections show "Manual import source … /admin/cb-imports"; freshness rows read "manual import — never imported". |
| `/central-banks?bank=SNB&view=projections` | Point projections with "policy rate held at 0.0%". |
| `/admin/cb-imports` | Admin only; lists BOJ projections and RBA/RBNZ projections, statements, speeches with "last updated never"; file upload and value-entry forms. |
| `/desks/USD`, `/desks/EUR` (section 3) | "Statement language tracker", latest statements, "Committee balance · 12 weeks" with "Tone: pending AI layer", "Speaker focus mix · v1" bar. |
| `/` Central bank regime map | Guidance column adds "statement changed <date> (n sentences)" where a diff exists. |
| `/calendar/event-log/new` | No Trade section; "Price moves after the release" grid (1m/5m/30m/1h/4h); instruments include XAU/USD, US100, US30, US500. |

Tone is hidden everywhere until the labelled sample passes the gate (`config/tone_lexicon.yaml` `display_gate`, ≥ 75 % precision on ≥ 15 labels). Labels live in `REPORT_STEP12_TONE_SAMPLE.md`, which ships in the image. Label, commit and release again to enable tone.

**After deploy — manual imports (blocked or PDF-only sources).** RBA (403 to scripted clients), RBNZ (robots.txt 403, Cloudflare) and BoJ projections (PDF only) are filled by hand at `/admin/cb-imports`. Download the official files in a normal browser yourself:
- RBNZ projections: each Monetary Policy Statement's data workbook (`mps<mon><yy>-data.xlsx`), uploaded with the MPS decision date. RBNZ statements: the OCR decision media release page saved as HTML, with the decision date.
- RBA statements: the monetary policy decision media release (HTML or PDF). RBA and BoJ projections: enter values (`variable, horizon, value`; horizons `YYYY` or `YYYYQn`) from the SMP forecast table and the Outlook Report forecast table.
- Speeches for RBA/RBNZ have no upload; those officials are covered by investingLive comment attribution.

Rollback: stop services, restore the dump, then return to the saved revision and image. Do not rely on `alembic downgrade`: 0034-0036 drop the speech, score and Event Log price-move tables.

```bash
dc stop app news_pipeline telegram_bot
bash scripts/restore_db.sh "$BACKUP"  # type the requested RESTORE confirmation
git switch --detach "$(cat backups/pre-v2.3-revision.txt)"
export APP_IMAGE_TAG=pre-v2.3
dc up -d --no-build app news_pipeline telegram_bot
curl -fsS http://127.0.0.1:8000/health
```

## v2.5-pairs release (Steps 13–15; no separate v2.4 tag)

Follow sections 1–4 (backup, checkout, build, migrate), checking out `v2.5-pairs` instead of the earlier release tag. Migration head after this release: `0043_retail_sentiment_snapshots` (adds `retail_sentiment_snapshots` and the `retail_sentiment_daily` view; no data rewritten). Step 15 adds no migration.

```bash
git switch --detach v2.5-pairs
```

**Before starting the app — Myfxbook credentials.** Add the owner's Myfxbook account to the VPS `.env` (never commit, never paste into logs or tickets):

```bash
# in /opt/macro-dashboard/.env (or the deploy .env), values without quotes
MYFXBOOK_EMAIL=
MYFXBOOK_PASSWORD=
```

Both compose files pass these to the app. Without them the 4-hourly `myfxbook_retail` job records "skipped" and the retail panels read "Myfxbook credentials not configured". OANDA/IG retail readers stay off (`RETAIL_WEB_SOURCES_ENABLED` unset).

After `dc up -d app`, run these once, in order:

```bash
# 1. One Myfxbook snapshot: prints pairs stored and the G10 pairs Myfxbook does not cover.
dc exec -T app python -c 'import asyncio; from app.services.myfxbook import run_snapshot; print(asyncio.run(run_snapshot()))' | tee backups/step14-myfxbook-first.log
# 2. S&P 500 history from EODHD (GSPC.INDX) back to 2000 + metals refresh: 3 EODHD calls.
dc exec -T app python -c 'import asyncio; from app.services.news_assets import refresh_snapshot; print(asyncio.run(refresh_snapshot()).get("SP500"))' | tee backups/step14-sp500.log
# 3. Retag stored non-release "Data" headlines (PBOC fixings, wraps, previews) as Flows & market.
dc exec -T app python -c 'import asyncio; from app.services.news_tagging import retag_data_items; print(asyncio.run(retag_data_items()))'
# 4. Re-run the widened headline parser on the last 30 days (clears only unparsed/unmatched audit rows).
dc exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DELETE FROM newswire_actuals a USING news_items n WHERE n.id = a.news_item_id AND n.published_at >= now() - interval '30 days' AND a.status IN ('unparsed', 'unmatched');"
dc exec -T app python -c 'import asyncio; from app.services.news_actuals import process_pending; print(asyncio.run(process_pending(limit=2000)))'
```

Expected: step 1 `{'status': 'ok', 'stored': N, 'missing': [...]}` (or `not_configured` if the keys are absent); step 2 about 6,700 SP500 rows, `source=eodhd`; step 4 no `discrepancy` rows.

Smoke checks in an authenticated browser:

| URL | Expected result |
|---|---|
| `/positioning` | HTTP 200; summary sentence, Data as of (Tue) / Released / Next release (CFTC schedule, 15:30 ET); crowding map with 1y/3y/5y switch; flows 1W/4W; squeeze cards; 52-week chart; past-extremes table ("too few episodes" below 5); institutions vs retail (28 pairs). No panel shows "could not load". |
| `/positioning?ccy=EUR&lookback=5y` | Sections 4–5 show EUR; crowding percentiles change with the lookback. |
| `/desks/USD` section 7, `/` currency ranking | Positioning links open `/positioning?ccy=…`. |
| `/api/positioning/summary`, `/divergence`, `/retail` | JSON 200. |
| `/pairs` | HTTP 200; 28 unique rows. Default sort is descending absolute overall score; each pair uses market convention from `config/pairs.yaml`. Search and all five group filters work. Sort Last, Policy gap, and COT in both directions; pending cells stay last. |
| `/pairs` retail column | Myfxbook long percentage says “% by volume”; long and short position counts appear separately. Missing snapshots say “pending”. |
| `/pairs` matrix view | Overview pair map and lenses load; pair links open `/pairs/{BASEQUOTE}`. |
| `/pairs/EURUSD` | HTTP 200; “Pair page pending”, back link to screener, and USD/EUR desk links. Calendar event panel and Overview pair links open the same route. |

Rollback: restore the pre-release dump as in section 7; 0043 only adds a table and a view.
