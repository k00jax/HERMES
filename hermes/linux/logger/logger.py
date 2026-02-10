import os
import time
import sqlite3
import datetime
import serial
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None
try:
    from backports.zoneinfo import ZoneInfo as BackportZoneInfo
except ImportError:  # pragma: no cover
    BackportZoneInfo = None

PORT = os.environ.get("HERMES_NRF_PORT", "/dev/hermes-nrf")
BAUD = int(os.environ.get("HERMES_BAUD", "115200"))

RAW_DIR = os.path.expanduser("~/hermes-data/raw")
DB_PATH = os.path.expanduser("~/hermes-data/db/hermes.sqlite3")

os.makedirs(RAW_DIR, exist_ok=True)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def utc_now():
    return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)

TZ_NAME = os.environ.get("HERMES_TZ", "America/Chicago")

def resolve_tz():
    if ZoneInfo:
        try:
            return ZoneInfo(TZ_NAME)
        except Exception:
            pass
    if BackportZoneInfo:
        try:
            return BackportZoneInfo(TZ_NAME)
        except Exception:
            pass
    return datetime.timezone(datetime.timedelta(hours=-6))

CENTRAL_TZ = resolve_tz()

def local_now():
    if CENTRAL_TZ:
        return datetime.datetime.now(tz=CENTRAL_TZ)
    return datetime.datetime.now().astimezone()

def day_stamp(dt):
    return dt.strftime("%Y-%m-%d")

def raw_path(dt):
    return os.path.join(RAW_DIR, f"nrf_{day_stamp(dt)}.log")

def ensure_column(conn: sqlite3.Connection, table: str, column: str, col_type: str):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if any(row[1] == column for row in rows):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

