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

Check: `alembic current` ends at `0027_situation_episodes`; app and Postgres become healthy, `/health` returns `{"status":"ok"}`. From the last known deployed `0026_country_fiscal_observations`, migration `0027_situation_episodes` applies. If the current revision is earlier, the chain is `0023_cot_positions` → `0024_cb_projections` → `0025_fred_observations` → `0026_country_fiscal_observations` → `0027_situation_episodes` (plus any intervening revisions shown by Alembic). Stop if the revision is unexpected.

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
| Fed SEP | `dc exec -T app python -c 'import asyncio; from app.services.fed_projections import load_sep_rounds; print(asyncio.run(load_sep_rounds()))'` | Loaded rounds from 2020; inspect rejected rounds; 3–15 min; 0 EODHD calls. |
| ECB projections and EER | **Unavailable:** Step 9 Parts C/E loaders are absent at this release. There is no valid command. Do not claim these data are populated; EUR tracking, gap and EER remain unavailable. | Stop here if these are deployment prerequisites. 0 calls. |
| Situations | `dc exec -T app python -m scripts.backtest_situations` then `dc exec -T app python -c 'import asyncio; from app.services.situations import run_situations_job; print(asyncio.run(run_situations_job()))'` | Backtest file and episode count; daily job persists episodes. Baseline local run: 174 episodes, 9 active; 2–15 min; 0 calls. |

EODHD budget: ≤74 yield + ≤60 FX + ~1,660 calendar = **~1,794 calls** for this runbook, plus cap-split retries and routine scheduler traffic. This is far below 50,000/day. If calendar reports many cap splits, stop at 40,000 calls and resume the next day. FRED, Fed/CFTC and Eurostat traffic do not consume EODHD calls.

## 6. Smoke checks

Open these in an authenticated browser session (API endpoints require viewer/admin as configured):

| URL | Expected result |
|---|---|
| `/desks/USD`, `/desks/EUR` | HTTP 200; verdict, active situations and scenarios load; EUR ECB/EER fields explicitly unavailable until Step 9. |
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
