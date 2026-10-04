#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! -f $1 ]]; then
  echo "Usage: scripts/restore_db.sh <pg_dump-custom-format-file>" >&2
  exit 2
fi
archive=$(realpath "$1")
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
compose=(docker compose -f "$root/docker-compose.prod.yml")
"${compose[@]}" exec -T postgres pg_restore -l < "$archive" > /dev/null
database=${RESTORE_DB:-$("${compose[@]}" exec -T postgres sh -c 'printf %s "$POSTGRES_DB"')}
printf 'Restore %s into database %s? This replaces matching database objects.\n' "$archive" "$database"
printf 'Type RESTORE %s to continue: ' "$database"
IFS= read -r answer
if [[ $answer != "RESTORE $database" ]]; then
  echo 'Restore cancelled.' >&2
  exit 1
fi
"${compose[@]}" exec -T postgres sh -c 'exec pg_restore -U "$POSTGRES_USER" -d "$1" --clean --if-exists --no-owner --no-acl' sh "$database" < "$archive"
printf 'Restored %s into %s.\n' "$archive" "$database"
