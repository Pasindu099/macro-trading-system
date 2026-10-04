Before deploying Step 10, prepare the deployment. Do NOT run anything
against production; I run the commands myself. Save this prompt as
DECISIONS_DEPLOY.md, commit per part.

PART A — PRODUCTION COMPOSE
- docker-compose.prod.yml (override or standalone): no --reload, no code
  bind-mount (code baked into the image), restart: unless-stopped,
  healthchecks for app and postgres, scheduler enabled in exactly one
  process.
- Confirm the image builds and starts locally with this file.

PART B — BACKUP AND RESTORE
- scripts/backup_db.sh: pg_dump (custom format) with timestamp, keep the
  last 14 locally, print the path and size.
- scripts/restore_db.sh <file>: restores into the compose Postgres, with a
  confirmation prompt.
- Test both locally (backup → restore into a scratch DB → row counts match).
- Document a daily cron line for the VPS.

PART C — DEPLOY_RUNBOOK.md
Step-by-step commands for the VPS, each with what to check after it:
1. Backup (Part B). Note the file.
2. git fetch; check out the release tag (create tag v2.0-desks on the
   current commit).
3. Build and start with the prod compose file.
4. alembic upgrade head (list the migrations it will apply).
5. Data runbook, in dependency order, each with dry-run first where it
   exists, expected duration and EODHD calls: yields + 30Y + FR backfill,
   FX 28 pairs + USD/SEK backfill, rates_derived (incl. DXY), COT backfill,
   FRED series, calendar month-by-month re-fetch (Step 7 Part F), null-
   period repair (Step 9 Part 0), Event Innovation rebuild (dry-run, then
   real), Macro State chain, SEP parser, ECB projections, EER, situations
   backtest + daily job.
6. Smoke tests: list of URLs/endpoints to open and what each must show
   (desks USD and EUR, /api/admin/jobs/status, /api/rates/regimes,
   /api/positioning/crowding, /api/cb/FED/tracking, /api/verdict/USD).
7. Rollback: previous image/tag + restore_db.sh, exact commands.
- Total EODHD calls for the whole runbook (must stay well under 50k/day;
  if not, split across days and say how).

DELIVER: 10-line status + the runbook file path.
