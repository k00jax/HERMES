#!/usr/bin/env bash
set -euo pipefail

DEVICE="${1:-/dev/hermes-nrf}"

if [[ ! -e "$DEVICE" ]]; then
  echo "Device not found: $DEVICE" >&2
  exit 2
fi

echo "OLED,STATUS" | sudo tee "$DEVICE" > /dev/null

if sudo timeout 2 cat "$DEVICE" | head -n 200 | grep -m 1 -q '^ACK,kind=OLED,op=STATUS$'; then
  echo "PASS: ACK received"
  exit 0
fi

echo "FAIL: no ACK received" >&2
exit 1
