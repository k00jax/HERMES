#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-/dev/ttyACM0}"
BAUD="${2:-115200}"
LOG_DIR="$HOME/hermes/logs"

mkdir -p "$LOG_DIR"

configure_port() {
  stty -F "$PORT" "$BAUD" cs8 -cstopb -parenb -ixon -ixoff -crtscts -echo -icanon -isig -iexten -icrnl -inlcr -opost
}

while true; do
  if [[ ! -c "$PORT" ]]; then
    sleep 2
    continue
  fi

  if ! configure_port; then
    sleep 2
    continue
  fi

  while IFS= read -r line; do
    ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    log_file="$LOG_DIR/hermes_$(date -u +"%Y-%m-%d").log"
    printf "%s %s\n" "$ts" "$line" >> "$log_file"
  done < "$PORT"

  sleep 1
  done
