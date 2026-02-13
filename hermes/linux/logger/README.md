# HERMES Logger (Odroid)

## Install
```bash
python3 -m pip install --user pyserial
```

## Run

```bash
python3 ~/hermes-src/hermes/linux/logger/logger.py
```

## Override port/baud

```bash
export HERMES_NRF_PORT=/dev/ttyACM1
export HERMES_BAUD=115200
python3 ~/hermes-src/hermes/linux/logger/logger.py
```

## Output

* Raw logs: `~/hermes-data/raw/nrf_YYYY-MM-DD.log`
* SQLite DB: `~/hermes-data/db/hermes.sqlite3`
* OLED status table: `oled_status(ts_utc, source, stack, page, focus, debug, screen)`
* Typed tables: `hb`, `env`, `air`, `acks` (each includes `ts_utc` and `ts_local`)

### Local Time

All tables keep `ts_utc` as the source of truth and add `ts_local` in America/Chicago (CST/CDT).
You can override the timezone with `HERMES_TZ` (default: `America/Chicago`). If timezone data is
unavailable, the logger falls back to fixed CST (-06:00).

### Raw Log Retention

Raw log files are cleaned up periodically. Set `HERMES_RAW_RETENTION_DAYS` to control retention
days (default: 30). Use `0` to disable cleanup.

## OLED Status Query

Send a STATUS command via the daemon client:

```bash
python3 ~/hermes-src/hermes/linux/logger/client.py oled-status
```

## Bring-up Validation

Run these checks after flash/restart to confirm logging integrity.

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

<!--
```bash
sqlite3 ~/hermes-data/db/hermes.sqlite3 "select ts_utc,line from raw_lines order by id desc limit 5;"
sqlite3 ~/hermes-data/db/hermes.sqlite3 "select ts_utc,kind,key,value from metrics order by id desc limit 10;"
```
-->
