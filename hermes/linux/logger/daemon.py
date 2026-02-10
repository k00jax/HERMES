import glob
import os
import time
import sqlite3
import datetime
import threading
import queue
import socket
import serial
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

PREFERRED_PORT = os.environ.get("HERMES_NRF_PORT", "/dev/hermes-nrf")
PORT = None
BAUD = int(os.environ.get("HERMES_BAUD", "115200"))

RAW_DIR = os.path.expanduser("~/hermes-data/raw")
DB_PATH = os.path.expanduser("~/hermes-data/db/hermes.sqlite3")
SOCK_PATH = "/tmp/hermesd.sock"
NRF_BY_ID_HINT = "usb-Seeed_Studio_XIAO_nRF52840_9FBE2A3ABD93B121-if00"

os.makedirs(RAW_DIR, exist_ok=True)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

stats = {
    "lines_in": 0,
    "last_line_ts": "never",
    "last_error": "none",
    "serial_connected": False,
    "port": PREFERRED_PORT,
}
stats_lock = threading.Lock()

CENTRAL_TZ = ZoneInfo("America/Chicago") if ZoneInfo else None

def utc_now():
    return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)

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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_env_ts ON env(ts_utc);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_air_ts ON air(ts_utc);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_acks_ts ON acks(ts_utc);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_oled_status_ts ON oled_status(ts_utc);")
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

def update_stats(**kwargs):
    with stats_lock:
        for key, value in kwargs.items():
            stats[key] = value

def snapshot_stats():
    with stats_lock:
        return dict(stats)

def log_info(message: str):
    print(message, flush=True)

def resolve_serial_port(preferred: str) -> str:
    if preferred and os.path.exists(preferred):
        return preferred

    by_id = f"/dev/serial/by-id/{NRF_BY_ID_HINT}"
    if os.path.exists(by_id):
        return by_id

    for path in glob.glob("/dev/serial/by-id/*9FBE2A3ABD93B121*"):
        return path

    acms = sorted(glob.glob("/dev/ttyACM*"))
    if acms:
        return acms[0]

    return preferred

PORT = resolve_serial_port(PREFERRED_PORT)

def ensure_socket_unlinked():
    if os.path.exists(SOCK_PATH):
        os.unlink(SOCK_PATH)

def handle_line(conn: sqlite3.Connection, line: str):
    dt = utc_now()
    ts = dt.isoformat()
    ts_local = local_now().isoformat()
    with open(raw_path(dt), "a", encoding="utf-8") as f:
        f.write(f"{ts}\t{line}\n")
    conn.execute(
        "INSERT INTO raw_lines (ts_utc, ts_local, source, line) VALUES (?, ?, ?, ?)",
        (ts, ts_local, "nrf", line),
    )
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
    conn.commit()
    with stats_lock:
        stats["lines_in"] += 1
        stats["last_line_ts"] = ts
        if stats["lines_in"] % 50 == 0:
            log_info(f"[hermesd] lines_in={stats['lines_in']}")

def serial_worker(shutdown: threading.Event, out_q: queue.Queue):
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    ser = None
    while not shutdown.is_set():
        if ser is None:
            try:
                port = resolve_serial_port(PORT)
                log_info(f"[hermesd] trying serial port: {port}")
                ser = serial.Serial(port, BAUD, timeout=1)
                ser.reset_input_buffer()
                update_stats(serial_connected=True, last_error="none", port=port)
                log_info(f"[hermesd] serial connected: {port}")
            except Exception as e:
                update_stats(serial_connected=False, last_error=f"serial connect failed: {e}")
                log_info(f"[hermesd] serial connect failed: {e}")
                time.sleep(1.5)
                continue

        try:
            line_bytes = ser.readline()
            if line_bytes:
                line = line_bytes.decode(errors="replace").strip()
                if line:
                    handle_line(conn, line)

            while True:
                try:
                    cmd = out_q.get_nowait()
                except queue.Empty:
                    break
                ser.write((cmd + "\n").encode("utf-8"))

        except Exception as e:
            update_stats(serial_connected=False, last_error=f"serial error: {e}")
            log_info(f"[hermesd] serial error: {e}")
            try:
                ser.close()
            except Exception:
                pass
            ser = None

    if ser is not None:
        try:
            ser.close()
        except Exception:
            pass
    try:
        conn.close()
    except Exception:
        pass

def handle_command(cmd_line: str, out_q: queue.Queue):
    cmd_line = cmd_line.strip()
    if not cmd_line:
        return "ERR empty", False

    parts = cmd_line.split(" ", 1)
    cmd = parts[0].upper()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "PING":
        return "PONG", False
    if cmd == "STATUS":
        snap = snapshot_stats()
        return (
            "OK "
            f"port={snap['port']} "
            f"baud={BAUD} "
            f"lines_in={snap['lines_in']} "
            f"last_line_ts={snap['last_line_ts']} "
            f"last_error={snap['last_error']}",
            False,
        )
    if cmd == "SEND":
        if not arg:
            return "ERR no payload", False
        out_q.put(arg)
        return "OK", False
    if cmd == "STOP":
        return "OK", True
    return "ERR unknown command", False

def socket_worker(shutdown: threading.Event, out_q: queue.Queue):
    ensure_socket_unlinked()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCK_PATH)
    server.listen(8)
    server.settimeout(1)
    log_info(f"[hermesd] socket listening: {SOCK_PATH}")

    try:
        while not shutdown.is_set():
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except Exception as e:
                update_stats(last_error=f"socket accept failed: {e}")
                continue

            with conn:
                conn.settimeout(2)
                data = b""
                while b"\n" not in data and len(data) < 4096:
                    chunk = conn.recv(1024)
                    if not chunk:
                        break
                    data += chunk

                line = data.split(b"\n", 1)[0].decode(errors="replace")
                resp, should_stop = handle_command(line, out_q)
                try:
                    conn.sendall((resp + "\n").encode("utf-8"))
                except Exception:
                    pass
                if should_stop:
                    shutdown.set()
                    break
    finally:
        try:
            server.close()
        except Exception:
            pass
        try:
            ensure_socket_unlinked()
        except Exception:
            pass

def main():
    log_info(f"[hermesd] starting. port={PREFERRED_PORT} baud={BAUD} db={DB_PATH}")
    shutdown = threading.Event()
    out_q = queue.Queue()

    serial_thread = threading.Thread(
        target=serial_worker,
        args=(shutdown, out_q),
        daemon=True,
    )
    socket_thread = threading.Thread(
        target=socket_worker,
        args=(shutdown, out_q),
        daemon=True,
    )

    serial_thread.start()
    socket_thread.start()

    try:
        while not shutdown.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        shutdown.set()

    serial_thread.join(timeout=2)
    socket_thread.join(timeout=2)
    log_info("[hermesd] stopped")

if __name__ == "__main__":
    main()
