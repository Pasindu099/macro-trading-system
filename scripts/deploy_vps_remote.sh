#!/usr/bin/env bash
set -euo pipefail

revision=${1:?Pass the committed Git revision}
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid revision' >&2; exit 2; }
[[ -f docker-compose.prod.yml && -f scripts/backup_db.sh ]] || { echo 'Run from the VPS checkout' >&2; exit 2; }
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo 'VPS checkout has local edits; stopped' >&2; exit 2; }

dc() { docker compose -f docker-compose.prod.yml "$@"; }
git fetch origin deploy-vps
[[ "$(git rev-parse FETCH_HEAD)" == "$revision" ]] || { echo 'Pushed revision does not match' >&2; exit 2; }
previous=$(git rev-parse HEAD)
mkdir -p backups
printf '%s\n' "$previous" > backups/pre-one-click-revision.txt
REPO_ROOT="$PWD" COMPOSE_FILE=docker-compose.prod.yml bash scripts/backup_db.sh
docker image tag "$(docker inspect -f '{{.Image}}' macro_dashboard_app)" macro-dashboard:pre-one-click

git switch --detach "$revision"
export APP_IMAGE_TAG="deploy-${revision:0:12}"
dc build app
dc stop app
dc run --rm --no-deps app alembic current
dc run --rm --no-deps app alembic upgrade head
dc up -d --no-build app

for attempt in {1..12}; do
    if curl -fsS --max-time 5 http://127.0.0.1:8000/health >/dev/null; then
        dc ps app
        echo "Deployed $revision; backup retained in backups/"
        exit 0
    fi
    sleep 5
done
echo 'Health check failed; backup and pre-one-click image are retained' >&2
exit 1
