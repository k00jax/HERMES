#!/usr/bin/env bash
set -euo pipefail

REPO=~/hermes-src/hermes
NRF_PORT=/dev/hermes-nrf
ESP_PORT=/dev/hermes-esp

while true; do
  clear
  echo "HERMES Control Menu"
  echo "-------------------"
  echo "1) Flash ESP32-S3 (Odroid -> ESP)"
  echo "2) Flash nRF (Odroid copies UF2 already in ~/incoming)"
  echo "3) Show USB devices (tty + uf2 drive)"
  echo "4) Tail today's raw log"
  echo "5) Query last 5 raw lines (SQLite)"
  echo "6) Exit"
  echo ""
  read -r -p "Choose: " choice

  case "$choice" in
    1)
      "$REPO/tools/flash_esp.sh" "$ESP_PORT"
      read -r -p "Press Enter..."
      ;;
    2)
      echo "Put nRF into UF2 bootloader (double-tap reset), then flashing."
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
      exit 0
      ;;
    *)
      echo "Invalid choice"
      sleep 1
      ;;
  esac
done
