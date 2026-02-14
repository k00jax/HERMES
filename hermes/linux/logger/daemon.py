import glob
import os
import time
import sqlite3
import datetime
import threading
import queue
import socket
import re
import fcntl
import sys
from typing import Optional
import serial
from serial.tools import list_ports
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None
try:
    from backports.zoneinfo import ZoneInfo as BackportZoneInfo
except ImportError:  # pragma: no cover
    BackportZoneInfo = None

PREFERRED_PORT = os.environ.get("HERMES_NRF_PORT", "/dev/hermes-nrf")
PORT = None
BAUD = int(os.environ.get("HERMES_BAUD", "115200"))

RAW_DIR = os.path.expanduser("~/hermes-data/raw")
DB_PATH = os.path.expanduser("~/hermes-data/db/hermes.sqlite3")
SOCK_PATH = "/tmp/hermesd.sock"
NRF_BY_ID_HINT = "usb-Seeed_Studio_XIAO_nRF52840_9FBE2A3ABD93B121-if00"
RAW_RETENTION_DAYS = int(os.environ.get("HERMES_RAW_RETENTION_DAYS", "30"))
RAW_RETENTION_SECS = RAW_RETENTION_DAYS * 86400
RAW_CLEANUP_INTERVAL_SECS = 3600
PARSE_FAIL_MAX_RAW_CHARS = 200
PARSE_FAIL_SUMMARY_INTERVAL_SECS = 600
PARSER_VERSION = "v1"
HEALTH_WINDOW_SECS = 60
HEALTH_MAX_RESPONSE_CHARS = 2000
FRAME_PREFIX_RE = re.compile(r"^[A-Z]{2,8},")

EXPECTED_PREFIXES = {
    "HB": {"period_s": 1.0, "stale_s": 5.0, "dead_s": 30.0},
    "ENV": {"period_s": 1.0, "stale_s": 5.0, "dead_s": 30.0},
    "AIR": {"period_s": 1.0, "stale_s": 5.0, "dead_s": 30.0},
    "LIGHT": {"period_s": 1.0, "stale_s": 5.0, "dead_s": 30.0},
    "MIC": {"period_s": 1.0, "stale_s": 5.0, "dead_s": 30.0},
    "ESP,NET": {"period_s": 5.0, "stale_s": 20.0, "dead_s": 60.0},
}

LOCK_PATH = "/tmp/hermesd.lock"
lock_handle = None
NRF_LOCK_PATH = "/tmp/hermes-nrf.lock"
nrf_lock_handle = None

os.makedirs(RAW_DIR, exist_ok=True)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

stats = {
    "lines_in": 0,
    "last_line_ts": "never",
    "last_error": "none",
    "serial_connected": False,
    "port": PREFERRED_PORT,
    "last_seen_nrf": "never",
    "last_seen_esp": "never",
}
stats_lock = threading.Lock()

ingest_health = {
    "window_start": time.time(),
    "frame_counts_total": {},
    "frame_counts_window": {},
    "last_seen_by_prefix": {},
    "window1m_start": time.time(),
    "corrupt_lines_1m": 0,
    "non_ascii_1m": 0,
    "decode_repl_1m": 0,
    "parse_fail_1m": 0,
}

parse_fail_stats = {
    "total": 0,
    "window_total": 0,
    "window_start": time.time(),
    "by_reason_total": {},
    "by_prefix_total": {},
    "by_reason_window": {},
    "by_prefix_window": {},
}

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

