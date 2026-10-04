#!/usr/bin/env bash
set -euo pipefail

root=${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
compose_file=${COMPOSE_FILE:-"$root/docker-compose.prod.yml"}
backup_dir=${BACKUP_DIR:-"$root/backups"}
mkdir -p "$backup_dir"
archive="$backup_dir/macro-dashboard-$(date -u +%Y%m%dT%H%M%SZ).dump"
tmp="${archive}.partial"
trap 'rm -f "$tmp"' EXIT

docker compose -f "$compose_file" exec -T postgres \
  sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$tmp"
test -s "$tmp"
mv "$tmp" "$archive"
trap - EXIT

# Lexical order matches creation order for UTC timestamped archive names.
find "$backup_dir" -maxdepth 1 -type f -name 'macro-dashboard-*.dump' -print \
  | sort -r | tail -n +15 | while IFS= read -r old; do rm -- "$old"; done
printf 'Backup: %s (%s bytes)\n' "$archive" "$(wc -c < "$archive" | tr -d ' ')"
