# Calendar and Event Log update — 19 October 2026

- Calendar impact colors: High red, Medium orange, Low white (`app/web/static/css/calendar.css:13`).
- Actual values use green for a currency-positive beat and red for a currency-negative miss (`app/services/calendar_service.py:29`, `app/web/templates/calendar/day.html:12`). The existing indicator direction flag and normalized surprise handle lower-is-better releases such as unemployment.
- A small lightning symbol marks surprises of at least 1σ beside Actual (`app/web/templates/calendar/day.html:12`), leaving the Surprise column numeric.
- Replaced Syne and DM Mono with Arial across the shared shell and page styles; removed the remote font request (`app/web/templates/base.html:7`).
- Event Log move entries retain up to six decimal places instead of rounding to one (`app/api/routes/event_log.py:70`, migration `migrations/versions/2026_10_19_0037_event_log_move_precision.py:1`). Apply the migration before using this form against the database.
- Existing linked notes retain the calendar release ID and copy actual, forecast, and previous values; the move table stores each instrument and 1m, 5m, 30m, 1h, and 4h horizon.
- Research limitation: this form stores signed moves only. It does not record the underlying t0 and horizon prices, actual quote timestamps, market data provider, or bid/ask spread. These fields are needed to audit execution and exit-model inputs reliably. Free-text events have a date but no exact release timestamp.
- Added focused tests for lower-is-better direction and move precision. Python syntax check passed. Pytest could not run: neither `pytest` nor the Python module is installed in this environment.
- One-time VPS setup: `scripts/setup_vps_deploy.ps1` copies a working key to the local `.ssh` directory, saves the remote checkout path there, and tests SSH access. The key remains outside OneDrive.
- Double-click `Deploy VPS.cmd` after committing source changes. It pushes the committed revision, uploads the deploy helper, and runs `scripts/deploy_vps_remote.sh` on the VPS. The helper checks the revision, backs up Postgres, tags the previous app image, builds, migrates, starts the app, and checks `/health`.
- VPS deployment completed on 19 October 2026 at commit `dbb93c4`. The durable local key lives in `.ssh` outside OneDrive; its public key is installed for VPS root and the one-click config points to `/root/macro-trading-system-v2`.
- VPS backup: `/root/macro-trading-system-v2/backups/macro-dashboard-20261019T121917Z.dump` (53,915,121 bytes). Previous app image tagged `macro-dashboard:pre-one-click`.
- Post-deploy verification: `/health` returned `{"status":"ok"}`; the app container was healthy; Alembic reported `0037_event_log_move_precision (head)`.
- Deploy-script verification: PowerShell parser passed for both scripts; Git Bash `bash -n` passed for the remote script; the one-click workflow completed on the VPS.
- Calendar correction deployed at `b0ddbd1`: Actual-column lightning marker, stronger beat/miss and impact color rules, and a versioned calendar stylesheet URL to refresh browser caches. VPS `/health` returned OK, app healthy, CSS endpoint HTTP 200. Jinja template syntax passed locally.
