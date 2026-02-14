#!/usr/bin/env bash
set -euo pipefail

SERVICE="hermes-dashboard.service"
URL="${HERMES_DASHBOARD_HEALTH_URL:-http://127.0.0.1:8000/healthz}"
TRIES=30
SLEEP_SECS=0.3

echo "[dashboard] restarting ${SERVICE}"
sudo systemctl restart "${SERVICE}"

for i in $(seq 1 "${TRIES}"); do
  if curl -fsS "${URL}" >/dev/null 2>&1; then
    echo "[dashboard] ready (${URL})"
    exit 0
  fi
  sleep "${SLEEP_SECS}"
done

echo "[dashboard] ERROR: not ready after ${TRIES} tries (${URL})" >&2
echo "[dashboard] debug: systemctl status ${SERVICE}" >&2
sudo systemctl status "${SERVICE}" --no-pager -n 30 || true
echo "[dashboard] debug: ss -ltnp | grep ':8000'" >&2
ss -ltnp | grep ':8000' || true
echo "[dashboard] debug: journalctl -u ${SERVICE}" >&2
sudo journalctl -u "${SERVICE}" -n 60 --no-pager || true

exit 1
