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
