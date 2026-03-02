#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

pkill -f 'python3 app.py' >/dev/null 2>&1 || true
python3 app.py >/tmp/hermes_dash_smoke.log 2>&1 &
PID=$!
trap 'kill "$PID" >/dev/null 2>&1 || true; wait "$PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 20); do
	if curl --max-time 1 -fsS http://127.0.0.1:8000/healthz >/tmp/smoke_healthz.json 2>/dev/null; then
		break
	fi
	sleep 0.5
done

curl --max-time 5 -fsS http://127.0.0.1:8000/healthz >/tmp/smoke_healthz.json
curl --max-time 5 -fsS http://127.0.0.1:8000/api/ready >/tmp/smoke_ready.json
curl --max-time 5 -fsS http://127.0.0.1:8000/api/status >/tmp/smoke_status.json
curl --max-time 5 -fsS http://127.0.0.1:8000/api/health >/tmp/smoke_api_health.json

curl --max-time 5 -fsS http://127.0.0.1:8000/settings >/tmp/smoke_settings_page.html
curl --max-time 5 -fsS http://127.0.0.1:8000/api/settings >/tmp/smoke_settings.json
curl --max-time 5 -fsS http://127.0.0.1:8000/ >/tmp/smoke_home.html
curl --max-time 5 -fsS http://127.0.0.1:8000/flip >/tmp/smoke_flip.html
curl --max-time 5 -fsS http://127.0.0.1:8000/home2 >/tmp/smoke_home2.html
curl --max-time 5 -fsS "http://127.0.0.1:8000/api/history?range=24h" >/tmp/smoke_history.json
curl --max-time 5 -fsS "http://127.0.0.1:8000/api/events?since_id=0&limit=10" >/tmp/smoke_events.json
curl --max-time 5 -fsS "http://127.0.0.1:8000/api/analytics/presence_by_hour?hours=24" >/tmp/smoke_analytics_presence.json
curl --max-time 5 -fsS http://127.0.0.1:8000/calibration >/tmp/smoke_calibration.html

echo SMOKE_OK
