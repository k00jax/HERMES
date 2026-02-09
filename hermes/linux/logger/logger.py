import os
import time
import sqlite3
import datetime
import serial

PORT = os.environ.get("HERMES_NRF_PORT", "/dev/hermes-nrf")
BAUD = int(os.environ.get("HERMES_BAUD", "115200"))

RAW_DIR = os.path.expanduser("~/hermes-data/raw")
DB_PATH = os.path.expanduser("~/hermes-data/db/hermes.sqlite3")

os.makedirs(RAW_DIR, exist_ok=True)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def utc_now():
    return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)

def day_stamp(dt):
    return dt.strftime("%Y-%m-%d")

def raw_path(dt):
    return os.path.join(RAW_DIR, f"nrf_{day_stamp(dt)}.log")

def init_db(conn: sqlite3.Connection):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS raw_lines (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts_utc TEXT NOT NULL,
      source TEXT NOT NULL,
      line TEXT NOT NULL
    );
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS metrics (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts_utc TEXT NOT NULL,
      source TEXT NOT NULL,
      kind TEXT NOT NULL,
      key TEXT NOT NULL,
      value REAL
    );
    """)
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
                    dt = utc_now()

                    # raw file append
                    with open(raw_path(dt), "a", encoding="utf-8") as f:
                        f.write(f"{ts}\t{line}\n")

                    # raw db insert
                    conn.execute(
                        "INSERT INTO raw_lines (ts_utc, source, line) VALUES (?, ?, ?)",
                        (ts, "nrf", line),
                    )

                    # parsed metrics insert
                    kind, kvs = parse_line(line)
                    if kind and kvs:
                        conn.executemany(
                            "INSERT INTO metrics (ts_utc, source, kind, key, value) VALUES (?, ?, ?, ?, ?)",
                            [(ts, "nrf", kind, k, v) for (k, v) in kvs],
                        )

                    conn.commit()

        except Exception as e:
            print(f"[logger] disconnected or error: {e}")
            time.sleep(1.5)

if __name__ == "__main__":
    main()
