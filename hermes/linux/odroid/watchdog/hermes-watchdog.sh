#!/usr/bin/env bash

set -u

HEALTH_CMD=(python3 /home/odroid/hermes-src/hermes/linux/logger/client.py health)
DB_PATH="/home/odroid/hermes-data/db/hermes.sqlite3"
CLIENT_PATH="/home/odroid/hermes-src/hermes/linux/logger/client.py"
STATE_FILE="/home/odroid/hermes-data/watchdog_led_state"
LOCK_FILE="/tmp/hermes-nrf.lock"

current_led_state="UNKNOWN"
if [[ -f "$STATE_FILE" ]]; then
  current_led_state="$(head -n 1 "$STATE_FILE" 2>/dev/null || echo "UNKNOWN")"
fi

persist_led_state() {
  local state="$1"
  mkdir -p "$(dirname "$STATE_FILE")"
  printf '%s\n' "$state" > "$STATE_FILE"
}

send_led_alert() {
  local state="$1"
  local frame="OLED,ALERT,STALE,${state}"
  if [[ "$state" == "$current_led_state" ]]; then
    return
  fi

  if timeout 2s python3 "$CLIENT_PATH" send "$frame" >/dev/null 2>&1; then
    echo "[watchdog] sent=${frame}"
    current_led_state="$state"
    persist_led_state "$state"
    return
  fi

  if timeout 2s systemctl is-active --quiet hermes-logger.service; then
    echo "[watchdog] send_fail=${frame},reason=logger_active"
    return
  fi

  if ! timeout 2s flock -n "$LOCK_FILE" -c true >/dev/null 2>&1; then
    echo "[watchdog] send_fail=lock_busy"
    return
  fi

  if timeout 2s flock -w 1 "$LOCK_FILE" sh -c 'printf "%s\n" "$1" > /dev/hermes-nrf' _ "$frame"; then
    echo "[watchdog] sent=${frame}"
    current_led_state="$state"
    persist_led_state "$state"
    return
  fi

  echo "[watchdog] send_fail=${frame},reason=write_fail"
}

health_output=""
if ! health_output="$(timeout 2s "${HEALTH_CMD[@]}" 2>/dev/null)"; then
  send_led_alert "ON"
  echo "[watchdog] health_fail"
  exit 0
fi

freshness_segment="$(printf '%s\n' "$health_output" | sed -n 's/.*freshness=\([^ ]*\).*/\1/p' | head -n 1)"

required_prefixes=("HB" "ENV" "AIR" "LIGHT" "MIC" "ESP,NET")
declare -A status_by_prefix=()
parse_ok=1

if [[ -z "$freshness_segment" ]]; then
  parse_ok=0
else
  IFS='|' read -r -a freshness_parts <<< "$freshness_segment"
  for part in "${freshness_parts[@]}"; do
    prefix="${part%%:*}"
    remainder="${part#*:}"
    status="${remainder%%(*}"
    if [[ -n "$prefix" && -n "$status" && "$part" == *":"* ]]; then
      status_by_prefix["$prefix"]="$status"
    fi
  done

  for prefix in "${required_prefixes[@]}"; do
    if [[ -z "${status_by_prefix[$prefix]+x}" ]]; then
      parse_ok=0
      break
    fi
  done
fi

if [[ "$parse_ok" -eq 1 ]]; then
  stale_prefixes=()
  for prefix in "${required_prefixes[@]}"; do
    if [[ "${status_by_prefix[$prefix]}" != "ok" ]]; then
      stale_prefixes+=("$prefix")
    fi
  done

  if [[ "${#stale_prefixes[@]}" -gt 0 ]]; then
    send_led_alert "ON"
    stale_joined="$(IFS=,; echo "${stale_prefixes[*]}")"
    echo "[watchdog] STALE prefixes=${stale_joined}"
  else
    send_led_alert "OFF"
  fi
  exit 0
fi

sqlite_output=""
if ! sqlite_output="$(timeout 2s sqlite3 "$DB_PATH" "PRAGMA busy_timeout=2000; select max(ts_utc) from hb; select max(ts_utc) from env; select max(ts_utc) from air; select max(ts_utc) from light; select max(ts_utc) from mic_noise; select max(ts_utc) from esp_net;" 2>/dev/null)"; then
  send_led_alert "ON"
  echo "[watchdog] health_fail"
  exit 0
fi

mapfile -t ts_values <<< "$sqlite_output"
if [[ "${#ts_values[@]}" -lt 6 ]]; then
  send_led_alert "ON"
  echo "[watchdog] health_fail"
  exit 0
fi

now_epoch="$(date -u +%s)"
fallback_names=("HB" "ENV" "AIR" "LIGHT" "MIC" "ESP,NET")
fallback_limits=(8 8 8 8 8 25)
stale_prefixes=()

for i in "${!fallback_names[@]}"; do
  ts="${ts_values[$i]}"
  prefix="${fallback_names[$i]}"
  limit="${fallback_limits[$i]}"

  if [[ -z "$ts" ]]; then
    stale_prefixes+=("$prefix")
    continue
  fi

  if ! ts_epoch="$(date -u -d "$ts" +%s 2>/dev/null)"; then
    stale_prefixes+=("$prefix")
    continue
  fi

  age="$((now_epoch - ts_epoch))"
  if (( age > limit )); then
    stale_prefixes+=("$prefix")
  fi
done

if [[ "${#stale_prefixes[@]}" -gt 0 ]]; then
  send_led_alert "ON"
  stale_joined="$(IFS=,; echo "${stale_prefixes[*]}")"
  echo "[watchdog] STALE prefixes=${stale_joined}"
else
  send_led_alert "OFF"
fi

exit 0
