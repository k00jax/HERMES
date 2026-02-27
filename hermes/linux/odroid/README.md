# Odroid Linux Integration

Odroid-specific services, scripts, containers, and systemd units.

## OLED Context Pusher

Pushes host-computed deltas to the nRF OLED overlay.
Also sends host epoch time for OLED time/date fallback.

Script:

```bash
~/hermes-src/hermes/tools/push_oled_context.sh
```

Systemd install:

```bash
sudo cp ~/hermes-src/hermes/linux/odroid/systemd/hermes-oled-context.service /etc/systemd/system/
sudo cp ~/hermes-src/hermes/linux/odroid/systemd/hermes-oled-context.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-oled-context.timer
```

Check status:

```bash
systemctl status hermes-oled-context.timer
journalctl -u hermes-oled-context.service -n 50
```

## Logger Daemon (systemd)

Install and enable the logger daemon:

```bash
sudo cp ~/hermes-src/hermes/linux/odroid/systemd/hermes-logger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-logger.service
```

Check status:

```bash
systemctl status hermes-logger.service
sudo journalctl -u hermes-logger.service -n 50
```

## Dashboard API + UI (systemd)

Install and enable the local dashboard service:

```bash
python3 -m pip install --user fastapi
sudo rm -f /etc/systemd/system/hermes-dashboard.service.d/override.conf
sudo cp ~/hermes-src/hermes/linux/odroid/systemd/hermes-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-dashboard.service
```

Notes:
- Remove any old `override.conf` first; stale `ExecStart` overrides can drop `--app-dir` and cause `Error loading ASGI app. Could not import module "app"`.
- The canonical unit file lives at `linux/odroid/systemd/hermes-dashboard.service`.

Check status and API:

```bash
systemctl status hermes-dashboard.service
curl -sS http://127.0.0.1:8000/api/status
curl -sS http://127.0.0.1:8000/api/health
curl -sS http://127.0.0.1:8000/readyz
curl -sS http://127.0.0.1:8000/metrics
curl -sS http://127.0.0.1:8000/api/events/latest?limit=20
curl -sS http://100.93.105.81:8000/healthz
```

Readiness checks (`/readyz`) include DB readability, logger status, and per-table ingest freshness.
The watchdog should target localhost readiness (`http://127.0.0.1:8000/readyz`) so local restart logic is independent of Tailnet state.

### Telnet HERMES Portal (optional)

Local text portal for VT100-ish clients (for example KaiOS Mocha Telnet).

Quick local test:

```bash
cd ~/hermes-src/hermes/linux/odroid/services/dashboard
TELNET_ENABLE=true TELNET_TOKEN=hermes TELNET_BIND_LAN=true python3 app.py
telnet <odroid_ip> 8023
```

Defaults and safety:
- Disabled by default (`TELNET_ENABLE=false`).
- Binds localhost by default (`127.0.0.1:8023`).
- LAN binding requires explicit `TELNET_BIND_LAN=true`.

## Events/Anomaly Emitter (systemd timer)

Builds high-signal `events` rows (`stale_detected`, `stale_recovered`, `reboot_detected`) from recent telemetry every 10 seconds.

Install and enable:

```bash
sudo chmod +x /home/odroid/hermes-src/hermes/linux/odroid/events/hermes_events_emitter.py
sudo cp ~/hermes-src/hermes/linux/odroid/systemd/hermes-events-emitter.service /etc/systemd/system/
sudo cp ~/hermes-src/hermes/linux/odroid/systemd/hermes-events-emitter.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-events-emitter.timer
```

Check status:

```bash
systemctl status hermes-events-emitter.timer
journalctl -u hermes-events-emitter.service -n 50
curl -sS "http://127.0.0.1:8000/api/events/latest?limit=20"
```

Open UI:

```bash
http://<odroid-ip>:8000/
```

## Dashboard Health Watchdog (systemd timer)

Checks `/healthz` every 10 seconds, requires 3 consecutive failures before restart,
and enforces a 120-second restart cooldown to avoid flapping.

Install and enable:

```bash
sudo chmod +x /home/odroid/hermes-src/hermes/linux/odroid/watchdog/hermes-dashboard-watchdog.sh
sudo cp ~/hermes-src/hermes/linux/odroid/systemd/hermes-dashboard-watchdog.service /etc/systemd/system/
sudo cp ~/hermes-src/hermes/linux/odroid/systemd/hermes-dashboard-watchdog.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-dashboard-watchdog.timer
```

Check status:

```bash
systemctl status hermes-dashboard-watchdog.timer
journalctl -u hermes-dashboard-watchdog.service -n 50
```

## ESP32 Wi-Fi Credentials (Odroid)

Create local Wi-Fi credentials for ESP32 station mode:

```bash
cat > ~/hermes-src/hermes/firmware/esp32/src/secrets.h <<'EOF'
#pragma once
#define WIFI_SSID "REDACTED_WIFI_SSID"
#define WIFI_PASS "REDACTED_WIFI_PASSWORD"
EOF
```

Reflash ESP32 from Odroid:

```bash
cd ~/hermes-src/hermes && ./tools/flash_esp.sh /dev/hermes-esp
```

Notes:
- `firmware/esp32/src/secrets.h` is gitignored and stays local to the device.
- Firmware uses STA mode (`WIFI_STA`) and joins your existing network.

## Validation after Flash

Use this checklist after flashing nRF and restarting services.

### A) Verify device came back

```bash
readlink -f /dev/hermes-nrf
ls -l /dev/hermes-nrf
```

### B) Verify logger is healthy

```bash
python3 ~/hermes-src/hermes/linux/logger/client.py status
python3 ~/hermes-src/hermes/linux/logger/client.py health
```

### C) Verify HB has boot/reset metadata

```bash
sqlite3 ~/hermes-data/db/hermes.sqlite3 \
"select id,ts_utc,uptime_s,boot,reset_reason from hb order by id desc limit 8;"
```

```bash
sqlite3 ~/hermes-data/db/hermes.sqlite3 \
"select count(*) from hb;"
```

### D) Verify LIGHT/MIC typed tables are growing

```bash
sqlite3 ~/hermes-data/db/hermes.sqlite3 "select count(*) from light;"
sqlite3 ~/hermes-data/db/hermes.sqlite3 "select count(*) from mic_noise;"
sleep 5
sqlite3 ~/hermes-data/db/hermes.sqlite3 "select count(*) from light;"
sqlite3 ~/hermes-data/db/hermes.sqlite3 "select count(*) from mic_noise;"
```

### E) Verify single-daemon safety

```bash
pgrep -af "linux/logger/daemon.py"
sudo lsof /dev/hermes-nrf 2>/dev/null || true
```

Expected: one daemon process and one owner of `/dev/hermes-nrf`.

### F) Flash command (reference)

```bash
cd ~/hermes-src/hermes && ./flash-nrf
```

### Warnings

- Do not run `run_daemon.sh` while `hermes-logger.service` is active.
- If HEALTH shows corruption, check for multiple readers using `lsof`/`fuser`.
