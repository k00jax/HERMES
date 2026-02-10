#!/usr/bin/env bash
set -euo pipefail

HOME="${HOME:-/home/odroid}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="$HOME/hermes-data/db/hermes.sqlite3"
CLIENT="$ROOT_DIR/linux/logger/client.py"
SOCK="/tmp/hermesd.sock"

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

env_count=$(sqlite3 -noheader -batch "$DB" "select count(*) from env;")
air_count=$(sqlite3 -noheader -batch "$DB" "select count(*) from air;")
if [[ "${env_count:-0}" -eq 0 || "${air_count:-0}" -eq 0 ]]; then
  echo "No data yet (env=$env_count air=$air_count)" >&2
  exit 1
fi

cmd="$(sqlite3 -noheader -batch "$DB" "
WITH
  now_env AS (SELECT ts_utc, temp_c, hum_pct FROM env ORDER BY id DESC LIMIT 1),
  now_air AS (SELECT ts_utc, eco2_ppm, tvoc_ppb FROM air ORDER BY id DESC LIMIT 1),

  env_5m AS (
    SELECT temp_c AS temp_c_5m, hum_pct AS hum_pct_5m
    FROM env
    WHERE julianday(ts_utc) >= julianday((SELECT ts_utc FROM now_env)) - (5.0/1440.0)
    ORDER BY id ASC LIMIT 1
  ),
  air_5m AS (
    SELECT eco2_ppm AS eco2_5m, tvoc_ppb AS tvoc_5m
    FROM air
    WHERE julianday(ts_utc) >= julianday((SELECT ts_utc FROM now_air)) - (5.0/1440.0)
    ORDER BY id ASC LIMIT 1
  ),

  env_60m AS (
    SELECT temp_c AS temp_c_60m, hum_pct AS hum_pct_60m
    FROM env
    WHERE julianday(ts_utc) >= julianday((SELECT ts_utc FROM now_env)) - (60.0/1440.0)
    ORDER BY id ASC LIMIT 1
  ),
  air_60m AS (
    SELECT eco2_ppm AS eco2_60m, tvoc_ppb AS tvoc_60m
    FROM air
    WHERE julianday(ts_utc) >= julianday((SELECT ts_utc FROM now_air)) - (60.0/1440.0)
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
" | tr -d '\n')"

if [[ -z "$cmd" ]]; then
  echo "No command generated" >&2
  exit 1
fi

python3 "$CLIENT" send "$cmd" >/dev/null

epoch=$(date +%s)
python3 "$CLIENT" send "OLED,TIME,epoch=$epoch" >/dev/null

echo "sent: $cmd"
