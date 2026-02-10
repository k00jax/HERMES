#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLIENT="$ROOT_DIR/linux/logger/client.py"
SOCK="/tmp/hermesd.sock"
DB_PATH="$HOME/hermes-data/db/hermes.sqlite3"

if [[ ! -S "$SOCK" ]]; then
  echo "Daemon socket not found: $SOCK" >&2
  exit 2
fi

if [[ ! -f "$DB_PATH" ]]; then
  echo "DB not found: $DB_PATH" >&2
  exit 2
fi

python3 "$CLIENT" oled-status >/dev/null
sleep 1

count=$(sqlite3 "$DB_PATH" "select count(*) from oled_status where julianday(ts_utc) >= julianday('now','-5 seconds');")
if [[ "$count" -gt 0 ]]; then
  echo "PASS: ACK recorded"
  sqlite3 "$DB_PATH" "select ts_utc,stack,page,focus,debug,screen from oled_status order by id desc limit 1;"
  exit 0
fi

echo "FAIL: no ACK recorded" >&2
exit 1
