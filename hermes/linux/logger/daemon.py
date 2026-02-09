import os
import time
import sqlite3
import datetime
import selectors
import socket
import serial

PORT = os.environ.get("HERMES_NRF_PORT", "/dev/hermes-nrf")
BAUD = int(os.environ.get("HERMES_BAUD", "115200"))

RAW_DIR = os.path.expanduser("~/hermes-data/raw")
DB_PATH = os.path.expanduser("~/hermes-data/db/hermes.sqlite3")
SOCK_PATH = "/tmp/hermesd.sock"

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
                pass
    return kind, kvs

def ensure_socket_unlinked():
    if os.path.exists(SOCK_PATH):
        os.unlink(SOCK_PATH)

def setup_server(selector: selectors.BaseSelector):
    ensure_socket_unlinked()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCK_PATH)
    server.listen(8)
    server.setblocking(False)
    selector.register(server, selectors.EVENT_READ, data="accept")
    return server

def open_serial(port: str, baud: int):
    ser = serial.Serial(port, baud, timeout=0)
    ser.reset_input_buffer()
    return ser

def handle_serial_read(ser, buffer: bytearray, conn: sqlite3.Connection):
    try:
        data = ser.read(ser.in_waiting or 1)
    except Exception:
        raise
    if not data:
        return
    buffer.extend(data)
    while True:
        try:
            idx = buffer.index(b"\n")
        except ValueError:
            break
        line_bytes = buffer[:idx]
        del buffer[:idx + 1]
        if line_bytes.endswith(b"\r"):
            line_bytes = line_bytes[:-1]
        line = line_bytes.decode(errors="replace").strip()
        if not line:
            continue
        ts = utc_now().isoformat()
        dt = utc_now()
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

def handle_client_command(cmd_line: str, ser, start_time: float, clients: int):
    cmd_line = cmd_line.strip()
    if not cmd_line:
        return "ERR empty", False
    parts = cmd_line.split(" ", 1)
    cmd = parts[0].upper()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "PING":
        return "PONG", False
    if cmd == "STATUS":
        uptime_s = int(time.monotonic() - start_time)
        return f"OK port={PORT} baud={BAUD} clients={clients} uptime_s={uptime_s}", False
    if cmd == "SEND":
        if not arg:
            return "ERR no payload", False
        if ser is None:
            return "ERR serial not connected", False
        try:
            ser.write((arg + "\n").encode("utf-8"))
        except Exception:
            return "ERR serial write failed", False
        return "OK", False
    if cmd == "STOP":
        return "OK stopping", True
    return "ERR unknown command", False

def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    print(f"[hermesd] starting. port={PORT} baud={BAUD} db={DB_PATH} sock={SOCK_PATH}")

    selector = selectors.DefaultSelector()
    server = setup_server(selector)

    ser = None
    serial_buf = bytearray()
    last_serial_attempt = 0.0
    start_time = time.monotonic()
    stop_requested = False
    client_sockets = set()

    try:
        while not stop_requested:
            now = time.monotonic()
            if ser is None and now - last_serial_attempt >= 1.5:
                last_serial_attempt = now
                try:
                    ser = open_serial(PORT, BAUD)
                    selector.register(ser.fileno(), selectors.EVENT_READ, data="serial")
                    print("[hermesd] serial connected")
                except Exception as e:
                    ser = None
                    print(f"[hermesd] serial connect failed: {e}")

            events = selector.select(timeout=1)
            for key, _ in events:
                if key.data == "accept":
                    conn_sock, _ = server.accept()
                    conn_sock.setblocking(False)
                    selector.register(conn_sock, selectors.EVENT_READ, data={"buffer": b""})
                    client_sockets.add(conn_sock)
                    continue

                if key.data == "serial":
                    if ser is None:
                        continue
                    try:
                        handle_serial_read(ser, serial_buf, conn)
                    except Exception as e:
                        print(f"[hermesd] serial error: {e}")
                        try:
                            selector.unregister(ser.fileno())
                        except Exception:
                            pass
                        try:
                            ser.close()
                        except Exception:
                            pass
                        ser = None
                    continue

                conn_sock = key.fileobj
                data = key.data
                try:
                    chunk = conn_sock.recv(1024)
                except Exception:
                    chunk = b""

                if not chunk:
                    selector.unregister(conn_sock)
                    conn_sock.close()
                    client_sockets.discard(conn_sock)
                    continue

                buf = data["buffer"] + chunk
                if b"\n" not in buf:
                    data["buffer"] = buf
                    continue

                line, _ = buf.split(b"\n", 1)
                cmd_line = line.decode(errors="replace")
                resp, should_stop = handle_client_command(
                    cmd_line,
                    ser,
                    start_time,
                    len(client_sockets),
                )
                try:
                    conn_sock.sendall((resp + "\n").encode("utf-8"))
                except Exception:
                    pass
                selector.unregister(conn_sock)
                conn_sock.close()
                client_sockets.discard(conn_sock)
                if should_stop:
                    stop_requested = True
                    break

    finally:
        try:
            selector.unregister(server)
        except Exception:
            pass
        try:
            server.close()
        except Exception:
            pass
        for sock in list(client_sockets):
            try:
                selector.unregister(sock)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
        if ser is not None:
            try:
                selector.unregister(ser.fileno())
            except Exception:
                pass
            try:
                ser.close()
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass
        try:
            ensure_socket_unlinked()
        except Exception:
            pass

if __name__ == "__main__":
    main()
