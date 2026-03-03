#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PORT=18000
while ss -ltn | awk '{print $4}' | grep -q ":${PORT}$"; do
  PORT=$((PORT + 1))
done
echo "SMOKE_PORT=${PORT}"

setsid python3 -m uvicorn app:app --host 127.0.0.1 --port "${PORT}" >/tmp/hermes_dash_smoke.log 2>&1 &
PID=$!
PGID=$PID
trap 'kill -TERM -- -"$PGID" 2>/dev/null || true; sleep 0.5; kill -KILL -- -"$PGID" 2>/dev/null || true; wait "$PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 20); do
	if curl --max-time 1 -fsS "http://127.0.0.1:${PORT}/healthz" >/tmp/smoke_healthz.json 2>/dev/null; then
		break
	fi
	sleep 0.5
done

curl --max-time 5 -fsS "http://127.0.0.1:${PORT}/healthz" >/tmp/smoke_healthz.json
curl --max-time 5 -fsS "http://127.0.0.1:${PORT}/api/ready" >/tmp/smoke_ready.json
curl --max-time 5 -fsS "http://127.0.0.1:${PORT}/api/status" >/tmp/smoke_status.json
curl --max-time 5 -fsS "http://127.0.0.1:${PORT}/api/health" >/tmp/smoke_api_health.json

curl --max-time 5 -fsS "http://127.0.0.1:${PORT}/settings" >/tmp/smoke_settings_page.html
curl --max-time 5 -fsS "http://127.0.0.1:${PORT}/api/settings" >/tmp/smoke_settings.json
curl --max-time 5 -fsS "http://127.0.0.1:${PORT}/" >/tmp/smoke_home.html
curl --max-time 5 -fsS "http://127.0.0.1:${PORT}/flip" >/tmp/smoke_flip.html
curl --max-time 5 -fsS "http://127.0.0.1:${PORT}/home2" >/tmp/smoke_home2.html
curl --max-time 5 -fsS "http://127.0.0.1:${PORT}/api/history?range=24h&limit=50" >/tmp/smoke_history.json
curl --max-time 5 -fsS "http://127.0.0.1:${PORT}/api/events?since_id=0&limit=10" >/tmp/smoke_events.json
curl --max-time 5 -fsS "http://127.0.0.1:${PORT}/api/analytics/presence_by_hour?hours=24" >/tmp/smoke_analytics_presence.json
curl --max-time 5 -fsS "http://127.0.0.1:${PORT}/calibration" >/tmp/smoke_calibration.html
curl --max-time 5 -fsS "http://127.0.0.1:${PORT}/reports" >/tmp/smoke_reports.html
curl --max-time 5 -fsS "http://127.0.0.1:${PORT}/api/reports" >/tmp/smoke_reports_list.json

echo SMOKE_OK