def cleanup_raw_logs(now_ts: float):
    if RAW_RETENTION_DAYS <= 0:
        return
    cutoff = now_ts - RAW_RETENTION_SECS
    removed = 0
    for name in os.listdir(RAW_DIR):
        if not (name.startswith("nrf_") and name.endswith(".log")):
            continue
        path = os.path.join(RAW_DIR, name)
        try:
            if not os.path.isfile(path):
                continue
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except Exception as e:
            log_info(f"[hermesd] raw cleanup failed: {name} err={e}")
    if removed:
        log_info(f"[hermesd] raw cleanup removed={removed}")

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
            seq INTEGER,
            uptime_s INTEGER,
            boot INTEGER,
            reset_reason TEXT
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
        conn.execute("""
        CREATE TABLE IF NOT EXISTS light (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            ts_local TEXT,
            source TEXT NOT NULL,
            light REAL,
            scene REAL,
            roc REAL,
            delta REAL
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS mic_noise (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            ts_local TEXT,
            source TEXT NOT NULL,
            mic_rms REAL,
            mic_peak REAL,
            noise_floor REAL,
            roc REAL,
            delta REAL,
            spike INTEGER,
            sustain INTEGER
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS esp_net (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            ts_local TEXT,
            wifist INTEGER,
            rssi INTEGER,
            ntp INTEGER,
            ip TEXT
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS parse_fail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            ts_local TEXT,
            source TEXT NOT NULL,
            raw TEXT NOT NULL,
            reason TEXT,
            prefix TEXT,
            parser_version TEXT
        );
        """)
        ensure_column(conn, "raw_lines", "ts_local", "TEXT")
        ensure_column(conn, "metrics", "ts_local", "TEXT")
        ensure_column(conn, "oled_status", "ts_local", "TEXT")
        ensure_column(conn, "hb", "ts_local", "TEXT")
        ensure_column(conn, "hb", "uptime_s", "INTEGER")
        ensure_column(conn, "hb", "boot", "INTEGER")
        ensure_column(conn, "hb", "reset_reason", "TEXT")
        ensure_column(conn, "env", "ts_local", "TEXT")
        ensure_column(conn, "air", "ts_local", "TEXT")
        ensure_column(conn, "acks", "ts_local", "TEXT")
        ensure_column(conn, "light", "ts_local", "TEXT")
        ensure_column(conn, "mic_noise", "ts_local", "TEXT")
        ensure_column(conn, "esp_net", "ts_local", "TEXT")
        ensure_column(conn, "parse_fail", "ts_local", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hb_ts ON hb(ts_utc);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hb_local ON hb(ts_local);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_env_ts ON env(ts_utc);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_env_local ON env(ts_local);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_air_ts ON air(ts_utc);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_air_local ON air(ts_local);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_acks_ts ON acks(ts_utc);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_acks_local ON acks(ts_local);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_light_ts ON light(ts_utc);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_light_local ON light(ts_local);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mic_noise_ts ON mic_noise(ts_utc);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mic_noise_local ON mic_noise(ts_local);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_esp_net_ts ON esp_net(ts_utc);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_esp_net_local ON esp_net(ts_local);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_oled_status_ts ON oled_status(ts_utc);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_oled_status_local ON oled_status(ts_local);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_parse_fail_ts ON parse_fail(ts_utc);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_parse_fail_local ON parse_fail(ts_local);")
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

def frame_prefix_label(line: str, kind: Optional[str]) -> str:
    if kind == "ESP" and line.startswith("ESP,NET"):
        return "ESP,NET"
    if kind:
        return kind
    return "UNKNOWN"

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

def parse_int(value: Optional[str]):
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None

def parse_float(value: Optional[str]):
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None

def bump_counter(counter: dict, key: str):
    counter[key] = counter.get(key, 0) + 1

def looks_like_frame(line: str) -> bool:
    return FRAME_PREFIX_RE.match(line) is not None

def parse_ts_to_unix(ts: str):
    try:
        return datetime.datetime.fromisoformat(ts).timestamp()
    except Exception:
        return None

def record_ingest_health(
    ts: str,
    prefix: str,
    *,
    corrupt: bool,
    non_ascii_count: int,
    decode_replacement_count: int,
    parse_failed: bool,
):
    now_ts = time.time()

    with stats_lock:
        elapsed = now_ts - ingest_health["window_start"]
        if elapsed >= 1.0:
            ingest_health["window_start"] = now_ts
            ingest_health["frame_counts_window"] = {}

        elapsed_1m = now_ts - ingest_health["window1m_start"]
        if elapsed_1m >= HEALTH_WINDOW_SECS:
            ingest_health["window1m_start"] = now_ts
            ingest_health["corrupt_lines_1m"] = 0
            ingest_health["non_ascii_1m"] = 0
            ingest_health["decode_repl_1m"] = 0
            ingest_health["parse_fail_1m"] = 0

        bump_counter(ingest_health["frame_counts_total"], prefix)
        bump_counter(ingest_health["frame_counts_window"], prefix)
        ingest_health["last_seen_by_prefix"][prefix] = ts
        stats["last_seen_nrf"] = ts
        if prefix in {"SENS", "LOG", "LIGHT", "ESP,NET"}:
            stats["last_seen_esp"] = ts

        if corrupt:
            ingest_health["corrupt_lines_1m"] += 1
        if non_ascii_count > 0:
            ingest_health["non_ascii_1m"] += non_ascii_count
        if decode_replacement_count > 0:
            ingest_health["decode_repl_1m"] += decode_replacement_count
        if parse_failed:
            ingest_health["parse_fail_1m"] += 1

def format_health_summary():
    with stats_lock:
        now_ts = time.time()
        elapsed = max(now_ts - ingest_health["window_start"], 0.001)
        window_counts = dict(ingest_health["frame_counts_window"])
        last_seen = dict(ingest_health["last_seen_by_prefix"])
        last_seen_nrf = stats.get("last_seen_nrf", "never")
        last_seen_esp = stats.get("last_seen_esp", "never")
        corrupt_lines_1m = ingest_health["corrupt_lines_1m"]
        non_ascii_1m = ingest_health["non_ascii_1m"]
        decode_repl_1m = ingest_health["decode_repl_1m"]
        parse_fail_1m = ingest_health["parse_fail_1m"]

    rates = []
    for prefix, count in window_counts.items():
        fps = count / elapsed
        rates.append((prefix, fps))
    rates.sort(key=lambda item: item[1], reverse=True)
    fps_text = "|".join([f"{prefix}:{fps:.2f}" for prefix, fps in rates[:8]]) if rates else "none"

    parse_total = parse_fail_stats.get("total", 0)
    parse_by_prefix = parse_fail_stats.get("by_prefix_total", {})
    parse_top = sorted(parse_by_prefix.items(), key=lambda item: item[1], reverse=True)[:5]
    parse_text = "|".join([f"{prefix}:{count}" for prefix, count in parse_top]) if parse_top else "none"

    seen_items = sorted(last_seen.items(), key=lambda item: item[0])[:8]
    seen_text = "|".join([f"{prefix}:{ts}" for prefix, ts in seen_items]) if seen_items else "none"

    freshness_tokens = []
    expected_fps_tokens = []
    for prefix, cfg in EXPECTED_PREFIXES.items():
        ts = last_seen.get(prefix)
        fps_for_prefix = window_counts.get(prefix, 0) / elapsed
        expected_fps_tokens.append(f"{prefix}:{fps_for_prefix:.2f}")
        if not ts:
            freshness_tokens.append(
                f"{prefix}:dead(age=never,exp={cfg['period_s']:.1f}s,stale={cfg['stale_s']:.1f}s)"
            )
            continue
        ts_unix = parse_ts_to_unix(ts)
        if ts_unix is None:
            freshness_tokens.append(
                f"{prefix}:unknown(age=bad_ts,exp={cfg['period_s']:.1f}s,stale={cfg['stale_s']:.1f}s)"
            )
            continue
        age = max(now_ts - ts_unix, 0.0)
        if age >= cfg["dead_s"]:
            state = "dead"
        elif age >= cfg["stale_s"]:
            state = "stale"
        else:
            state = "ok"
        freshness_tokens.append(
            f"{prefix}:{state}(age={age:.1f}s,exp={cfg['period_s']:.1f}s,stale={cfg['stale_s']:.1f}s)"
        )
    freshness_text = "|".join(freshness_tokens)
    expected_fps_text = "|".join(expected_fps_tokens)

    summary = (
        "OK "
        f"freshness={freshness_text} "
        f"fps_expected={expected_fps_text} "
        f"fps={fps_text} "
        f"corrupt_lines_1m={corrupt_lines_1m} "
        f"non_ascii_1m={non_ascii_1m} "
        f"decode_repl_1m={decode_repl_1m} "
        f"parse_fail_1m={parse_fail_1m} "
        f"parse_fail_total={parse_total} "
        f"parse_fail_by_prefix={parse_text} "
        f"last_seen_nrf={last_seen_nrf} "
        f"last_seen_esp={last_seen_esp} "
        f"last_seen_prefix={seen_text}"
    )

    summary = summary.replace("\n", " ").replace("\r", " ")
    if len(summary) > HEALTH_MAX_RESPONSE_CHARS:
        summary = summary[: HEALTH_MAX_RESPONSE_CHARS - 9] + " ...trunc"
    return summary

def record_parse_fail(conn: sqlite3.Connection, ts: str, ts_local: str, line: str, reason: str, prefix: str):
    raw = line[:PARSE_FAIL_MAX_RAW_CHARS]
    conn.execute(
        "INSERT INTO parse_fail (ts_utc, ts_local, source, raw, reason, prefix, parser_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ts, ts_local, "nrf", raw, reason, prefix, PARSER_VERSION),
    )
    parse_fail_stats["total"] += 1
    parse_fail_stats["window_total"] += 1
    bump_counter(parse_fail_stats["by_reason_total"], reason)
    bump_counter(parse_fail_stats["by_prefix_total"], prefix)
    bump_counter(parse_fail_stats["by_reason_window"], reason)
    bump_counter(parse_fail_stats["by_prefix_window"], prefix)

def maybe_log_parse_fail_summary(now_ts: float):
    if (now_ts - parse_fail_stats["window_start"]) < PARSE_FAIL_SUMMARY_INTERVAL_SECS:
        return
    window_total = parse_fail_stats["window_total"]
    if window_total:
        reason_items = list(parse_fail_stats["by_reason_window"].items())
        prefix_items = list(parse_fail_stats["by_prefix_window"].items())
        reason_items.sort(key=lambda item: item[1], reverse=True)
        prefix_items.sort(key=lambda item: item[1], reverse=True)
        top_reason = reason_items[0] if reason_items else ("unknown", 0)
        top_prefix = prefix_items[0] if prefix_items else ("unknown", 0)
        minutes = int(PARSE_FAIL_SUMMARY_INTERVAL_SECS / 60)
        log_info(
            f"[hermesd] parse_fail: {window_total} in last {minutes}m, top: {top_reason[0]}={top_reason[1]} ({top_prefix[0]})"
        )
    parse_fail_stats["window_total"] = 0
    parse_fail_stats["by_reason_window"].clear()
    parse_fail_stats["by_prefix_window"].clear()
    parse_fail_stats["window_start"] = now_ts

def insert_typed_frames(conn: sqlite3.Connection, ts: str, ts_local: str, line: str):
    kind, kvs = parse_kv_pairs(line)
    if not kind:
        return
    if kind == "ESP" and line.startswith("ESP,NET"):
        wifist = parse_int(kvs.get("wifist"))
        rssi = parse_int(kvs.get("rssi"))
        ntp = parse_int(kvs.get("ntp"))
        ip = kvs.get("ip")
        if wifist is None and rssi is None and ntp is None and not ip:
            return
        conn.execute(
            "INSERT INTO esp_net (ts_utc, ts_local, wifist, rssi, ntp, ip) VALUES (?, ?, ?, ?, ?, ?)",
            (ts, ts_local, wifist, rssi, ntp, ip),
        )
        return
    if kind == "HB":
        tick = parse_int(kvs.get("tick"))
        seq = parse_int(kvs.get("seq"))
        uptime_s = parse_int(kvs.get("uptime_s"))
        boot = parse_int(kvs.get("boot"))
        reset_reason = kvs.get("reset_reason")
        if tick is None and seq is None and uptime_s is None and boot is None and not reset_reason:
            return
        conn.execute(
            "INSERT INTO hb (ts_utc, ts_local, source, tick_ms, seq, uptime_s, boot, reset_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, ts_local, "nrf", tick, seq, uptime_s, boot, reset_reason),
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
    if kind == "LIGHT":
        light = parse_float(kvs.get("light"))
        scene = parse_float(kvs.get("scene"))
        roc = parse_float(kvs.get("roc"))
        delta = parse_float(kvs.get("delta"))
        if light is None and scene is None and roc is None and delta is None:
            return
        conn.execute(
            "INSERT INTO light (ts_utc, ts_local, source, light, scene, roc, delta) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, ts_local, "nrf", light, scene, roc, delta),
        )
        return
    if kind == "MIC":
        mic_rms = parse_float(kvs.get("mic_rms"))
        mic_peak = parse_float(kvs.get("mic_peak"))
        noise_floor = parse_float(kvs.get("noise_floor"))
        roc = parse_float(kvs.get("roc"))
        delta = parse_float(kvs.get("delta"))
        spike = parse_int(kvs.get("spike"))
        sustain = parse_int(kvs.get("sustain"))
        if (
            mic_rms is None
            and mic_peak is None
            and noise_floor is None
            and roc is None
            and delta is None
            and spike is None
            and sustain is None
        ):
            return
        conn.execute(
            "INSERT INTO mic_noise (ts_utc, ts_local, source, mic_rms, mic_peak, noise_floor, roc, delta, spike, sustain) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, ts_local, "nrf", mic_rms, mic_peak, noise_floor, roc, delta, spike, sustain),
        )
        return

def classify_parse_failure(line: str):
    kind, kvs = parse_kv_pairs(line)
    if not kind:
        return "no_match", None

    prefix = kind
    non_typed = {"NACK", "PROTO", "SYS", "EVT", "BTN", "DBG", "SENS"}
    if prefix in non_typed:
        return None, prefix

    if prefix == "ESP" and line.startswith("ESP,NET"):
        wifist_raw = kvs.get("wifist")
        rssi_raw = kvs.get("rssi")
        ntp_raw = kvs.get("ntp")
        ip_raw = kvs.get("ip")
        wifist = parse_int(wifist_raw)
        rssi = parse_int(rssi_raw)
        ntp = parse_int(ntp_raw)
        if wifist_raw is None and rssi_raw is None and ntp_raw is None and not ip_raw:
            return "missing_fields", "ESP,NET"
        if wifist_raw is not None and wifist is None:
            return "bad_int", "ESP,NET"
        if rssi_raw is not None and rssi is None:
            return "bad_int", "ESP,NET"
        if ntp_raw is not None and ntp is None:
            return "bad_int", "ESP,NET"
        return None, "ESP,NET"

    if prefix == "HB":
        tick_raw = kvs.get("tick")
        seq_raw = kvs.get("seq")
        uptime_raw = kvs.get("uptime_s")
        boot_raw = kvs.get("boot")
        reset_reason_raw = kvs.get("reset_reason")
        tick = parse_int(tick_raw)
        seq = parse_int(seq_raw)
        uptime = parse_int(uptime_raw)
        boot = parse_int(boot_raw)
        if tick_raw is None and seq_raw is None and uptime_raw is None and boot_raw is None and not reset_reason_raw:
            return "missing_fields", prefix
        if tick_raw is not None and tick is None:
            return "bad_int", prefix
        if seq_raw is not None and seq is None:
            return "bad_int", prefix
        if uptime_raw is not None and uptime is None:
            return "bad_int", prefix
        if boot_raw is not None and boot is None:
            return "bad_int", prefix
        return None, prefix

    if prefix == "ENV":
        temp_raw = kvs.get("temp_c")
        hum_raw = kvs.get("hum_pct")
        temp = parse_float(temp_raw)
        hum = parse_float(hum_raw)
        if temp_raw is None and hum_raw is None:
            return "missing_fields", prefix
        if temp_raw is not None and temp is None:
            return "bad_float", prefix
        if hum_raw is not None and hum is None:
            return "bad_float", prefix
        return None, prefix

    if prefix == "AIR":
        eco2_raw = kvs.get("eco2_ppm")
        tvoc_raw = kvs.get("tvoc_ppb")
        eco2 = parse_float(eco2_raw)
        tvoc = parse_float(tvoc_raw)
        if eco2_raw is None and tvoc_raw is None:
            return "missing_fields", prefix
        if eco2_raw is not None and eco2 is None:
            return "bad_float", prefix
        if tvoc_raw is not None and tvoc is None:
            return "bad_float", prefix
        return None, prefix

    if prefix == "ACK":
        ack_kind = kvs.get("kind")
        ack_op = kvs.get("op")
        if not ack_kind and not ack_op:
            return "missing_fields", prefix
        return None, prefix

    if prefix == "LIGHT":
        light_raw = kvs.get("light")
        scene_raw = kvs.get("scene")
        roc_raw = kvs.get("roc")
        delta_raw = kvs.get("delta")
        light = parse_float(light_raw)
        scene = parse_float(scene_raw)
        roc = parse_float(roc_raw)
        delta = parse_float(delta_raw)
        if light_raw is None and scene_raw is None and roc_raw is None and delta_raw is None:
            return "missing_fields", prefix
        if light_raw is not None and light is None:
            return "bad_float", prefix
        if scene_raw is not None and scene is None:
            return "bad_float", prefix
        if roc_raw is not None and roc is None:
            return "bad_float", prefix
        if delta_raw is not None and delta is None:
            return "bad_float", prefix
        return None, prefix

    if prefix == "MIC":
        rms_raw = kvs.get("mic_rms")
        peak_raw = kvs.get("mic_peak")
        floor_raw = kvs.get("noise_floor")
        roc_raw = kvs.get("roc")
        delta_raw = kvs.get("delta")
        spike_raw = kvs.get("spike")
        sustain_raw = kvs.get("sustain")
        rms = parse_float(rms_raw)
        peak = parse_float(peak_raw)
        floor = parse_float(floor_raw)
        roc = parse_float(roc_raw)
        delta = parse_float(delta_raw)
        spike = parse_int(spike_raw)
        sustain = parse_int(sustain_raw)
        if all(raw is None for raw in [rms_raw, peak_raw, floor_raw, roc_raw, delta_raw, spike_raw, sustain_raw]):
            return "missing_fields", prefix
        if rms_raw is not None and rms is None:
            return "bad_float", prefix
        if peak_raw is not None and peak is None:
            return "bad_float", prefix
        if floor_raw is not None and floor is None:
            return "bad_float", prefix
        if roc_raw is not None and roc is None:
            return "bad_float", prefix
        if delta_raw is not None and delta is None:
            return "bad_float", prefix
        if spike_raw is not None and spike is None:
            return "bad_int", prefix
        if sustain_raw is not None and sustain is None:
            return "bad_int", prefix
        return None, prefix

    kind_num, kvs_num = parse_line(line)
    if kind_num and kvs_num:
        return None, prefix

    return "unknown_prefix", prefix

def update_stats(**kwargs):
    with stats_lock:
        for key, value in kwargs.items():
            stats[key] = value

def snapshot_stats():
    with stats_lock:
        return dict(stats)

def log_info(message: str):
    print(message, flush=True)

def resolve_serial_port(preferred: Optional[str]) -> str:
    if preferred and os.path.exists(preferred):
        return preferred

    by_id = f"/dev/serial/by-id/{NRF_BY_ID_HINT}"
    if os.path.exists(by_id):
        return by_id

    for path in glob.glob("/dev/serial/by-id/*9FBE2A3ABD93B121*"):
        return path

    nrf_ports = []
    for port_info in list_ports.comports():
        device = port_info.device
        if not device:
            continue
        vid = getattr(port_info, "vid", None)
        pid = getattr(port_info, "pid", None)
        serial_number = (getattr(port_info, "serial_number", "") or "").upper()

        if vid == 0x2886:
            nrf_ports.append(device)
            continue
        if vid == 0x239A and pid == 0x00C9:
            nrf_ports.append(device)
            continue
        if "9FBE2A3ABD93B121" in serial_number:
            nrf_ports.append(device)

    if nrf_ports:
        return sorted(set(nrf_ports))[0]

    return preferred or PREFERRED_PORT

PORT = resolve_serial_port(PREFERRED_PORT)

def ensure_socket_unlinked():
    if os.path.exists(SOCK_PATH):
        os.unlink(SOCK_PATH)

def acquire_singleton_lock():
    global lock_handle
    lock_handle = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"[hermesd] another instance is already running (lock: {LOCK_PATH})", flush=True)
        raise SystemExit(1)
    lock_handle.seek(0)
    lock_handle.truncate(0)
    lock_handle.write(f"{os.getpid()}\n")
    lock_handle.flush()

def release_singleton_lock():
    global lock_handle
    if not lock_handle:
        return
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        lock_handle.close()
    except Exception:
        pass
    lock_handle = None

def acquire_nrf_lock():
    global nrf_lock_handle
    nrf_lock_handle = open(NRF_LOCK_PATH, "w")
    try:
        fcntl.flock(nrf_lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"[hermesd] nrf serial lock busy (lock: {NRF_LOCK_PATH})", flush=True)
        sys.exit(2)
    nrf_lock_handle.seek(0)
    nrf_lock_handle.truncate(0)
    nrf_lock_handle.write(f"{os.getpid()}\n")
    nrf_lock_handle.flush()

def release_nrf_lock():
    global nrf_lock_handle
    if not nrf_lock_handle:
        return
    try:
        fcntl.flock(nrf_lock_handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        nrf_lock_handle.close()
    except Exception:
        pass
    nrf_lock_handle = None

def handle_line(conn: sqlite3.Connection, line: str):
    dt = utc_now()
    ts = dt.isoformat()
    ts_local = local_now().isoformat()
    non_ascii_count = sum(1 for c in line if ord(c) > 127)
    decode_replacement_count = line.count("\ufffd")
    corrupt = not looks_like_frame(line)

    with open(raw_path(dt), "a", encoding="utf-8") as f:
        f.write(f"{ts}\t{line}\n")

    conn.execute(
        "INSERT INTO raw_lines (ts_utc, ts_local, source, line) VALUES (?, ?, ?, ?)",
        (ts, ts_local, "nrf", line),
    )

    if corrupt:
        record_parse_fail(conn, ts, ts_local, line, "corrupt_prefix", "CORRUPT")
        record_ingest_health(
            ts,
            "CORRUPT",
            corrupt=True,
            non_ascii_count=non_ascii_count,
            decode_replacement_count=decode_replacement_count,
            parse_failed=True,
        )
        maybe_log_parse_fail_summary(time.time())
        conn.commit()
        with stats_lock:
            stats["lines_in"] += 1
            stats["last_line_ts"] = ts
            if stats["lines_in"] % 50 == 0:
                log_info(f"[hermesd] lines_in={stats['lines_in']}")
        return

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
    reason, prefix = classify_parse_failure(line)
    if reason:
        record_parse_fail(conn, ts, ts_local, line, reason, prefix or "")
    effective_prefix = prefix or frame_prefix_label(line, kind)
    record_ingest_health(
        ts,
        effective_prefix,
        corrupt=False,
        non_ascii_count=non_ascii_count,
        decode_replacement_count=decode_replacement_count,
        parse_failed=bool(reason),
    )
    maybe_log_parse_fail_summary(time.time())
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
    last_cleanup = 0.0
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

            now_ts = time.time()
            if (now_ts - last_cleanup) >= RAW_CLEANUP_INTERVAL_SECS:
                cleanup_raw_logs(now_ts)
                last_cleanup = now_ts

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
            f"last_seen_nrf={snap['last_seen_nrf']} "
            f"last_seen_esp={snap['last_seen_esp']} "
            f"last_error={snap['last_error']}",
            False,
        )
    if cmd == "HEALTH":
        try:
            return format_health_summary(), False
        except Exception as e:
            update_stats(last_error=f"health format failed: {e}")
            return f"ERR health failed: {e}", False
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
                try:
                    while b"\n" not in data and len(data) < 4096:
                        chunk = conn.recv(1024)
                        if not chunk:
                            break
                        data += chunk
                except socket.timeout:
                    try:
                        conn.sendall(b"ERR timeout\n")
                    except Exception:
                        pass
                    continue
                except Exception as e:
                    update_stats(last_error=f"socket recv failed: {e}")
                    try:
                        conn.sendall(b"ERR recv failed\n")
                    except Exception:
                        pass
                    continue

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
    acquire_singleton_lock()
    acquire_nrf_lock()
    try:
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
    finally:
        release_nrf_lock()
        release_singleton_lock()

if __name__ == "__main__":
    main()
