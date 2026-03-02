#!/usr/bin/env bash
set -euo pipefail

PORT_ARG="${1:-}"
BAUD="${2:-115200}"
ENV="${3:-esp32}"   # set to your real env name in platformio.ini

# Resolve repo root from this script location (tools/ -> repo root)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ESP_DIR="${REPO_ROOT}/firmware/esp32"  # adjust if needed

resolve_port() {
  local explicit_port="${1:-}"

  if [[ -n "${explicit_port}" ]]; then
    if [[ -e "${explicit_port}" ]]; then
      echo "${explicit_port}"
      return 0
    fi
    echo "[esp] Error: explicit port not found: ${explicit_port}" >&2
    return 1
  fi

  if [[ -e "/dev/hermes-esp" ]]; then
    echo "/dev/hermes-esp"
    return 0
  fi

  local dev vid
  local -a candidates=()
  for dev in /dev/ttyUSB*; do
    [[ -e "${dev}" ]] || continue
    vid="$(udevadm info --query=property --name="${dev}" 2>/dev/null | sed -n 's/^ID_VENDOR_ID=//p' | head -n1)"
    if [[ "${vid,,}" == "303a" ]]; then
      candidates+=("${dev}")
    fi
  done

  if [[ ${#candidates[@]} -eq 1 ]]; then
    echo "${candidates[0]}"
    return 0
  fi

  if [[ ${#candidates[@]} -gt 1 ]]; then
    echo "[esp] Error: multiple Espressif serial devices found: ${candidates[*]}" >&2
    echo "[esp] Pass port explicitly: $0 /dev/hermes-esp" >&2
    return 1
  fi

  echo "[esp] Error: no Espressif serial device found (expected USB VID 303a)." >&2
  echo "[esp] Try: pio device list" >&2
  echo "[esp] Or pass port explicitly: $0 /dev/hermes-esp" >&2
  return 1
}

if ! command -v pio >/dev/null 2>&1; then
  echo "[esp] Error: PlatformIO 'pio' not found. Install with: pipx install platformio"
  exit 1
fi

if [ ! -d "${ESP_DIR}" ]; then
  echo "[esp] Error: ESP32 firmware directory not found at ${ESP_DIR}"
  exit 1
fi

PORT="$(resolve_port "${PORT_ARG}")"

cd "${ESP_DIR}"

echo "[esp] Building (env=${ENV})..."
pio run -e "${ENV}"

echo "[esp] Uploading to ${PORT} (env=${ENV})..."
pio run -e "${ENV}" -t upload --upload-port "${PORT}"

echo "[esp] Done. To monitor serial:"
echo "  pio device monitor --port ${PORT} --baud ${BAUD}"
