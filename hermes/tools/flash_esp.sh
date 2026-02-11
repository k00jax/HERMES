#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-/dev/hermes-esp}"
BAUD="${2:-115200}"
ENV="${3:-esp32}"   # set to your real env name in platformio.ini

# Resolve repo root from this script location (tools/ -> repo root)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ESP_DIR="${REPO_ROOT}/firmware/esp32"  # adjust if needed

if ! command -v pio >/dev/null 2>&1; then
  echo "[esp] Error: PlatformIO 'pio' not found. Install with: pipx install platformio"
  exit 1
fi

if [ ! -d "${ESP_DIR}" ]; then
  echo "[esp] Error: ESP32 firmware directory not found at ${ESP_DIR}"
  exit 1
fi

cd "${ESP_DIR}"

echo "[esp] Building (env=${ENV})..."
pio run -e "${ENV}"

echo "[esp] Uploading to ${PORT} (env=${ENV})..."
pio run -e "${ENV}" -t upload --upload-port "${PORT}"

echo "[esp] Done. To monitor serial:"
echo "  pio device monitor --port ${PORT} --baud ${BAUD}"
