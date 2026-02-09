#!/usr/bin/env bash
set -euo pipefail
export HERMES_NRF_PORT="${HERMES_NRF_PORT:-/dev/hermes-nrf}"
export HERMES_BAUD="${HERMES_BAUD:-115200}"
python3 "$(dirname "$0")/logger.py"
