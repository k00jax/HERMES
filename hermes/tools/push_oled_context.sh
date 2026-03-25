#!/usr/bin/env bash
# Push OLED,CONTEXT + OLED,TIME to hermes-logger via UNIX socket.
# Uses read-only SQLite URI, index-friendly time windows, bounded runtime, and
# a minimum gap between successful pushes to avoid starving nRF telemetry.
set -euo pipefail

HOME="${HOME:-/home/odroid}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="$HOME/hermes-data/db/hermes.sqlite3"
CLIENT="$ROOT_DIR/linux/logger/client.py"
SOCK="/tmp/hermesd.sock"

SQL_TIMEOUT_SEC="${HERMES_OLED_SQL_TIMEOUT_SEC:-45}"
MIN_GAP_SEC="${HERMES_OLED_CONTEXT_MIN_INTERVAL_SEC:-2}"
STAMP_DIR="${XDG_CACHE_HOME:-$HOME/.cache}"
STAMP_FILE="${HERMES_OLED_CONTEXT_STAMP_FILE:-$STAMP_DIR/hermes-oled-context.last}"

if [[ ! -S "$SOCK" ]]; then
  echo "Daemon socket not found: $SOCK" >&2
  exit 2
fi

if [[ ! -f "$DB" ]]; then
  echo "DB not found: $DB" >&2
  exit 2
fi

if [[ ! -f "$CLIENT" ]]; then
  echo "Client not found: $CLIENT" >&2
  exit 2
fi

# Read-only DB URI (avoids blocking writer locks from the logger).
if [[ -n "${HERMES_SQLITE_RO_URI:-}" ]]; then
  DB_RO_URI="$HERMES_SQLITE_RO_URI"
else
  DB_RO_URI="file:${DB}?mode=ro"
fi

_sql() {
  # PRAGMA may echo a line; take only the last line as the query result.
  timeout "$SQL_TIMEOUT_SEC" sqlite3 -noheader -batch "$DB_RO_URI" \
    -cmd "PRAGMA busy_timeout=60000;" \
    "$1" | tail -n 1
}

now_ts=$(date +%s)
if [[ -f "$STAMP_FILE" ]]; then
  last_ts=$(tr -d ' \t\r\n' < "$STAMP_FILE" 2>/dev/null || echo 0)
  if [[ "$last_ts" =~ ^[0-9]+$ ]] && (( now_ts - last_ts < MIN_GAP_SEC )); then
    exit 0
  fi
fi

env_count=$(_sql "select count(*) from env;")
air_count=$(_sql "select count(*) from air;")
if [[ "${env_count:-0}" -eq 0 || "${air_count:-0}" -eq 0 ]]; then
  echo "No data yet (env=$env_count air=$air_count)" >&2
  exit 1
fi

read -r -d '' _OLED_SQL <<'SQL' || true
WITH
  now_env AS (SELECT ts_utc, temp_c, hum_pct FROM env ORDER BY id DESC LIMIT 1),
  now_air AS (SELECT ts_utc, eco2_ppm, tvoc_ppb FROM air ORDER BY id DESC LIMIT 1),

  env_5m AS (
    SELECT temp_c AS temp_c_5m, hum_pct AS hum_pct_5m
    FROM env
    WHERE ts_utc >= datetime((SELECT ts_utc FROM now_env), '-5 minutes')
    ORDER BY id ASC LIMIT 1
  ),
  air_5m AS (
    SELECT eco2_ppm AS eco2_5m, tvoc_ppb AS tvoc_5m
    FROM air
    WHERE ts_utc >= datetime((SELECT ts_utc FROM now_air), '-5 minutes')
    ORDER BY id ASC LIMIT 1
  ),

  env_60m AS (
    SELECT temp_c AS temp_c_60m, hum_pct AS hum_pct_60m
    FROM env
    WHERE ts_utc >= datetime((SELECT ts_utc FROM now_env), '-60 minutes')
    ORDER BY id ASC LIMIT 1
  ),
  air_60m AS (
    SELECT eco2_ppm AS eco2_60m, tvoc_ppb AS tvoc_60m
    FROM air
    WHERE ts_utc >= datetime((SELECT ts_utc FROM now_air), '-60 minutes')
    ORDER BY id ASC LIMIT 1
  )

SELECT
  'OLED,CONTEXT'
  || ',temp_d5='  || printf('%.1f', COALESCE((SELECT temp_c FROM now_env) - (SELECT temp_c_5m FROM env_5m), 0))
  || ',rh_d5='    || printf('%.1f', COALESCE((SELECT hum_pct FROM now_env) - (SELECT hum_pct_5m FROM env_5m), 0))
  || ',eco2_d5='  || CAST(COALESCE((SELECT eco2_ppm FROM now_air) - (SELECT eco2_5m FROM air_5m), 0) AS INT)
  || ',tvoc_d5='  || CAST(COALESCE((SELECT tvoc_ppb FROM now_air) - (SELECT tvoc_5m FROM air_5m), 0) AS INT)
  || ',temp_d60=' || printf('%.1f', COALESCE((SELECT temp_c FROM now_env) - (SELECT temp_c_60m FROM env_60m), 0))
  || ',rh_d60='   || printf('%.1f', COALESCE((SELECT hum_pct FROM now_env) - (SELECT hum_pct_60m FROM env_60m), 0))
  || ',eco2_d60=' || CAST(COALESCE((SELECT eco2_ppm FROM now_air) - (SELECT eco2_60m FROM air_60m), 0) AS INT)
  || ',tvoc_d60=' || CAST(COALESCE((SELECT tvoc_ppb FROM now_air) - (SELECT tvoc_60m FROM air_60m), 0) AS INT)
;
SQL

cmd="$(_sql "$_OLED_SQL" | tr -d '\n')"

if [[ -z "$cmd" ]]; then
  echo "No command generated" >&2
  exit 1
fi

python3 "$CLIENT" send "$cmd" >/dev/null

epoch=$(date +%s)
python3 "$CLIENT" send "OLED,TIME,epoch=$epoch" >/dev/null

mkdir -p "$(dirname "$STAMP_FILE")"
date +%s > "$STAMP_FILE"

echo "sent: $cmd"
