#!/usr/bin/env bash

set -u

URL="${HERMES_DASHBOARD_HEALTH_URL:-http://127.0.0.1:8000/readyz}"
CURL_TIMEOUT_SECS="${HERMES_DASHBOARD_CURL_TIMEOUT_SECS:-2}"
FAIL_THRESHOLD="${HERMES_DASHBOARD_FAIL_THRESHOLD:-3}"
RESTART_COOLDOWN_SECS="${HERMES_DASHBOARD_RESTART_COOLDOWN_SECS:-120}"
STATE_DIR="${HERMES_DASHBOARD_WATCHDOG_STATE_DIR:-/home/odroid/hermes-data/dashboard-watchdog}"
FAIL_COUNT_FILE="$STATE_DIR/fail_count"
LAST_RESTART_FILE="$STATE_DIR/last_restart_epoch"
RESTART_COUNT_FILE="$STATE_DIR/restart_count"

mkdir -p "$STATE_DIR"

read_num_file() {
  local path="$1"
  local fallback="$2"
  if [[ -f "$path" ]]; then
    local raw
    raw="$(head -n 1 "$path" 2>/dev/null || true)"
    if [[ "$raw" =~ ^[0-9]+$ ]]; then
      echo "$raw"
      return
    fi
  fi
  echo "$fallback"
}

write_num_file() {
  local path="$1"
  local value="$2"
  printf '%s\n' "$value" > "$path"
}

fail_count="$(read_num_file "$FAIL_COUNT_FILE" 0)"
last_restart_epoch="$(read_num_file "$LAST_RESTART_FILE" 0)"
restart_count="$(read_num_file "$RESTART_COUNT_FILE" 0)"

ready_resp=""
if ready_resp="$(timeout "${CURL_TIMEOUT_SECS}s" curl -sS "$URL" 2>&1)"; then
  if printf '%s' "$ready_resp" | grep -Eq '"ready"[[:space:]]*:[[:space:]]*true'; then
    if [[ "$fail_count" != "0" ]]; then
      echo "[dashboard-watchdog] recovered url=$URL prev_fail_count=$fail_count"
    fi
    write_num_file "$FAIL_COUNT_FILE" 0
    exit 0
  fi
fi

ready_reason="unknown"
if [[ -n "$ready_resp" ]]; then
  ready_reason="$(python3 - <<'PY' "$ready_resp"
import json
import sys

raw = sys.argv[1]
try:
    payload = json.loads(raw)
except Exception:
    print("response_not_json")
    raise SystemExit

failures = payload.get("failures")
if isinstance(failures, list) and failures:
    print(str(failures[0]))
else:
    print("ready_false")
PY
)"
else
  ready_reason="curl_error"
fi

if [[ "$ready_reason" == "response_not_json" ]]; then
  ready_reason="curl_error"
fi

if [[ "$ready_reason" == "curl_error" && -n "$ready_resp" ]]; then
  ready_reason="curl_error:$(printf '%s' "$ready_resp" | tail -n 1)"
fi

fail_count=$((fail_count + 1))
write_num_file "$FAIL_COUNT_FILE" "$fail_count"
echo "[dashboard-watchdog] readiness_failed url=$URL reason=$ready_reason fail_count=$fail_count threshold=$FAIL_THRESHOLD"

if (( fail_count < FAIL_THRESHOLD )); then
  exit 0
fi

now_epoch="$(date +%s)"
time_since_restart=$((now_epoch - last_restart_epoch))

if (( time_since_restart < RESTART_COOLDOWN_SECS )); then
  echo "[dashboard-watchdog] restart_suppressed cooldown_remaining=$((RESTART_COOLDOWN_SECS - time_since_restart))s"
  exit 0
fi

if systemctl restart hermes-dashboard.service; then
  restart_count=$((restart_count + 1))
  write_num_file "$LAST_RESTART_FILE" "$now_epoch"
  write_num_file "$FAIL_COUNT_FILE" 0
  write_num_file "$RESTART_COUNT_FILE" "$restart_count"
  echo "[dashboard-watchdog] restarted hermes-dashboard.service reason=$ready_reason restart_count=$restart_count"
  exit 0
fi

echo "[dashboard-watchdog] restart_failed service=hermes-dashboard.service"
exit 1
