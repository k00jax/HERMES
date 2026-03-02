#!/usr/bin/env bash

set -u

section() {
  echo
  echo "==== $1 ===="
}

run_timeout() {
  timeout 2s "$@"
}

resolve_if_exists() {
  local dev="$1"
  if [[ -e "$dev" ]]; then
    readlink -f "$dev" 2>/dev/null || true
  fi
}

section "A) Timestamp + hostname + kernel"
date -Is
hostname
uname -a

section "B) Services"
logger_active="unknown"
oled_timer_active="unknown"

logger_active="$(run_timeout systemctl is-active hermes-logger.service 2>/dev/null || echo "unknown")"
oled_timer_active="$(run_timeout systemctl is-active hermes-oled-context.timer 2>/dev/null || echo "unknown")"

echo "hermes-logger.service: ${logger_active}"
echo "hermes-oled-context.timer: ${oled_timer_active}"

if [[ "$logger_active" == "active" ]]; then
  echo "--- journal: hermes-logger.service (last 5) ---"
  run_timeout journalctl -u hermes-logger.service -n 5 --no-pager 2>/dev/null || echo "[doctor] journal hermes-logger.service: TIMEOUT/FAIL"
fi

if [[ "$oled_timer_active" == "active" ]]; then
  echo "--- journal: hermes-oled-context.service (last 5) ---"
  run_timeout journalctl -u hermes-oled-context.service -n 5 --no-pager 2>/dev/null || echo "[doctor] journal hermes-oled-context.service: TIMEOUT/FAIL"
fi

section "C) Device mapping"
ls -l /dev/hermes-nrf /dev/hermes-esp 2>/dev/null || true

resolved_nrf="$(resolve_if_exists /dev/hermes-nrf)"
resolved_esp="$(resolve_if_exists /dev/hermes-esp)"

if [[ -n "$resolved_nrf" ]]; then
  echo "/dev/hermes-nrf -> $resolved_nrf"
fi
if [[ -n "$resolved_esp" ]]; then
  echo "/dev/hermes-esp -> $resolved_esp"
fi

declare -A acm_targets=()

if [[ "$resolved_nrf" == /dev/tty* ]]; then
  acm_targets["$resolved_nrf"]=1
fi
if [[ "$resolved_esp" == /dev/tty* ]]; then
  acm_targets["$resolved_esp"]=1
fi

for dev in "${!acm_targets[@]}"; do
  echo "--- udev: $dev ---"
  run_timeout udevadm info -a -n "$dev" 2>/dev/null | egrep -i 'idVendor|idProduct|serial|product' | head -n 20 || true
done

section "D) Port ownership (single-reader proof)"
timeout 2s sudo -n lsof /dev/hermes-nrf 2>/dev/null || true
timeout 2s sudo -n lsof /dev/hermes-esp 2>/dev/null || true

section "E) Logger daemon quick status (non-blocking)"
timeout 2s python3 ~/hermes-src/hermes/linux/logger/client.py status || echo "[doctor] status: TIMEOUT/FAIL"
timeout 2s python3 ~/hermes-src/hermes/linux/logger/client.py health || echo "[doctor] health: TIMEOUT/FAIL"

section "F) DB and disk"
ls -lh ~/hermes-data/db/hermes.sqlite3 2>/dev/null || true
df -h ~ | tail -n 1
timeout 2s sqlite3 ~/hermes-data/db/hermes.sqlite3 "PRAGMA busy_timeout=2000; select 'hb',count(*) from hb; select 'env',count(*) from env; select 'air',count(*) from air; select 'light',count(*) from light; select 'mic_noise',count(*) from mic_noise; select 'esp_net',count(*) from esp_net;" 2>/dev/null || true