def init_db(conn: sqlite3.Connection):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS raw_lines (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts_utc TEXT NOT NULL,
            ts_local TEXT,
      source TEXT NOT NULL,
      line TEXT NOT NULL
    );
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS metrics (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts_utc TEXT NOT NULL,
            ts_local TEXT,
      source TEXT NOT NULL,
      kind TEXT NOT NULL,
      key TEXT NOT NULL,
      value REAL
    );
    """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS oled_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            ts_local TEXT,
            source TEXT NOT NULL,
            stack TEXT,
            page INTEGER,
            focus INTEGER,
            debug INTEGER,
            screen TEXT
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS hb (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            ts_local TEXT,
            source TEXT NOT NULL,
            tick_ms INTEGER,
            seq INTEGER
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS env (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            ts_local TEXT,
            source TEXT NOT NULL,
            temp_c REAL,
            hum_pct REAL
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS air (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            ts_local TEXT,
            source TEXT NOT NULL,
            eco2_ppm REAL,
            tvoc_ppb REAL
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS acks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            ts_local TEXT,
            source TEXT NOT NULL,
            kind TEXT,
            op TEXT
        );
        """)
        ensure_column(conn, "raw_lines", "ts_local", "TEXT")
        ensure_column(conn, "metrics", "ts_local", "TEXT")
        ensure_column(conn, "oled_status", "ts_local", "TEXT")
        ensure_column(conn, "hb", "ts_local", "TEXT")
        ensure_column(conn, "env", "ts_local", "TEXT")
        ensure_column(conn, "air", "ts_local", "TEXT")
        ensure_column(conn, "acks", "ts_local", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hb_ts ON hb(ts_utc);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hb_local ON hb(ts_local);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_env_ts ON env(ts_utc);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_env_local ON env(ts_local);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_air_ts ON air(ts_utc);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_air_local ON air(ts_local);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_acks_ts ON acks(ts_utc);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_acks_local ON acks(ts_local);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_oled_status_ts ON oled_status(ts_utc);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_oled_status_local ON oled_status(ts_local);")
    conn.commit()

def parse_line(line: str):
    parts = [p.strip() for p in line.split(",") if p.strip()]
    if not parts:
        return None, []
    kind = parts[0]
    kvs = []
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            k = k.strip()
            v = v.strip()
            try:
                kvs.append((k, float(v)))
            except ValueError:
                # ignore non-numeric values for now
                pass
    return kind, kvs

def parse_kv_pairs(line: str):
    parts = [p.strip() for p in line.split(",") if p.strip()]
    if not parts:
        return None, {}
    kind = parts[0]
    kvs = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            kvs[k.strip()] = v.strip()
    return kind, kvs

def parse_oled_status(line: str):
    kind, kvs = parse_kv_pairs(line)
    if kind != "ACK":
        return None
    if kvs.get("kind") != "OLED" or kvs.get("op") != "STATUS":
        return None
    out = {
        "stack": kvs.get("stack"),
        "screen": kvs.get("screen"),
    }
    for key in ("page", "focus", "debug"):
        value = kvs.get(key)
        if value is None:
            out[key] = None
            continue
        try:
            out[key] = int(value)
        except ValueError:
            out[key] = None
    return out

def parse_int(value: str):
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None

def parse_float(value: str):
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None

def insert_typed_frames(conn: sqlite3.Connection, ts: str, ts_local: str, line: str):
    kind, kvs = parse_kv_pairs(line)
    if not kind:
        return
    if kind == "HB":
        tick = parse_int(kvs.get("tick"))
        seq = parse_int(kvs.get("seq"))
        if tick is None and seq is None:
            return
        conn.execute(
            "INSERT INTO hb (ts_utc, ts_local, source, tick_ms, seq) VALUES (?, ?, ?, ?, ?)",
            (ts, ts_local, "nrf", tick, seq),
        )
        return
    if kind == "ENV":
        temp_c = parse_float(kvs.get("temp_c"))
        hum_pct = parse_float(kvs.get("hum_pct"))
        if temp_c is None and hum_pct is None:
            return
        conn.execute(
            "INSERT INTO env (ts_utc, ts_local, source, temp_c, hum_pct) VALUES (?, ?, ?, ?, ?)",
            (ts, ts_local, "nrf", temp_c, hum_pct),
        )
        return
    if kind == "AIR":
        eco2_ppm = parse_float(kvs.get("eco2_ppm"))
        tvoc_ppb = parse_float(kvs.get("tvoc_ppb"))
        if eco2_ppm is None and tvoc_ppb is None:
            return
        conn.execute(
            "INSERT INTO air (ts_utc, ts_local, source, eco2_ppm, tvoc_ppb) VALUES (?, ?, ?, ?, ?)",
            (ts, ts_local, "nrf", eco2_ppm, tvoc_ppb),
        )
        return
    if kind == "ACK":
        ack_kind = kvs.get("kind")
        ack_op = kvs.get("op")
        if not ack_kind and not ack_op:
            return
        conn.execute(
            "INSERT INTO acks (ts_utc, ts_local, source, kind, op) VALUES (?, ?, ?, ?, ?)",
            (ts, ts_local, "nrf", ack_kind, ack_op),
        )
        return

def parse_int(value: str):
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None

def parse_float(value: str):
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None

def insert_typed_frames(conn: sqlite3.Connection, ts: str, line: str):
    kind, kvs = parse_kv_pairs(line)
    if not kind:
        return
    if kind == "HB":
        tick = parse_int(kvs.get("tick"))
        seq = parse_int(kvs.get("seq"))
        if tick is None and seq is None:
            return
        conn.execute(
            "INSERT INTO hb (ts_utc, source, tick_ms, seq) VALUES (?, ?, ?, ?)",
            (ts, "nrf", tick, seq),
        )
        return
    if kind == "ENV":
        temp_c = parse_float(kvs.get("temp_c"))
        hum_pct = parse_float(kvs.get("hum_pct"))
        if temp_c is None and hum_pct is None:
            return
        conn.execute(
            "INSERT INTO env (ts_utc, source, temp_c, hum_pct) VALUES (?, ?, ?, ?)",
            (ts, "nrf", temp_c, hum_pct),
        )
        return
    if kind == "AIR":
        eco2_ppm = parse_float(kvs.get("eco2_ppm"))
        tvoc_ppb = parse_float(kvs.get("tvoc_ppb"))
        if eco2_ppm is None and tvoc_ppb is None:
            return
        conn.execute(
            "INSERT INTO air (ts_utc, source, eco2_ppm, tvoc_ppb) VALUES (?, ?, ?, ?)",
            (ts, "nrf", eco2_ppm, tvoc_ppb),
        )
        return
    if kind == "ACK":
        ack_kind = kvs.get("kind")
        ack_op = kvs.get("op")
        if not ack_kind and not ack_op:
            return
        conn.execute(
            "INSERT INTO acks (ts_utc, source, kind, op) VALUES (?, ?, ?, ?)",
            (ts, "nrf", ack_kind, ack_op),
        )
        return

def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    print(f"[logger] starting. port={PORT} baud={BAUD} db={DB_PATH}")

    while True:
        try:
            with serial.Serial(PORT, BAUD, timeout=1) as ser:
                ser.reset_input_buffer()
                print("[logger] connected")
                while True:
                    b = ser.readline()
                    if not b:
                        continue
                    line = b.decode(errors="replace").strip()
                    if not line:
                        continue

                    ts = utc_now().isoformat()
                    ts_local = local_now().isoformat()
                    dt = utc_now()

                    # raw file append
                    with open(raw_path(dt), "a", encoding="utf-8") as f:
                        f.write(f"{ts}\t{line}\n")

                    # raw db insert
                    conn.execute(
                        "INSERT INTO raw_lines (ts_utc, ts_local, source, line) VALUES (?, ?, ?, ?)",
                        (ts, ts_local, "nrf", line),
                    )

                    # parsed metrics insert
                    kind, kvs = parse_line(line)
                    if kind and kvs:
                        conn.executemany(
                            "INSERT INTO metrics (ts_utc, ts_local, source, kind, key, value) VALUES (?, ?, ?, ?, ?, ?)",
                            [(ts, ts_local, "nrf", kind, k, v) for (k, v) in kvs],
                        )

                    oled_status = parse_oled_status(line)
                    if oled_status:
                        conn.execute(
                            """
                            INSERT INTO oled_status (ts_utc, ts_local, source, stack, page, focus, debug, screen)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                ts,
                                ts_local,
                                "nrf",
                                oled_status.get("stack"),
                                oled_status.get("page"),
                                oled_status.get("focus"),
                                oled_status.get("debug"),
                                oled_status.get("screen"),
                            ),
                        )

                    insert_typed_frames(conn, ts, ts_local, line)

                    insert_typed_frames(conn, ts, line)

                    conn.commit()

        except Exception as e:
            print(f"[logger] disconnected or error: {e}")
            time.sleep(1.5)

if __name__ == "__main__":
    main()
