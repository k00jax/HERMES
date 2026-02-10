#!/usr/bin/env bash
set -euo pipefail

DEVICE="${1:-/dev/hermes-nrf}"

if [[ ! -e "$DEVICE" ]]; then
  echo "Device not found: $DEVICE" >&2
  exit 2
fi

tmpfile="$(mktemp)"
cleanup() {
  rm -f "$tmpfile"
}
trap cleanup EXIT

sudo timeout 2 cat "$DEVICE" > "$tmpfile" &
reader_pid=$!
sleep 0.1

echo "OLED,STATUS" | sudo tee "$DEVICE" > /dev/null

wait "$reader_pid" 2>/dev/null || true

if head -n 400 "$tmpfile" | grep -m 1 -q '^ACK,kind=OLED,op=STATUS'; then
  echo "PASS: ACK received"
  exit 0
fi

echo "FAIL: no ACK received" >&2
exit 1
