#!/usr/bin/env bash
set -euo pipefail

REPO=~/hermes-src/hermes
NRF_PORT=/dev/hermes-nrf
ESP_PORT=/dev/hermes-esp
DAEMON_RUN="$REPO/linux/logger/run_daemon.sh"
LOGGER_CLIENT="$REPO/linux/logger/client.py"
OLED_CTL="$REPO/linux/oled/oledctl.py"

daemon_running() {
  python3 "$LOGGER_CLIENT" status >/dev/null 2>&1
}

daemon_status() {
  python3 "$LOGGER_CLIENT" status || echo "Daemon: STOPPED"
}

daemon_start() {
  "$DAEMON_RUN"
  sleep 0.4
  daemon_status
  echo "Log output: /tmp/hermesd.out"
}

daemon_stop() {
  python3 "$LOGGER_CLIENT" stop || echo "Daemon not running"
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
  echo "6) Daemon status"
  echo "7) Start daemon"
  echo "8) Stop daemon"
  echo "9) Tail daemon output"
  echo "10) OLED: Next page"
  echo "11) OLED: Prev page"
  echo "12) OLED: Stack USER"
  echo "13) OLED: Stack DEBUG"
  echo "14) OLED: Focus toggle"
  echo "15) Exit"
  echo ""
  read -r -p "Choose: " choice

  case "$choice" in
    1)
      "$REPO/tools/flash_esp.sh" "$ESP_PORT"
      read -r -p "Press Enter..."
      ;;
    2)
      echo "Put nRF into UF2 bootloader (double-tap reset), then flashing."
      if daemon_running; then
        echo "Note: daemon is running. Flashing is OK, but serial monitoring may require stopping the daemon."
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
      daemon_status
      read -r -p "Press Enter..."
      ;;
    7)
      daemon_start
      read -r -p "Press Enter..."
      ;;
    8)
      daemon_stop
      read -r -p "Press Enter..."
      ;;
    9)
      echo "Tailing /tmp/hermesd.out"
      tail -n 50 -f /tmp/hermesd.out
      ;;
    10)
      python3 "$OLED_CTL" next
      read -r -p "Press Enter..."
      ;;
    11)
      python3 "$OLED_CTL" prev
      read -r -p "Press Enter..."
      ;;
    12)
      python3 "$OLED_CTL" stack user
      read -r -p "Press Enter..."
      ;;
    13)
      python3 "$OLED_CTL" stack debug
      read -r -p "Press Enter..."
      ;;
    14)
      python3 "$OLED_CTL" focus toggle
      read -r -p "Press Enter..."
      ;;
    15)
      exit 0
      ;;
    *)
      echo "Invalid choice"
      sleep 1
      ;;
  esac
done
