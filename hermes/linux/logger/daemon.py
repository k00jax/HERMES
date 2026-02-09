import glob
import os
import time
import sqlite3
import datetime
import threading
import queue
import socket
import serial

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
                pass
    return kind, kvs

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
    with open(raw_path(dt), "a", encoding="utf-8") as f:
        f.write(f"{ts}\t{line}\n")
    conn.execute(
        "INSERT INTO raw_lines (ts_utc, source, line) VALUES (?, ?, ?)",
        (ts, "nrf", line),
    )
    kind, kvs = parse_line(line)
    if kind and kvs:
        conn.executemany(
            "INSERT INTO metrics (ts_utc, source, kind, key, value) VALUES (?, ?, ?, ?, ?)",
            [(ts, "nrf", kind, k, v) for (k, v) in kvs],
        )
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
