#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-/dev/hermes-esp}"
BAUD="${2:-115200}"

cd ~/hermes-src/hermes/firmware/esp32

echo "[esp] Building..."
pio run

echo "[esp] Uploading to ${PORT}..."
pio run -t upload --upload-port "${PORT}"

echo "[esp] Done. If you want serial:"
echo "  pio device monitor --port ${PORT} --baud ${BAUD}"
