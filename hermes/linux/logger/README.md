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

<!--
```bash
sqlite3 ~/hermes-data/db/hermes.sqlite3 "select ts_utc,line from raw_lines order by id desc limit 5;"
sqlite3 ~/hermes-data/db/hermes.sqlite3 "select ts_utc,kind,key,value from metrics order by id desc limit 10;"
```
-->
