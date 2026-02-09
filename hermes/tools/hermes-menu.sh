#!/usr/bin/env bash
set -euo pipefail

REPO=~/hermes-src/hermes
NRF_PORT=/dev/hermes-nrf
ESP_PORT=/dev/hermes-esp
LOGGER_RUN="$REPO/linux/logger/run_logger.sh"
LOGGER_PY="$REPO/linux/logger/logger.py"

logger_pids() {
  pgrep -f "$LOGGER_PY" || true
}

logger_status() {
  local pids
  pids="$(logger_pids)"
  if [[ -z "$pids" ]]; then
    echo "Logger: STOPPED"
  else
    echo "Logger: RUNNING (PID(s): $pids)"
    echo "Port symlinks:"
    ls -l /dev/hermes-* 2>/dev/null || true
    echo "Current raw log tail:"
    tail -n 5 ~/hermes-data/raw/nrf_$(date -u +%F).log 2>/dev/null || true
  fi
}

logger_stop() {
  local pids
  pids="$(logger_pids)"
  if [[ -z "$pids" ]]; then
    echo "Logger already stopped."
    return 0
  fi
  echo "Stopping logger PID(s): $pids"
  pkill -f "$LOGGER_PY" || true
  sleep 0.3
  echo "Stopped."
}

logger_start() {
  local pids
  pids="$(logger_pids)"
  if [[ -n "$pids" ]]; then
    echo "Logger already running (PID(s): $pids)"
    return 0
  fi
  echo "Starting logger..."
  # detach so the menu stays usable
  nohup "$LOGGER_RUN" >/tmp/hermes_logger.out 2>&1 &
  sleep 0.4
  logger_status
  echo "Log output: /tmp/hermes_logger.out"
}

while true; do
  clear
  echo "HERMES Control Menu"
  echo "-------------------"
  echo "1) Flash ESP32-S3 (Odroid -> ESP)"
  echo "2) Flash nRF (Odroid copies UF2 already in ~/incoming)"
  echo "3) Show USB devices (tty + uf2 drive)"
  echo "4) Tail today's raw log"
  echo "5) Query last 5 raw lines (SQLite)"
  echo "6) Logger status"
  echo "7) Start logger"
  echo "8) Stop logger"
  echo "9) Exit"
  echo ""
  read -r -p "Choose: " choice

  case "$choice" in
    1)
      "$REPO/tools/flash_esp.sh" "$ESP_PORT"
      read -r -p "Press Enter..."
      ;;
    2)
      echo "Put nRF into UF2 bootloader (double-tap reset), then flashing."
      if [[ -n "$(logger_pids)" ]]; then
        echo "Note: logger is running. Flashing is OK, but serial monitoring may require stopping the logger."
      fi
      "$REPO/tools/flash_nrf_uf2.sh" ~/incoming/firmware.uf2
      read -r -p "Press Enter..."
      ;;
    3)
      echo "TTY devices:"
      ls -l /dev/ttyACM* 2>/dev/null || true
      echo ""
      echo "Block devices:"
      lsblk -o NAME,SIZE,RM,TYPE,FSTYPE,MOUNTPOINT,LABEL
      read -r -p "Press Enter..."
      ;;
    4)
      LOG=~/hermes-data/raw/nrf_$(date -u +%F).log
      echo "Tailing $LOG"
      tail -n 50 -f "$LOG"
      ;;
    5)
      sqlite3 ~/hermes-data/db/hermes.sqlite3 \
        "select ts_utc, line from raw_lines order by id desc limit 5;"
      read -r -p "Press Enter..."
      ;;
    6)
      logger_status
      read -r -p "Press Enter..."
      ;;
    7)
      logger_start
      read -r -p "Press Enter..."
      ;;
    8)
      logger_stop
      read -r -p "Press Enter..."
      ;;
    9)
      exit 0
      ;;
    *)
      echo "Invalid choice"
      sleep 1
      ;;
  esac
done
