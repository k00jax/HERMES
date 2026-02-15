import json
import re
import sqlite3
import subprocess
import os
import io
import math
import time
import datetime
import threading
from pathlib import Path
from typing import Dict, List

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.middleware.cors import CORSMiddleware

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt

APP = FastAPI(title="HERMES Dashboard", version="0.1.0")

cors_origins_env = os.environ.get("HERMES_DASHBOARD_CORS_ORIGINS", "")
if cors_origins_env.strip():
  APP.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in cors_origins_env.split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
  )

BASE_DIR = Path("/home/odroid/hermes-src/hermes")
CLIENT_PATH = BASE_DIR / "linux/logger/client.py"
DOCTOR_PATH = BASE_DIR / "tools/hermes-doctor.sh"
DB_PATH = Path("/home/odroid/hermes-data/db/hermes.sqlite3")
DB_TIMEOUT_SECS = float(os.environ.get("HERMES_DB_TIMEOUT_SECS", "2.0"))
MAX_CACHE_KEYS = int(os.environ.get("HERMES_CHART_CACHE_KEYS", "64"))

TABLES = ("hb", "env", "air", "light", "mic_noise", "esp_net")
FRESHNESS_KEYS = ("HB", "ENV", "AIR", "LIGHT", "MIC", "ESP,NET")

SERIES_MAP = {
  "env_temp": {"table": "env", "column": "temp_c", "label": "Temp (C)", "color": "#4fc3f7", "stepped": False},
  "env_hum": {"table": "env", "column": "hum_pct", "label": "Humidity (%)", "color": "#81c784", "stepped": False},
  "air_eco2": {"table": "air", "column": "eco2_ppm", "label": "eCO2 (ppm)", "color": "#ffb74d", "stepped": False},
  "air_tvoc": {"table": "air", "column": "tvoc_ppb", "label": "TVOC (ppb)", "color": "#ba68c8", "stepped": False},
  "esp_rssi": {"table": "esp_net", "column": "rssi", "label": "RSSI (dBm)", "color": "#ef5350", "stepped": False},
  "esp_wifist": {"table": "esp_net", "column": "wifist", "label": "WiFi State", "color": "#90a4ae", "stepped": True},
}

CHART_CACHE_TTL_SECS = 5.0
chart_cache: Dict[str, tuple] = {}
chart_cache_lock = threading.Lock()
chart_render_lock = threading.Lock()
chart_metrics_lock = threading.Lock()
chart_render_ms_samples: List[float] = []
CHART_RENDER_SAMPLES_MAX = int(os.environ.get("HERMES_CHART_RENDER_SAMPLES_MAX", "256"))

db_locked_count = 0
db_locked_count_lock = threading.Lock()

READY_MAX_AGE_SECS = int(os.environ.get("HERMES_READY_MAX_AGE_SECS", "180"))
WATCHDOG_STATE_DIR = Path(os.environ.get("HERMES_DASHBOARD_WATCHDOG_STATE_DIR", "/home/odroid/hermes-data/dashboard-watchdog"))
WATCHDOG_RESTART_COUNT_FILE = WATCHDOG_STATE_DIR / "restart_count"
EVENT_POSTS_PER_MIN = int(os.environ.get("HERMES_EVENTS_POSTS_PER_MIN", "30"))

event_post_rate_lock = threading.Lock()
event_post_rate: Dict[str, List[float]] = {}


def run_cmd(args: List[str], timeout_sec: float) -> Dict[str, object]:
    try:
        proc = subprocess.run(
            args,
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": 124, "stdout": "", "stderr": "timeout"}
    return {
        "ok": proc.returncode == 0,
        "code": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def parse_ok_kv(line: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    text = line.strip()
    if text.startswith("OK "):
        text = text[3:]
    for token in text.split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        out[key] = value
    return out


def parse_freshness(raw_health: str) -> Dict[str, str]:
    result = {key: "unknown" for key in FRESHNESS_KEYS}
    match = re.search(r"freshness=([^ ]+)", raw_health)
    if not match:
        return result
    segment = match.group(1)
    for part in segment.split("|"):
        if ":" not in part:
            continue
        prefix, rest = part.split(":", 1)
        status = rest.split("(", 1)[0].strip()
        if prefix in result:
            result[prefix] = status or "unknown"
    return result


def cutoff_iso(minutes: int) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes)
    return dt.isoformat()


def downsample_points(points: List[Dict[str, object]], max_points: int = 300) -> List[Dict[str, object]]:
    if len(points) <= max_points:
        return points
    stride = math.ceil(len(points) / max_points)
    return points[::stride]


def open_db() -> sqlite3.Connection:
  conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECS)
  conn.execute("PRAGMA journal_mode=WAL;")
  conn.execute("PRAGMA synchronous=NORMAL;")
  return conn


def parse_iso8601_utc(raw: str) -> datetime.datetime:
  text = raw.strip()
  if text.endswith("Z"):
    text = text[:-1] + "+00:00"
  dt = datetime.datetime.fromisoformat(text)
  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=datetime.timezone.utc)
  return dt.astimezone(datetime.timezone.utc)


def current_table_ages(conn: sqlite3.Connection) -> Dict[str, float]:
  now = datetime.datetime.now(datetime.timezone.utc)
  ages: Dict[str, float] = {}
  for table in TABLES:
    row = conn.execute(f"SELECT ts_utc FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
    if not row or row[0] is None:
      ages[table] = -1.0
      continue
    try:
      ts = parse_iso8601_utc(str(row[0]))
      ages[table] = max(0.0, (now - ts).total_seconds())
    except Exception:
      ages[table] = -1.0
  return ages


def get_chart_render_p95_ms() -> float:
  with chart_metrics_lock:
    if not chart_render_ms_samples:
      return 0.0
    values = sorted(chart_render_ms_samples)
  idx = int((len(values) - 1) * 0.95)
  idx = min(max(idx, 0), len(values) - 1)
  return float(values[idx])


def get_watchdog_restart_count() -> int:
  try:
    text = WATCHDOG_RESTART_COUNT_FILE.read_text().strip()
  except Exception:
    return 0
  return int(text) if text.isdigit() else 0


def ensure_events_table(conn: sqlite3.Connection) -> None:
  conn.execute(
    """
    CREATE TABLE IF NOT EXISTS events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts_utc TEXT NOT NULL,
      ts_local TEXT,
      kind TEXT NOT NULL,
      severity TEXT NOT NULL,
      source TEXT,
      message TEXT NOT NULL,
      data_json TEXT,
      dedupe_key TEXT
    );
    """
  )
  conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts_utc);")
  conn.execute("CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);")
  conn.execute("CREATE INDEX IF NOT EXISTS idx_events_severity ON events(severity);")
  conn.execute("CREATE INDEX IF NOT EXISTS idx_events_dedupe ON events(dedupe_key);")


def ensure_event_state_table(conn: sqlite3.Connection) -> None:
  conn.execute(
    """
    CREATE TABLE IF NOT EXISTS event_state (
      target_type TEXT NOT NULL,
      target_value TEXT NOT NULL,
      ack_ts_utc TEXT,
      snooze_until_utc TEXT,
      note TEXT,
      updated_ts_utc TEXT NOT NULL,
      PRIMARY KEY (target_type, target_value)
    );
    """
  )
  conn.execute("CREATE INDEX IF NOT EXISTS idx_event_state_updated ON event_state(updated_ts_utc);")


def now_utc_iso() -> str:
  return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso_utc_maybe(raw: object):
  if raw is None:
    return None
  text = str(raw).strip()
  if not text:
    return None
  try:
    return parse_iso8601_utc(text)
  except Exception:
    return None


def normalize_event_state(row: sqlite3.Row = None) -> Dict[str, object]:
  if row is None:
    return {
      "ack_ts_utc": None,
      "snooze_until_utc": None,
      "note": None,
      "updated_ts_utc": None,
      "acked": False,
      "snoozed": False,
    }
  item = dict(row)
  now = datetime.datetime.now(datetime.timezone.utc)
  snooze_until = parse_iso_utc_maybe(item.get("snooze_until_utc"))
  return {
    "ack_ts_utc": item.get("ack_ts_utc"),
    "snooze_until_utc": item.get("snooze_until_utc"),
    "note": item.get("note"),
    "updated_ts_utc": item.get("updated_ts_utc"),
    "acked": bool(item.get("ack_ts_utc")),
    "snoozed": bool(snooze_until and snooze_until > now),
  }


def upsert_event_state(
    conn: sqlite3.Connection,
    *,
    target_type: str,
    target_value: str,
    ack_ts_utc: str = None,
    snooze_until_utc: str = None,
    note: str = None,
) -> None:
  ensure_event_state_table(conn)
  updated_ts = now_utc_iso()
  conn.execute(
    """
    INSERT INTO event_state (target_type, target_value, ack_ts_utc, snooze_until_utc, note, updated_ts_utc)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(target_type, target_value) DO UPDATE SET
      ack_ts_utc = COALESCE(excluded.ack_ts_utc, event_state.ack_ts_utc),
      snooze_until_utc = COALESCE(excluded.snooze_until_utc, event_state.snooze_until_utc),
      note = CASE
        WHEN excluded.note IS NOT NULL THEN excluded.note
        ELSE event_state.note
      END,
      updated_ts_utc = excluded.updated_ts_utc
    """,
    (target_type, target_value, ack_ts_utc, snooze_until_utc, note, updated_ts),
  )


def resolve_event_states(conn: sqlite3.Connection, rows: List[sqlite3.Row]) -> Dict[str, Dict[str, object]]:
  ensure_event_state_table(conn)
  id_keys = {str(dict(r).get("id")) for r in rows if dict(r).get("id") is not None}
  dedupe_keys = {str(dict(r).get("dedupe_key")) for r in rows if dict(r).get("dedupe_key")}

  by_id_raw: Dict[str, Dict[str, object]] = {}
  by_dedupe_raw: Dict[str, Dict[str, object]] = {}

  if id_keys:
    placeholders = ",".join(["?"] * len(id_keys))
    state_rows = conn.execute(
      f"SELECT target_type, target_value, ack_ts_utc, snooze_until_utc, note, updated_ts_utc FROM event_state WHERE target_type='id' AND target_value IN ({placeholders})",
      tuple(sorted(id_keys)),
    ).fetchall()
    by_id_raw = {str(row["target_value"]): dict(row) for row in state_rows}

  if dedupe_keys:
    placeholders = ",".join(["?"] * len(dedupe_keys))
    state_rows = conn.execute(
      f"SELECT target_type, target_value, ack_ts_utc, snooze_until_utc, note, updated_ts_utc FROM event_state WHERE target_type='dedupe' AND target_value IN ({placeholders})",
      tuple(sorted(dedupe_keys)),
    ).fetchall()
    by_dedupe_raw = {str(row["target_value"]): dict(row) for row in state_rows}

  out: Dict[str, Dict[str, object]] = {}
  for row in rows:
    item = dict(row)
    event_id = str(item.get("id"))
    dedupe_key = str(item.get("dedupe_key")) if item.get("dedupe_key") else ""

    dedupe_row = by_dedupe_raw.get(dedupe_key) if dedupe_key else None
    id_row = by_id_raw.get(event_id)

    merged_raw = {
      "ack_ts_utc": dedupe_row.get("ack_ts_utc") if dedupe_row else None,
      "snooze_until_utc": dedupe_row.get("snooze_until_utc") if dedupe_row else None,
      "note": dedupe_row.get("note") if dedupe_row else None,
      "updated_ts_utc": dedupe_row.get("updated_ts_utc") if dedupe_row else None,
    }
    if id_row:
      for key in ("ack_ts_utc", "snooze_until_utc", "note", "updated_ts_utc"):
        if id_row.get(key) is not None:
          merged_raw[key] = id_row.get(key)

    out[event_id] = normalize_event_state(merged_raw)
  return out


def apply_state_to_event_rows(conn: sqlite3.Connection, rows: List[sqlite3.Row]) -> List[Dict[str, object]]:
  states = resolve_event_states(conn, rows)
  output: List[Dict[str, object]] = []
  for row in rows:
    item = event_row_to_dict(row)
    state = states.get(str(item.get("id")), normalize_event_state(None))
    item["state"] = state
    item["acked"] = bool(state.get("acked"))
    item["snoozed"] = bool(state.get("snoozed"))
    item["note"] = state.get("note")
    output.append(item)
  return output


def enforce_event_post_rate_limit(request: Request) -> None:
  if EVENT_POSTS_PER_MIN <= 0:
    return
  host = "unknown"
  if request.client and request.client.host:
    host = str(request.client.host)
  now = time.time()
  window_start = now - 60.0
  with event_post_rate_lock:
    arr = [ts for ts in event_post_rate.get(host, []) if ts >= window_start]
    if len(arr) >= EVENT_POSTS_PER_MIN:
      raise HTTPException(status_code=429, detail="rate limit exceeded")
    arr.append(now)
    event_post_rate[host] = arr


def event_row_to_dict(row: sqlite3.Row) -> Dict[str, object]:
  item = dict(row)
  raw = item.get("data_json")
  if isinstance(raw, str) and raw.strip():
    try:
      item["data"] = json.loads(raw)
    except Exception:
      item["data"] = raw
  else:
    item["data"] = None
  return item


def build_ready_state() -> Dict[str, object]:
  failures: List[str] = []
  table_age_seconds: Dict[str, float] = {table: -1.0 for table in TABLES}
  db_exists = DB_PATH.exists()
  db_readable = False

  if not db_exists:
    failures.append("db_missing")
  else:
    try:
      with open_db() as conn:
        conn.execute("SELECT 1").fetchone()
        table_age_seconds = current_table_ages(conn)
        db_readable = True
    except sqlite3.OperationalError as exc:
      if "locked" in str(exc).lower():
        failures.append("db_locked")
      else:
        failures.append("db_operational_error")
    except Exception:
      failures.append("db_unreadable")

  for table, age in table_age_seconds.items():
    if age < 0:
      failures.append(f"stale_{table}:missing")
      continue
    if age > READY_MAX_AGE_SECS:
      failures.append(f"stale_{table}:{int(age)}s")

  status_cmd = run_cmd(["python3", str(CLIENT_PATH), "status"], timeout_sec=2)
  if not status_cmd["ok"]:
    failures.append("logger_status_failed")

  return {
    "ready": len(failures) == 0,
    "failures": failures,
    "db_exists": db_exists,
    "db_readable": db_readable,
    "logger_ok": bool(status_cmd["ok"]),
    "table_age_seconds": table_age_seconds,
    "stale_threshold_seconds": READY_MAX_AGE_SECS,
  }


def query_series(series: str, minutes: int) -> List[Dict[str, object]]:
    cfg = SERIES_MAP.get(series)
    if not cfg:
        raise HTTPException(status_code=404, detail="series not allowed")
    if not DB_PATH.exists():
        return []

    cutoff = cutoff_iso(minutes)
    sql = (
        f"SELECT ts_utc, {cfg['column']} "
        f"FROM {cfg['table']} "
        f"WHERE ts_utc >= ? AND {cfg['column']} IS NOT NULL "
        f"ORDER BY ts_utc ASC"
    )

    points: List[Dict[str, object]] = []
    try:
      with open_db() as conn:
        rows = conn.execute(sql, (cutoff,)).fetchall()
    except sqlite3.OperationalError as exc:
      if "locked" in str(exc).lower():
        global db_locked_count
        with db_locked_count_lock:
          db_locked_count += 1
        return []
      raise

    for ts_raw, value in rows:
      try:
        fv = float(value)
      except (TypeError, ValueError):
        continue
      if math.isnan(fv):
        continue
      ts_text = str(ts_raw)
      if ts_text.endswith("+00:00"):
        ts_text = ts_text[:-6] + "Z"
      points.append({"t": ts_text, "v": fv})

    return downsample_points(points, max_points=300)


def render_sparkline_png(series: str, minutes: int, points: List[Dict[str, object]]) -> bytes:
  cfg = SERIES_MAP[series]
  render_start = time.perf_counter()
  try:
    with chart_render_lock:
      fig, ax = plt.subplots(figsize=(4.6, 1.4), dpi=110)
      fig.patch.set_facecolor("#151c24")
      ax.set_facecolor("#151c24")

    if points:
      x = list(range(len(points)))
      y = [float(item["v"]) for item in points]
      ys = [float(item["v"]) for item in points if item.get("v") is not None]

      if ys:
        if cfg["stepped"]:
          lo = min(ys) - 0.5
          hi = max(ys) + 0.5
          if lo == hi:
            lo -= 0.5
            hi += 0.5
          ax.set_ylim(lo, hi)
        else:
          min_span_total_by_series = {
            "env_temp": 0.6,
            "env_hum": 4.0,
            "air_eco2": 60.0,
            "air_tvoc": 40.0,
            "esp_rssi": 10.0,
          }
          robust_q_by_series = {
            "esp_rssi": 0.90,
          }

          center = sum(ys) / len(ys)
          abs_devs = sorted(abs(v - center) for v in ys)
          q = float(robust_q_by_series.get(series, 0.95))
          q = min(max(q, 0.0), 1.0)
          q_index = int((len(abs_devs) - 1) * q)
          q_index = min(max(q_index, 0), len(abs_devs) - 1)
          robust_dev = abs_devs[q_index]

          pad = 0.10
          half_span = robust_dev * (1.0 + pad)
          min_span_total = float(min_span_total_by_series.get(series, 0.2))
          half_span = max(half_span, min_span_total / 2.0)
          half_span = max(half_span, 1e-6)

          lo = center - half_span
          hi = center + half_span
          y_min = min(ys)
          y_max = max(ys)

          if y_min < lo or y_max > hi:
            full_range = max(1e-9, y_max - y_min)
            pad2 = full_range * 0.10
            lo = min(lo, y_min - pad2)
            hi = max(hi, y_max + pad2)

          if lo == hi:
            lo -= 0.5
            hi += 0.5

          ax.set_ylim(lo, hi)

      ax.plot(
        x,
        y,
        color=cfg["color"],
        linewidth=1.8,
        drawstyle="steps-post" if cfg["stepped"] else "default",
      )
      ax.fill_between(x, y, color=cfg["color"], alpha=0.13)
      last_v = y[-1]
      min_v = min(y)
      max_v = max(y)
      ax.text(0.01, 0.98, cfg["label"], transform=ax.transAxes, va="top", ha="left", color="#9fb3c8", fontsize=8)
      ax.text(0.99, 0.98, f"last {last_v:.1f}", transform=ax.transAxes, va="top", ha="right", color="#e8eef5", fontsize=8)
      ax.text(0.99, 0.03, f"min {min_v:.1f}  max {max_v:.1f}  {minutes}m", transform=ax.transAxes, va="bottom", ha="right", color="#8ea1b3", fontsize=7)
    else:
      ax.text(0.5, 0.5, f"{cfg['label']}\nno data", transform=ax.transAxes, va="center", ha="center", color="#8ea1b3", fontsize=9)

    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
      spine.set_visible(False)

      fig.tight_layout(pad=0.2)
      buf = io.BytesIO()
      fig.savefig(buf, format="png", facecolor=fig.get_facecolor(), edgecolor=fig.get_facecolor())
      plt.close(fig)
      return buf.getvalue()
  finally:
    render_ms = (time.perf_counter() - render_start) * 1000.0
    with chart_metrics_lock:
      chart_render_ms_samples.append(render_ms)
      if len(chart_render_ms_samples) > CHART_RENDER_SAMPLES_MAX:
        del chart_render_ms_samples[: len(chart_render_ms_samples) - CHART_RENDER_SAMPLES_MAX]


@APP.get("/api/status")
def api_status() -> Dict[str, object]:
    cmd = run_cmd(["python3", str(CLIENT_PATH), "status"], timeout_sec=2)
    raw = cmd["stdout"] or cmd["stderr"]
    parsed = parse_ok_kv(raw) if isinstance(raw, str) else {}
    return {
        "ok": cmd["ok"],
        "code": cmd["code"],
        "raw": raw,
        "daemon_running": bool(cmd["ok"]),
        "port": parsed.get("port", "unknown"),
        "lines_in": parsed.get("lines_in", "unknown"),
        "last_error": parsed.get("last_error", "unknown"),
    }


@APP.get("/api/health")
def api_health() -> Dict[str, object]:
    cmd = run_cmd(["python3", str(CLIENT_PATH), "health"], timeout_sec=2)
    raw = cmd["stdout"] or cmd["stderr"]
    return {
        "ok": cmd["ok"],
        "code": cmd["code"],
        "raw": raw,
        "freshness": parse_freshness(raw if isinstance(raw, str) else ""),
    }


@APP.get("/api/latest/{table}")
def api_latest(table: str, limit: int = Query(20, ge=1, le=200)) -> Dict[str, object]:
    if table not in TABLES:
        raise HTTPException(status_code=404, detail="table not allowed")
    if not DB_PATH.exists():
        raise HTTPException(status_code=404, detail="db missing")

    try:
      with open_db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    except sqlite3.OperationalError as exc:
      if "locked" in str(exc).lower():
        global db_locked_count
        with db_locked_count_lock:
          db_locked_count += 1
        return {"table": table, "limit": limit, "rows": []}
      raise

    return {
        "table": table,
        "limit": limit,
        "rows": [dict(row) for row in rows],
    }


@APP.get("/api/events/latest")
def api_events_latest(
    limit: int = Query(50, ge=1, le=200),
    severity: str = Query(""),
    kind: str = Query(""),
  since_id: int = Query(0, ge=0),
) -> Dict[str, object]:
    try:
      with open_db() as conn:
        ensure_events_table(conn)
        conn.row_factory = sqlite3.Row
        where: List[str] = []
        params: List[object] = []
        if since_id > 0:
          where.append("id > ?")
          params.append(since_id)
        if severity.strip():
          where.append("severity = ?")
          params.append(severity.strip())
        if kind.strip():
          where.append("kind = ?")
          params.append(kind.strip())
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        sql = (
          "SELECT id, ts_utc, ts_local, kind, severity, source, message, data_json, dedupe_key "
          f"FROM events{where_sql} ORDER BY id DESC LIMIT ?"
        )
        rows = conn.execute(sql, (*params, limit)).fetchall()
        rows_out = apply_state_to_event_rows(conn, rows)
    except sqlite3.OperationalError as exc:
      if "locked" in str(exc).lower():
        return {"limit": limit, "rows": []}
      raise
    return {"limit": limit, "rows": rows_out}


@APP.get("/api/events")
def api_events_since(
    since_id: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    severity: str = Query(""),
    kind: str = Query(""),
) -> Dict[str, object]:
    try:
      with open_db() as conn:
        ensure_events_table(conn)
        conn.row_factory = sqlite3.Row
        where: List[str] = ["id > ?"]
        params: List[object] = [since_id]
        if severity.strip():
          where.append("severity = ?")
          params.append(severity.strip())
        if kind.strip():
          where.append("kind = ?")
          params.append(kind.strip())
        where_sql = " WHERE " + " AND ".join(where)
        sql = (
          "SELECT id, ts_utc, ts_local, kind, severity, source, message, data_json, dedupe_key "
          f"FROM events{where_sql} ORDER BY id ASC LIMIT ?"
        )
        rows = conn.execute(sql, (*params, limit)).fetchall()
        rows_out = apply_state_to_event_rows(conn, rows)
    except sqlite3.OperationalError as exc:
      if "locked" in str(exc).lower():
        return {"since_id": since_id, "rows": []}
      raise
    return {"since_id": since_id, "rows": rows_out}


@APP.post("/api/events/ack")
def api_events_ack(request: Request, payload: Dict[str, object] = Body(...)) -> Dict[str, object]:
    enforce_event_post_rate_limit(request)
    event_id = payload.get("id")
    dedupe_key = str(payload.get("dedupe_key") or "").strip()
    if event_id is None and not dedupe_key:
      raise HTTPException(status_code=400, detail="id or dedupe_key required")

    with open_db() as conn:
      ensure_events_table(conn)
      ensure_event_state_table(conn)
      target_type = "dedupe" if dedupe_key else "id"
      target_value = dedupe_key if dedupe_key else str(int(event_id))
      upsert_event_state(conn, target_type=target_type, target_value=target_value, ack_ts_utc=now_utc_iso())
      conn.commit()
    return {"ok": True, "target_type": target_type, "target_value": target_value}


@APP.post("/api/events/snooze")
def api_events_snooze(request: Request, payload: Dict[str, object] = Body(...)) -> Dict[str, object]:
    enforce_event_post_rate_limit(request)
    dedupe_key = str(payload.get("dedupe_key") or "").strip()
    event_id = payload.get("id")
    seconds_raw = payload.get("seconds", 1800)
    try:
      seconds = int(seconds_raw)
    except Exception:
      raise HTTPException(status_code=400, detail="seconds must be integer")
    if seconds < 1 or seconds > 7 * 24 * 3600:
      raise HTTPException(status_code=400, detail="seconds out of range")
    if not dedupe_key and event_id is None:
      raise HTTPException(status_code=400, detail="dedupe_key or id required")

    snooze_until = (
      datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    with open_db() as conn:
      ensure_events_table(conn)
      ensure_event_state_table(conn)
      target_type = "dedupe" if dedupe_key else "id"
      target_value = dedupe_key if dedupe_key else str(int(event_id))
      upsert_event_state(
        conn,
        target_type=target_type,
        target_value=target_value,
        snooze_until_utc=snooze_until,
      )
      conn.commit()
    return {
      "ok": True,
      "target_type": target_type,
      "target_value": target_value,
      "snooze_until_utc": snooze_until,
    }


@APP.post("/api/events/note")
def api_events_note(request: Request, payload: Dict[str, object] = Body(...)) -> Dict[str, object]:
    enforce_event_post_rate_limit(request)
    event_id = payload.get("id")
    if event_id is None:
      raise HTTPException(status_code=400, detail="id required")
    note = str(payload.get("note") or "").strip()
    if len(note) > 400:
      raise HTTPException(status_code=400, detail="note too long")

    with open_db() as conn:
      ensure_events_table(conn)
      ensure_event_state_table(conn)
      upsert_event_state(conn, target_type="id", target_value=str(int(event_id)), note=note)
      conn.commit()
    return {"ok": True, "target_type": "id", "target_value": str(int(event_id)), "note": note}


@APP.post("/api/events/ack_bulk")
def api_events_ack_bulk(request: Request, payload: Dict[str, object] = Body(...)) -> Dict[str, object]:
    enforce_event_post_rate_limit(request)
    ids_raw = payload.get("ids") or []
    dedupe_raw = payload.get("dedupe_keys") or []

    ids: List[int] = []
    for value in ids_raw:
      try:
        ids.append(int(value))
      except Exception:
        continue
    dedupe_keys = [str(value).strip() for value in dedupe_raw if str(value).strip()]

    if not ids and not dedupe_keys:
      raise HTTPException(status_code=400, detail="ids or dedupe_keys required")

    ack_ts = now_utc_iso()
    with open_db() as conn:
      ensure_events_table(conn)
      ensure_event_state_table(conn)
      for event_id in sorted(set(ids)):
        upsert_event_state(conn, target_type="id", target_value=str(event_id), ack_ts_utc=ack_ts)
      for dedupe_key in sorted(set(dedupe_keys)):
        upsert_event_state(conn, target_type="dedupe", target_value=dedupe_key, ack_ts_utc=ack_ts)
      conn.commit()
    return {"ok": True, "acked_ids": len(set(ids)), "acked_dedupe": len(set(dedupe_keys))}


@APP.post("/api/events/snooze_bulk")
def api_events_snooze_bulk(request: Request, payload: Dict[str, object] = Body(...)) -> Dict[str, object]:
    enforce_event_post_rate_limit(request)
    ids_raw = payload.get("ids") or []
    dedupe_raw = payload.get("dedupe_keys") or []
    seconds_raw = payload.get("seconds", 1800)
    try:
      seconds = int(seconds_raw)
    except Exception:
      raise HTTPException(status_code=400, detail="seconds must be integer")
    if seconds < 1 or seconds > 7 * 24 * 3600:
      raise HTTPException(status_code=400, detail="seconds out of range")

    ids: List[int] = []
    for value in ids_raw:
      try:
        ids.append(int(value))
      except Exception:
        continue
    dedupe_keys = [str(value).strip() for value in dedupe_raw if str(value).strip()]

    if not ids and not dedupe_keys:
      raise HTTPException(status_code=400, detail="ids or dedupe_keys required")

    snooze_until = (
      datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    with open_db() as conn:
      ensure_events_table(conn)
      ensure_event_state_table(conn)
      for event_id in sorted(set(ids)):
        upsert_event_state(conn, target_type="id", target_value=str(event_id), snooze_until_utc=snooze_until)
      for dedupe_key in sorted(set(dedupe_keys)):
        upsert_event_state(conn, target_type="dedupe", target_value=dedupe_key, snooze_until_utc=snooze_until)
      conn.commit()
    return {
      "ok": True,
      "snoozed_ids": len(set(ids)),
      "snoozed_dedupe": len(set(dedupe_keys)),
      "snooze_until_utc": snooze_until,
    }


@APP.post("/api/events/clear_snooze_kind")
def api_events_clear_snooze_kind(request: Request, payload: Dict[str, object] = Body(...)) -> Dict[str, object]:
    enforce_event_post_rate_limit(request)
    kind = str(payload.get("kind") or "").strip()
    if not kind:
      raise HTTPException(status_code=400, detail="kind required")

    updated_ts = now_utc_iso()
    cleared = 0
    with open_db() as conn:
      ensure_events_table(conn)
      ensure_event_state_table(conn)

      dedupe_rows = conn.execute(
        "SELECT DISTINCT dedupe_key FROM events WHERE kind=? AND dedupe_key IS NOT NULL AND dedupe_key<>''",
        (kind,),
      ).fetchall()
      dedupe_keys = [str(row[0]) for row in dedupe_rows if row and row[0]]
      for dedupe_key in dedupe_keys:
        cur = conn.execute(
          "UPDATE event_state SET snooze_until_utc=NULL, updated_ts_utc=? WHERE target_type='dedupe' AND target_value=?",
          (updated_ts, dedupe_key),
        )
        cleared += int(cur.rowcount or 0)

      id_rows = conn.execute("SELECT id FROM events WHERE kind=?", (kind,)).fetchall()
      ids = [str(int(row[0])) for row in id_rows if row and row[0] is not None]
      if ids:
        placeholders = ",".join(["?"] * len(ids))
        cur = conn.execute(
          f"UPDATE event_state SET snooze_until_utc=NULL, updated_ts_utc=? WHERE target_type='id' AND target_value IN ({placeholders})",
          (updated_ts, *ids),
        )
        cleared += int(cur.rowcount or 0)

      conn.commit()

    return {"ok": True, "kind": kind, "cleared": cleared}


@APP.get("/api/diag", response_class=PlainTextResponse)
def api_diag() -> PlainTextResponse:
    cmd = run_cmd(["bash", str(DOCTOR_PATH)], timeout_sec=15)
    output = cmd["stdout"] or cmd["stderr"] or ""
    return PlainTextResponse(str(output))


@APP.get("/api/ts/{series}")
def api_ts(series: str, minutes: int = Query(60, ge=1, le=24 * 60)) -> Dict[str, object]:
    points = query_series(series, minutes)
    vals = [p["v"] for p in points if isinstance(p.get("v"), (int, float))]
    stats = None
    if vals:
        stats = {
            "last": float(vals[-1]),
            "min": float(min(vals)),
            "max": float(max(vals)),
            "count": int(len(vals)),
        }
    return {"series": series, "minutes": minutes, "points": points, "stats": stats}


@APP.get("/chart/{series}.png")
def chart_png(series: str, minutes: int = Query(60, ge=1, le=24 * 60)) -> Response:
    if series not in SERIES_MAP:
        raise HTTPException(status_code=404, detail="series not allowed")

    if minutes <= 10:
        minutes = 5
    elif minutes <= 90:
        minutes = 60
    else:
        minutes = 240

    cache_key = f"{series}:{minutes}"
    now = time.time()
    with chart_cache_lock:
        cached = chart_cache.get(cache_key)
        if cached and (now - cached[0]) < CHART_CACHE_TTL_SECS:
            return Response(content=cached[1], media_type="image/png", headers={"Cache-Control": "no-store"})

    points = query_series(series, minutes)
    payload = render_sparkline_png(series, minutes, points)

    with chart_cache_lock:
        chart_cache[cache_key] = (now, payload)
        if len(chart_cache) > MAX_CACHE_KEYS:
            oldest = sorted(chart_cache.items(), key=lambda kv: kv[1][0])[: max(1, len(chart_cache) - MAX_CACHE_KEYS)]
            for k, _ in oldest:
                chart_cache.pop(k, None)

    return Response(content=payload, media_type="image/png", headers={"Cache-Control": "no-store"})


HTML_PAGE = """
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>HERMES Dashboard</title>
  <style>
    body { font-family: sans-serif; margin: 16px; background: #0b0f14; color: #e8eef5; }
    h1 { margin: 0 0 4px 0; }
    .row { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 12px; }
    .card { background: #151c24; border: 1px solid #26313d; border-radius: 10px; padding: 10px 12px; }

    .tables-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(320px, 1fr));
      gap: 12px;
      align-items: start;
      grid-auto-rows: min-content;
    }

    @media (max-width: 1100px) {
      .tables-grid { grid-template-columns: repeat(2, minmax(280px, 1fr)); }
    }

    @media (max-width: 700px) {
      .tables-grid { grid-template-columns: 1fr; }
    }
    .status { min-width: 240px; }
    .tile { width: 110px; text-align: center; }
    .ok { background: #173a1f; border-color: #2f7d40; }
    .stale { background: #3e3317; border-color: #92762f; }
    .dead, .unknown { background: #3d1a1a; border-color: #8c2f2f; }
    table { width: auto; border-collapse: collapse; margin-top: 6px; font-size: 11px; }
    th, td { border-bottom: 1px solid #2a3440; padding: 4px 6px; text-align: left; }
    th { color: #9fb3c8; }
    .table-wrap { overflow-x: auto; border-radius: 8px; }
    .table-wrap table { min-width: 820px; table-layout: fixed; }
    th, td { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

    .col-id { width: 72px; }
    .col-ts_utc, .col-ts_local { width: 170px; }
    .col-source { width: 90px; }
    .col-seq { width: 80px; }
    .col-tick_ms { width: 110px; }
    .col-uptime_s { width: 110px; }
    .col-boot { width: 70px; }
    .col-reset_reason { width: 140px; }
    .table-card { margin-bottom: 0; min-width: 320px; }
    button { background: #1f5f99; color: white; border: 0; border-radius: 8px; padding: 8px 12px; cursor: pointer; }
    pre { white-space: pre-wrap; font-size: 12px; background: #111820; border: 1px solid #273342; border-radius: 8px; padding: 10px; }
    .muted { color: #8ea1b3; }
    .small { font-size: 12px; }
    .ts-main { display: block; }
    .ts-sub { display: block; font-size: 11px; color: #8ea1b3; }
    td.changed { background: #213447; transition: background-color 0.5s ease; }
    .trend-card { min-width: 220px; flex: 1 1 260px; }
    .trend-value { font-size: 18px; font-weight: 700; margin: 4px 0 6px 0; }
    .trend-img { width: 100%; border-radius: 8px; border: 1px solid #26313d; display: block; }
    .trend-top { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
    .trend-badges { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }
    .badge { font-size: 11px; color: #9fb3c8; border: 1px solid #26313d; padding: 2px 6px; border-radius: 999px; background: #111820; }
    .seg { display: inline-flex; border: 1px solid #26313d; border-radius: 999px; overflow: hidden; }
    .seg button { background: #111820; color: #9fb3c8; border: 0; padding: 6px 10px; cursor: pointer; }
    .seg button.active { background: #1f5f99; color: #fff; }
    .hidden { display: none !important; }
    .banner { margin: 10px 0 12px 0; padding: 8px 12px; border-radius: 8px; border: 1px solid #8c2f2f; background: #3d1a1a; color: #ffd9d9; font-weight: 600; }
    .stale-card { border-color: #92762f !important; background: #3e3317 !important; }
    .dead-card { border-color: #8c2f2f !important; background: #3d1a1a !important; }
    .trend-img.stale { opacity: 0.38; filter: grayscale(45%); }
    .events-card { width: 100%; }
    .events-table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 12px; }
    .events-table th, .events-table td { border-bottom: 1px solid #2a3440; padding: 6px 8px; text-align: left; }
    .sev-info { color: #9fb3c8; }
    .sev-warn { color: #ffd27f; }
    .sev-crit { color: #ff8a8a; }
    .event-summary { margin-top: 6px; font-size: 12px; color: #9fb3c8; }
    .chip-wrap { display: inline-flex; gap: 4px; flex-wrap: wrap; }
    .chip { font-size: 10px; border-radius: 999px; padding: 1px 6px; border: 1px solid #26313d; background: #111820; color: #9fb3c8; }
    .chip-ack { border-color: #2f7d40; color: #9ed8aa; }
    .chip-snooze { border-color: #92762f; color: #ffd27f; }
    .chip-note { border-color: #4a5f80; color: #b9cbe3; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .event-actions { display: inline-flex; gap: 6px; }
    .event-actions button { padding: 4px 8px; font-size: 11px; }
  </style>
</head>
<body>
  <h1>HERMES Dashboard</h1>
  <div id="readyBanner" class="banner hidden">NOT READY</div>
  <div id="lastUpdated" class="muted small">Last updated: never</div>
  <div id="dbg" class="muted small" style="margin-top:6px;">
    dbg: <span id="dbg-js">booting</span> |
    origin: <span id="dbg-origin">?</span> |
    last fetch: <span id="dbg-fetch">none</span>
  </div>
  <div class=\"row\">
    <div class=\"card status\"><b>Daemon</b><div id=\"daemon\">loading...</div></div>
    <div class=\"card status\"><b>Port</b><div id=\"port\">-</div></div>
    <div class=\"card status\"><b>Lines In</b><div id=\"lines\">-</div></div>
    <div class=\"card status\"><b>Last Error</b><div id=\"error\">-</div></div>
    <div class=\"card\"><button onclick=\"downloadDiag()\">Download diagnostics</button></div>
  </div>

  <div class=\"row\" id=\"freshness\"></div>

  <div class=\"row\">
    <div class=\"card\">
      <b>Trend window</b>
      <div class=\"muted small\">Affects sparklines and badges</div>
      <div style=\"margin-top:8px\" class=\"seg\">
        <button id=\"win-5\" onclick=\"setTrendMinutes(5)\">5m</button>
        <button id=\"win-60\" onclick=\"setTrendMinutes(60)\">60m</button>
        <button id=\"win-240\" onclick=\"setTrendMinutes(240)\">4h</button>
      </div>
    </div>
  </div>

  <div class=\"row\" id=\"trends\"></div>

  <div class=\"row\">
    <div class=\"card events-card\">
      <div style=\"display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap;\">
        <div>
          <b>Events</b>
          <span class=\"muted small\">(last 50)</span>
          <div id="events-last" class="event-summary">Last event: loading...</div>
        </div>
        <div style=\"display:flex; align-items:center; gap:8px;\">
          <span class=\"muted small\">Severity</span>
          <select id=\"events-severity\" style=\"padding:5px 8px;\">
            <option value=\"\">all</option>
            <option value=\"info\">info</option>
            <option value=\"warn\">warn</option>
            <option value=\"crit\">crit</option>
          </select>
          <span class=\"muted small\">Kind</span>
          <select id=\"events-kind\" style=\"padding:5px 8px;\">
            <option value=\"\">all</option>
            <option value=\"stale_detected\">stale_detected</option>
            <option value=\"stale_recovered\">stale_recovered</option>
            <option value=\"reboot_detected\">reboot_detected</option>
            <option value=\"dashboard_restart\">dashboard_restart</option>
            <option value="wifi_drop">wifi_drop</option>
            <option value="wifi_recovered">wifi_recovered</option>
            <option value="air_spike">air_spike</option>
          </select>
          <button onclick=\"evtAckVisible()\">Ack all visible</button>
          <button onclick=\"evtSnoozeVisible(1800)\">Snooze visible 30m</button>
          <button onclick=\"evtClearKindSnoozes()\">Clear kind snoozes</button>
        </div>
      </div>
      <div id="events-sticky" class="banner hidden">ALERT</div>
      <table class=\"events-table\">
        <thead>
          <tr>
            <th>When</th>
            <th>Severity</th>
            <th>Kind</th>
            <th>Source</th>
            <th>Message</th>
            <th>State</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id=\"events-body\">
          <tr><td colspan=\"7\" class=\"muted\">loading...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <div id=\"tables\" class=\"tables-grid\"></div>

  <h3>Raw health</h3>
  <pre id=\"rawHealth\">loading...</pre>

<script src="/app.js"></script>
</body>
</html>
"""


JS_BUNDLE = r"""
const tables = ['hb','env','air','light','mic_noise','esp_net'];
const tableLabels = {
  hb: 'Heartbeat',
  env: 'Environment',
  air: 'Air Quality',
  light: 'Light',
  mic_noise: 'Microphone',
  esp_net: 'Wi-Fi',
};
const displayFresh = ['HB','ENV','AIR','LIGHT','MIC','ESP,NET'];
const trendSeries = [
  { key: 'air_eco2', title: 'ECO2', unit: 'ppm', decimals: 0, table: 'air' },
  { key: 'env_temp', title: 'Temp', unit: '°C', decimals: 1, table: 'env' },
  { key: 'env_hum', title: 'Humidity', unit: '%', decimals: 1, table: 'env' },
  { key: 'esp_rssi', title: 'RSSI', unit: 'dBm', decimals: 0, table: 'esp_net' },
];
const tableState = {};
const tableRowLimit = {};
const eventsState = {
  limit: 50,
  severity: '',
  kind: '',
  rows: [],
  sticky: {
    active: false,
    eventId: 0,
    kind: '',
    severity: '',
    message: '',
    ts: '',
    snoozeUntil: '',
    clearedThroughId: 0,
  },
};
let latestReady = null;
let statusController = null;
let tableController = null;
let trendController = null;
let readyController = null;
let eventsController = null;
let lastUpdatedMs = 0;
let trendMinutes = 60;

function formatAgeShort(seconds) {
  const s = Number(seconds);
  if (!Number.isFinite(s) || s < 0) return 'n/a';
  if (s < 60) return Math.floor(s) + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm';
  return Math.floor(s / 3600) + 'h';
}

function tableIsStale(tableName) {
  if (!latestReady || !latestReady.table_age_seconds) return false;
  const age = Number(latestReady.table_age_seconds[tableName]);
  const threshold = Number(latestReady.stale_threshold_seconds || 0);
  if (!Number.isFinite(age) || age < 0) return true;
  return age > threshold;
}

function firstReadyFailure() {
  if (!latestReady || !Array.isArray(latestReady.failures) || !latestReady.failures.length) {
    return 'unknown';
  }
  return String(latestReady.failures[0]);
}

function updateReadyBanner() {
  const banner = document.getElementById('readyBanner');
  if (!banner) return;
  const ready = !!(latestReady && latestReady.ready);
  if (ready) {
    banner.classList.add('hidden');
    return;
  }
  banner.textContent = 'NOT READY: ' + firstReadyFailure();
  banner.classList.remove('hidden');
}

function dbgSet(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = String(text);
}

dbgSet('dbg-js', 'alive');
dbgSet('dbg-origin', window.location.origin);
window.addEventListener('error', (e) => {
  dbgSet('dbg-fetch', 'JS error: ' + (e.message || 'unknown'));
});
window.addEventListener('unhandledrejection', (e) => {
  dbgSet('dbg-fetch', 'Promise reject: ' + (e.reason ? (e.reason.message || e.reason) : 'unknown'));
});

function setTrendMinutes(m) {
  trendMinutes = m;
  document.getElementById('win-5').classList.toggle('active', m === 5);
  document.getElementById('win-60').classList.toggle('active', m === 60);
  document.getElementById('win-240').classList.toggle('active', m === 240);
  pollTrends();
}

function renderBadges(seriesKey, stats, decimals, unit) {
  const el = document.getElementById('trend-badges-' + seriesKey);
  if (!el) return;
  const trend = trendSeries.find((item) => item.key === seriesKey);
  const staleByReady = trend ? tableIsStale(trend.table) : false;
  const stale = staleByReady || !stats;
  if (stale) {
    const age = trend && latestReady && latestReady.table_age_seconds
      ? latestReady.table_age_seconds[trend.table]
      : -1;
    el.innerHTML = '<span class="badge">STALE ' + formatAgeShort(age) + '</span>';
    return;
  }
  const last = formatMetric(stats.last, decimals);
  const minv = formatMetric(stats.min, decimals);
  const maxv = formatMetric(stats.max, decimals);
  el.innerHTML =
    '<span class="badge">min ' + minv + unit + '</span>' +
    '<span class="badge">max ' + maxv + unit + '</span>' +
    '<span class="badge">last ' + last + unit + '</span>';
}

function clsFor(s) {
  if (s === 'ok') return 'ok';
  if (s === 'stale') return 'stale';
  if (s === 'dead') return 'dead';
  return 'unknown';
}

function renderFreshness(map) {
  const root = document.getElementById('freshness');
  root.innerHTML = '';
  for (const k of displayFresh) {
    const v = (map && map[k]) ? map[k] : 'unknown';
    const d = document.createElement('div');
    d.className = 'card tile ' + clsFor(v);
    d.innerHTML = '<div><b>' + k + '</b></div><div>' + v + '</div>';
    root.appendChild(d);
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function formatDateTime(ts) {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) {
    return 'invalid';
  }
  const pad = (n) => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
    ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
}

function relativeAge(ts) {
  const t = Date.parse(ts);
  if (Number.isNaN(t)) {
    return 'invalid';
  }
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  return Math.floor(s / 3600) + 'h ago';
}

function renderCellHtml(key, value) {
  if (key === 'ts_utc' || key === 'ts_local') {
    const raw = value || '';
    return '<span class="ts-main">' + escapeHtml(formatDateTime(raw)) + '</span>' +
      '<span class="ts-sub time-rel" data-ts="' + escapeHtml(raw) + '">' + escapeHtml(relativeAge(raw)) + '</span>';
  }
  return escapeHtml(value ?? '');
}

function setLastUpdatedNow() {
  lastUpdatedMs = Date.now();
  refreshLastUpdatedLabel();
}

function refreshLastUpdatedLabel() {
  const el = document.getElementById('lastUpdated');
  if (!lastUpdatedMs) {
    el.textContent = 'Last updated: never';
    return;
  }
  const seconds = Math.max(0, Math.floor((Date.now() - lastUpdatedMs) / 1000));
  el.textContent = 'Last updated: ' + seconds + 's ago';
}

function formatMetric(value, decimals) {
  const n = Number(value);
  if (Number.isNaN(n)) {
    return 'n/a';
  }
  return n.toFixed(decimals);
}

function refreshRelativeTimes() {
  document.querySelectorAll('.time-rel').forEach((el) => {
    const raw = el.getAttribute('data-ts') || '';
    el.textContent = relativeAge(raw);
  });
}

function keysEqual(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

function flashCell(cell) {
  cell.classList.add('changed');
  setTimeout(() => cell.classList.remove('changed'), 500);
}

function stringifyTsvValue(value) {
  if (value === null || value === undefined) {
    return '';
  }
  let text;
  if (typeof value === 'object') {
    try {
      text = JSON.stringify(value);
    } catch (_err) {
      text = String(value);
    }
  } else {
    text = String(value);
  }
  return text.replaceAll('\t', ' ').replaceAll('\n', ' ').replaceAll('\r', ' ');
}

async function copyTableRows(tableName) {
  const state = tableState[tableName];
  if (!state) return;

  const setCopyBtnText = (text, ms) => {
    if (!state.copyBtn) return;
    state.copyBtn.textContent = text;
    setTimeout(() => {
      if (state.copyBtn) {
        state.copyBtn.textContent = 'Copy';
      }
    }, ms);
  };

  const rows = [];
  const displayKeys = Array.isArray(state.displayKeys) ? state.displayKeys : [];
  for (const key of displayKeys) {
    const row = state.rowData.get(key);
    if (row) {
      rows.push(row);
    }
  }

  const keys = (state && state.keys && state.keys.length)
    ? state.keys
    : (rows.length ? Object.keys(rows[0]) : []);
  if (!keys.length) {
    setCopyBtnText('Copy failed', 1500);
    return;
  }

  const lines = [keys.join('\t')];
  for (const row of rows) {
    lines.push(keys.map((key) => stringifyTsvValue(row[key])).join('\t'));
  }
  const tsv = lines.join('\n');

  let copied = false;
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      await navigator.clipboard.writeText(tsv);
      copied = true;
    }
  } catch (_err) {
  }

  if (!copied) {
    const area = document.createElement('textarea');
    area.value = tsv;
    area.setAttribute('readonly', 'readonly');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    area.style.pointerEvents = 'none';
    document.body.appendChild(area);
    area.focus();
    area.select();
    copied = document.execCommand('copy');
    area.remove();
  }

  if (copied) {
    setCopyBtnText('Copied', 1000);
  } else {
    setCopyBtnText('Copy failed', 1500);
  }
}

function buildTableCard(tableName) {
  const card = document.createElement('div');
  card.className = 'card table-card';

  const header = document.createElement('div');
  header.style.display = 'flex';
  header.style.alignItems = 'center';
  header.style.justifyContent = 'space-between';
  header.style.gap = '10px';

  const titleWrap = document.createElement('div');
  const label = (tableLabels[tableName] || tableName);
  const title = document.createElement('b');
  title.textContent = label + ' (last 0)';
  const sub = document.createElement('span');
  sub.className = 'muted small';
  sub.textContent = '(' + tableName + ')';
  const staleBadge = document.createElement('span');
  staleBadge.className = 'badge';
  staleBadge.style.marginLeft = '8px';
  staleBadge.textContent = 'OK n/a';
  titleWrap.appendChild(title);
  titleWrap.appendChild(staleBadge);
  titleWrap.appendChild(document.createTextNode(' '));
  titleWrap.appendChild(sub);

  const actions = document.createElement('div');
  actions.style.display = 'flex';
  actions.style.alignItems = 'center';
  actions.style.gap = '8px';

  const rowsLabel = document.createElement('span');
  rowsLabel.className = 'muted small';
  rowsLabel.textContent = 'Rows';

  const rowsSelect = document.createElement('select');
  rowsSelect.style.padding = '5px 8px';
  [20, 50, 200].forEach((n) => {
    const opt = document.createElement('option');
    opt.value = String(n);
    opt.textContent = String(n);
    rowsSelect.appendChild(opt);
  });
  rowsSelect.value = String(tableRowLimit[tableName] || 20);

  const copyBtn = document.createElement('button');
  copyBtn.textContent = 'Copy';
  copyBtn.style.padding = '6px 10px';
  copyBtn.onclick = () => copyTableRows(tableName);

  const exportBtn = document.createElement('button');
  exportBtn.textContent = 'Export JSON';
  exportBtn.style.padding = '6px 10px';
  exportBtn.onclick = () => exportTableJson(tableName);

  const freezeBadge = document.createElement('span');
  freezeBadge.className = 'badge hidden';
  freezeBadge.textContent = 'FROZEN';

  const freezeWrap = document.createElement('label');
  freezeWrap.className = 'muted small';
  freezeWrap.style.display = 'inline-flex';
  freezeWrap.style.alignItems = 'center';
  freezeWrap.style.gap = '5px';
  const freezeToggle = document.createElement('input');
  freezeToggle.type = 'checkbox';
  freezeToggle.onchange = () => {
    const state = tableState[tableName];
    state.frozen = !!freezeToggle.checked;
    freezeBadge.classList.toggle('hidden', !state.frozen);
  };
  freezeWrap.appendChild(freezeToggle);
  freezeWrap.appendChild(document.createTextNode('Freeze'));

  const searchInput = document.createElement('input');
  searchInput.type = 'text';
  searchInput.placeholder = 'Search';
  searchInput.style.padding = '5px 8px';
  searchInput.style.minWidth = '120px';
  searchInput.oninput = () => {
    const state = tableState[tableName];
    state.searchTerm = searchInput.value.trim().toLowerCase();
    renderTableRows(tableName, state.allRows || [], true);
  };

  rowsSelect.onchange = () => {
    const parsed = Number.parseInt(rowsSelect.value, 10);
    tableRowLimit[tableName] = [20, 50, 200].includes(parsed) ? parsed : 20;
    refreshOneTable(tableName);
  };

  header.appendChild(titleWrap);
  actions.appendChild(rowsLabel);
  actions.appendChild(rowsSelect);
  actions.appendChild(searchInput);
  actions.appendChild(freezeWrap);
  actions.appendChild(freezeBadge);
  actions.appendChild(copyBtn);
  actions.appendChild(exportBtn);
  header.appendChild(actions);
  card.appendChild(header);

  const noRows = document.createElement('div');
  noRows.textContent = 'no rows';
  noRows.className = 'muted small';
  card.appendChild(noRows);

  const table = document.createElement('table');
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  thead.appendChild(headRow);
  const tbody = document.createElement('tbody');
  table.appendChild(thead);
  table.appendChild(tbody);
  table.style.display = 'none';
  const wrap = document.createElement('div');
  wrap.className = 'table-wrap';
  wrap.appendChild(table);
  card.appendChild(wrap);

  return {
    card,
    title,
    staleBadge,
    freezeBadge,
    searchInput,
    freezeToggle,
    noRows,
    table,
    headRow,
    tbody,
    copyBtn,
    exportBtn,
    keys: [],
    rowEls: new Map(),
    rowData: new Map(),
    allRows: [],
    displayKeys: [],
    maxId: null,
    frozen: false,
    searchTerm: '',
  };
}

function updateTableStaleBadge(tableName) {
  const state = tableState[tableName];
  if (!state) return;
  if (!latestReady || !latestReady.table_age_seconds) {
    state.staleBadge.textContent = 'OK n/a';
    state.card.classList.remove('stale-card');
    return;
  }
  const age = Number(latestReady.table_age_seconds[tableName]);
  const stale = tableIsStale(tableName);
  state.staleBadge.textContent = (stale ? 'STALE ' : 'OK ') + formatAgeShort(age);
  state.card.classList.toggle('stale-card', stale);
}

function rowMatchesSearch(row, searchTerm) {
  if (!searchTerm) return true;
  for (const value of Object.values(row || {})) {
    if (String(value ?? '').toLowerCase().includes(searchTerm)) {
      return true;
    }
  }
  return false;
}

function initTables() {
  const root = document.getElementById('tables');
  for (const t of tables) {
    tableRowLimit[t] = 20;
    const state = buildTableCard(t);
    tableState[t] = state;
    root.appendChild(state.card);
  }
}

function initTrends() {
  const root = document.getElementById('trends');
  if (!root) return;
  root.innerHTML = '';
  for (const trend of trendSeries) {
    const card = document.createElement('div');
    card.className = 'card trend-card';
    card.innerHTML =
      '<div class="trend-top">' +
        '<div><b>' + trend.title + '</b></div>' +
        '<div id="trend-badges-' + trend.key + '" class="trend-badges"></div>' +
      '</div>' +
      '<div id="trend-value-' + trend.key + '" class="trend-value">n/a</div>' +
      '<img id="trend-img-' + trend.key + '" class="trend-img" alt="' + trend.title + ' trend" src="/chart/' + trend.key + '.png?minutes=60" />';
    root.appendChild(card);
  }
}

function initEvents() {
  const severityEl = document.getElementById('events-severity');
  const kindEl = document.getElementById('events-kind');
  if (severityEl) {
    severityEl.onchange = () => {
      eventsState.severity = severityEl.value || '';
      pollEvents();
    };
  }
  if (kindEl) {
    kindEl.onchange = () => {
      eventsState.kind = kindEl.value || '';
      pollEvents();
    };
  }
}

function isRecoveredKind(kind) {
  const value = String(kind || '').toLowerCase();
  return value.endsWith('_recovered');
}

function isAlertEvent(event) {
  if (!event) return false;
  const kind = String(event.kind || '').toLowerCase();
  const sev = String(event.severity || '').toLowerCase();
  if (isRecoveredKind(kind)) return false;
  if (!(sev === 'warn' || sev === 'crit')) return false;
  const state = event.state || {};
  if (state.acked) return false;
  if (state.snoozed) return false;
  return true;
}

function hasRecoveryAfter(event, rows) {
  const id = Number(event && event.id ? event.id : 0);
  if (!id) return false;
  return (rows || []).some((row) => {
    const rowId = Number(row && row.id ? row.id : 0);
    return rowId > id && isRecoveredKind(row.kind);
  });
}

function formatStateChips(event) {
  const state = event && event.state ? event.state : {};
  const chips = [];
  if (state.acked) {
    chips.push('<span class="chip chip-ack">Acked</span>');
  }
  if (state.snoozed) {
    chips.push('<span class="chip chip-snooze">Snoozed</span>');
  }
  if (state.note) {
    chips.push('<span class="chip chip-note" title="' + escapeHtml(state.note) + '">Note: ' + escapeHtml(state.note) + '</span>');
  }
  if (!chips.length) {
    return '<span class="muted">-</span>';
  }
  return '<span class="chip-wrap">' + chips.join('') + '</span>';
}

async function postJson(url, payload) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
  if (!resp.ok) {
    const txt = await resp.text();
    throw new Error('HTTP ' + resp.status + ' ' + url + ' :: ' + txt.slice(0, 200));
  }
  return await resp.json();
}

async function ackEvent(event) {
  const payload = event && event.id ? { id: event.id } : { dedupe_key: event.dedupe_key };
  await postJson('/api/events/ack', payload);
  await pollEvents();
}

async function snoozeEvent(event, seconds) {
  const payload = event && event.dedupe_key
    ? { dedupe_key: event.dedupe_key, seconds }
    : { id: event.id, seconds };
  await postJson('/api/events/snooze', payload);
  await pollEvents();
}

async function noteEvent(event) {
  const current = event && event.state && event.state.note ? String(event.state.note) : '';
  const text = window.prompt('Note for event #' + String(event.id || '?'), current);
  if (text === null) return;
  await postJson('/api/events/note', { id: event.id, note: String(text).trim() });
  await pollEvents();
}

function visibleEventRows() {
  return Array.isArray(eventsState.rows) ? eventsState.rows : [];
}

function uniqueNums(values) {
  return [...new Set((values || []).map((v) => Number(v)).filter((v) => Number.isFinite(v) && v > 0))];
}

function uniqueStrings(values) {
  return [...new Set((values || []).map((v) => String(v || '').trim()).filter((v) => v.length > 0))];
}

async function ackVisibleEvents() {
  const rows = visibleEventRows();
  const ids = uniqueNums(rows.map((row) => row.id));
  const dedupeKeys = uniqueStrings(rows.map((row) => row.dedupe_key));
  if (!ids.length && !dedupeKeys.length) return;
  await postJson('/api/events/ack_bulk', { ids, dedupe_keys: dedupeKeys });
  await pollEvents();
}

async function snoozeVisibleEvents(seconds) {
  const rows = visibleEventRows();
  const ids = uniqueNums(rows.map((row) => row.id));
  const dedupeKeys = uniqueStrings(rows.map((row) => row.dedupe_key));
  if (!ids.length && !dedupeKeys.length) return;
  await postJson('/api/events/snooze_bulk', { ids, dedupe_keys: dedupeKeys, seconds: Number(seconds || 1800) });
  await pollEvents();
}

async function clearKindSnoozes() {
  const kind = String(eventsState.kind || '').trim();
  if (!kind) {
    alert('Pick a Kind filter first.');
    return;
  }
  await postJson('/api/events/clear_snooze_kind', { kind });
  await pollEvents();
}

function updateLastEventSummary(rows) {
  const el = document.getElementById('events-last');
  if (!el) return;
  if (!rows || !rows.length) {
    el.textContent = 'Last event: none';
    return;
  }
  const latest = rows[0];
  const whenText = latest.ts_local || latest.ts_utc || '';
  el.textContent = 'Last event: ' + String(latest.kind || 'unknown') + ' · ' + relativeAge(whenText);
}

function updateStickyEventBanner() {
  const banner = document.getElementById('events-sticky');
  if (!banner) return;
  const sticky = eventsState.sticky;
  if (!sticky.active) {
    banner.classList.add('hidden');
    return;
  }
  const sev = String(sticky.severity || '').toLowerCase();
  const idPart = sticky.eventId ? ('id=' + String(sticky.eventId)) : 'id=?';
  const agePart = sticky.ts ? relativeAge(sticky.ts) : 'unknown age';
  const snoozePart = sticky.snoozeUntil
    ? ('snoozed until ' + formatDateTime(sticky.snoozeUntil))
    : 'not snoozed';
  banner.textContent = 'Sticky: ' + sticky.kind + ' ' + sev + ', ' + idPart + ', ' + agePart + ', ' + snoozePart + ' — ' + sticky.message;
  banner.classList.remove('hidden');
}

function reconcileStickyEvent(rows) {
  const sticky = eventsState.sticky;
  const actionable = (rows || []).find((row) => isAlertEvent(row) && !hasRecoveryAfter(row, rows || []));
  if (!actionable) {
    sticky.active = false;
    sticky.eventId = 0;
    sticky.kind = '';
    sticky.severity = '';
    sticky.message = '';
    sticky.ts = '';
    sticky.snoozeUntil = '';
    sticky.clearedThroughId = 0;
    updateStickyEventBanner();
    return;
  }
  sticky.active = true;
  sticky.eventId = Number(actionable.id || 0);
  sticky.kind = String(actionable.kind || 'alert');
  sticky.severity = String(actionable.severity || 'warn');
  sticky.message = String(actionable.message || '').slice(0, 180);
  sticky.ts = String(actionable.ts_local || actionable.ts_utc || '');
  sticky.snoozeUntil = String((actionable.state && actionable.state.snooze_until_utc) || '');
  updateStickyEventBanner();
}

function renderEvents(rows) {
  const body = document.getElementById('events-body');
  if (!body) return;
  body.innerHTML = '';
  if (!rows || !rows.length) {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td colspan="7" class="muted">no events</td>';
    body.appendChild(tr);
    updateLastEventSummary([]);
    reconcileStickyEvent([]);
    return;
  }
  for (const event of rows) {
    const tr = document.createElement('tr');
    const whenText = event.ts_local || event.ts_utc || '';
    const sev = String(event.severity || 'info').toLowerCase();
    const sevClass = sev === 'crit' ? 'sev-crit' : (sev === 'warn' ? 'sev-warn' : 'sev-info');
    const eventId = Number(event.id || 0);
    const actionAck = 'evtAck(' + eventId + ')';
    const actionSnooze = 'evtSnooze(' + eventId + ',1800)';
    const actionNote = 'evtNote(' + eventId + ')';
    tr.innerHTML =
      '<td>' + escapeHtml(formatDateTime(whenText)) + '</td>' +
      '<td class="' + sevClass + '">' + escapeHtml(sev) + '</td>' +
      '<td>' + escapeHtml(event.kind || '') + '</td>' +
      '<td>' + escapeHtml(event.source || '') + '</td>' +
      '<td>' + escapeHtml(event.message || '') + '</td>' +
      '<td>' + formatStateChips(event) + '</td>' +
      '<td><span class="event-actions">' +
      '<button onclick="' + actionAck + '">Ack</button>' +
      '<button onclick="' + actionSnooze + '">Snooze 30m</button>' +
      '<button onclick="' + actionNote + '">Add note</button>' +
      '</span></td>';
    body.appendChild(tr);
  }
  updateLastEventSummary(rows || []);
  reconcileStickyEvent(rows || []);
}

window.evtAck = async function evtAck(eventId) {
  const rows = Array.isArray(eventsState.rows) ? eventsState.rows : [];
  const event = rows.find((row) => Number(row.id || 0) === Number(eventId));
  if (!event) return;
  try {
    await ackEvent(event);
  } catch (err) {
    console.error(err);
    alert('Ack failed: ' + (err && err.message ? err.message : err));
  }
};

window.evtSnooze = async function evtSnooze(eventId, seconds) {
  const rows = Array.isArray(eventsState.rows) ? eventsState.rows : [];
  const event = rows.find((row) => Number(row.id || 0) === Number(eventId));
  if (!event) return;
  try {
    await snoozeEvent(event, Number(seconds || 1800));
  } catch (err) {
    console.error(err);
    alert('Snooze failed: ' + (err && err.message ? err.message : err));
  }
};

window.evtNote = async function evtNote(eventId) {
  const rows = Array.isArray(eventsState.rows) ? eventsState.rows : [];
  const event = rows.find((row) => Number(row.id || 0) === Number(eventId));
  if (!event) return;
  try {
    await noteEvent(event);
  } catch (err) {
    console.error(err);
    alert('Note failed: ' + (err && err.message ? err.message : err));
  }
};

window.evtAckVisible = async function evtAckVisible() {
  try {
    await ackVisibleEvents();
  } catch (err) {
    console.error(err);
    alert('Ack visible failed: ' + (err && err.message ? err.message : err));
  }
};

window.evtSnoozeVisible = async function evtSnoozeVisible(seconds) {
  try {
    await snoozeVisibleEvents(Number(seconds || 1800));
  } catch (err) {
    console.error(err);
    alert('Snooze visible failed: ' + (err && err.message ? err.message : err));
  }
};

window.evtClearKindSnoozes = async function evtClearKindSnoozes() {
  try {
    await clearKindSnoozes();
  } catch (err) {
    console.error(err);
    alert('Clear kind snoozes failed: ' + (err && err.message ? err.message : err));
  }
};

function updateTableHead(state, keys) {
  if (keysEqual(state.keys, keys)) {
    return;
  }
  state.keys = [...keys];
  state.headRow.innerHTML = '';
  for (const k of keys) {
    const th = document.createElement('th');
    th.className = 'col-' + k;
    th.textContent = k;
    state.headRow.appendChild(th);
  }
}

function ensureRowCell(tr, key) {
  if (!tr._cells) {
    tr._cells = {};
  }
  if (!tr._cells[key]) {
    const td = document.createElement('td');
    td.className = 'col-' + key;
    tr._cells[key] = td;
    tr.appendChild(td);
  }
  return tr._cells[key];
}

function updateRow(tr, oldRow, newRow, keys) {
  for (const k of keys) {
    const td = ensureRowCell(tr, k);
    const oldValue = oldRow ? (oldRow[k] ?? '') : undefined;
    const newValue = newRow[k] ?? '';
    const changed = oldRow && String(oldValue) !== String(newValue);
    td.innerHTML = renderCellHtml(k, newValue);
    if (changed) {
      flashCell(td);
    }
  }
}

function renderTableRows(tableName, rows, force = false) {
  const state = tableState[tableName];
  const label = (tableLabels[tableName] || tableName);
  state.allRows = Array.isArray(rows) ? [...rows] : [];
  const filteredRows = (state.allRows || []).filter((row) => rowMatchesSearch(row, state.searchTerm));
  rows = filteredRows;
  state.title.textContent = label + ' (last ' + rows.length + ')';
  if (!rows.length) {
    state.maxId = null;
    state.displayKeys = [];
    state.rowEls.clear();
    state.rowData.clear();
    state.tbody.innerHTML = '';
    state.table.style.display = 'none';
    state.noRows.style.display = 'block';
    return;
  }

  const newestId = rows[0].id ?? null;
  if (!force && state.maxId !== null && newestId === state.maxId) {
    return;
  }

  const keys = Object.keys(rows[0]);
  updateTableHead(state, keys);
  state.table.style.display = 'table';
  state.noRows.style.display = 'none';

  const seen = new Set();
  const displayKeys = [];
  for (const row of rows) {
    const id = row.id;
    displayKeys.push(id);
    seen.add(id);
    let tr = state.rowEls.get(id);
    if (!tr) {
      tr = document.createElement('tr');
      tr.dataset.id = String(id);
      state.rowEls.set(id, tr);
    }
    const oldRow = state.rowData.get(id);
    updateRow(tr, oldRow, row, keys);
    state.rowData.set(id, { ...row });
    state.tbody.appendChild(tr);
  }

  for (const [id, tr] of state.rowEls.entries()) {
    if (seen.has(id)) {
      continue;
    }
    tr.remove();
    state.rowEls.delete(id);
    state.rowData.delete(id);
  }

  state.displayKeys = displayKeys;
  state.maxId = newestId;
}

function applyTableRows(tableName, rows, force = false) {
  renderTableRows(tableName, rows, force);
  updateTableStaleBadge(tableName);
}

function fetchOneTable(tableName, controller = null) {
  const limit = tableRowLimit[tableName] || 20;
  return fetchJson('/api/latest/' + tableName + '?limit=' + limit, controller || new AbortController());
}

async function refreshOneTable(tableName) {
  if (document.visibilityState === 'hidden') {
    return;
  }
  if (tableState[tableName] && tableState[tableName].frozen) {
    return;
  }
  try {
    const data = await fetchOneTable(tableName);
    applyTableRows(tableName, data.rows || [], true);
    refreshRelativeTimes();
    setLastUpdatedNow();
  } catch (err) {
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      console.error(err);
    }
  }
}

async function fetchJson(url, controller) {
  try {
    const resp = await fetch(url, { signal: controller.signal, cache: 'no-store' });
    if (!resp.ok) {
      const msg = 'HTTP ' + resp.status + ' ' + url;
      dbgSet('dbg-fetch', msg);
      throw new Error(msg);
    }
    dbgSet('dbg-fetch', 'OK ' + url);
    return await resp.json();
  } catch (err) {
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      dbgSet('dbg-fetch', 'ERR ' + url + ' :: ' + (err && err.message ? err.message : err));
    }
    throw err;
  }
}

async function pollStatus() {
  if (document.visibilityState === 'hidden') {
    return;
  }
  if (statusController) {
    statusController.abort();
  }
  const controller = new AbortController();
  statusController = controller;
  try {
    const [status, health] = await Promise.all([
      fetchJson('/api/status', controller),
      fetchJson('/api/health', controller),
    ]);

    document.getElementById('daemon').innerText = status.daemon_running ? 'running' : 'down';
    document.getElementById('port').innerText = status.port || 'unknown';
    document.getElementById('lines').innerText = status.lines_in || 'unknown';
    document.getElementById('error').innerText = status.last_error || 'unknown';

    renderFreshness(health.freshness || {});
    document.getElementById('rawHealth').innerText = health.raw || '';
    setLastUpdatedNow();
  } catch (err) {
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      console.error(err);
    }
  } finally {
    if (statusController === controller) {
      statusController = null;
    }
  }
}

async function pollTables() {
  if (document.visibilityState === 'hidden') {
    return;
  }
  if (tableController) {
    tableController.abort();
  }
  const controller = new AbortController();
  tableController = controller;
  try {
    const activeTables = tables.filter((t) => !(tableState[t] && tableState[t].frozen));
    if (!activeTables.length) {
      return;
    }
    const results = await Promise.all(
      activeTables.map((t) => fetchOneTable(t, controller).then((data) => [t, data]))
    );

    for (const [tableName, data] of results) {
      applyTableRows(tableName, data.rows || []);
    }
    refreshRelativeTimes();
    setLastUpdatedNow();
  } catch (err) {
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      console.error(err);
    }
  } finally {
    if (tableController === controller) {
      tableController = null;
    }
  }
}

async function pollTrends() {
  if (document.visibilityState === 'hidden') {
    return;
  }
  if (trendController) {
    trendController.abort();
  }
  const controller = new AbortController();
  trendController = controller;
  try {
    const results = await Promise.all(
      trendSeries.map((trend) => fetchJson('/api/ts/' + trend.key + '?minutes=' + trendMinutes, controller).then((data) => [trend, data]))
    );

    const cacheBust = Date.now();
    for (const [trend, data] of results) {
      const points = data.points || [];
      const latest = points.length ? points[points.length - 1].v : null;
      const valueEl = document.getElementById('trend-value-' + trend.key);
      const stale = tableIsStale(trend.table) || !points.length || !data.stats;
      valueEl.textContent = latest === null ? 'n/a' : (formatMetric(latest, trend.decimals) + ' ' + trend.unit);
      renderBadges(trend.key, data.stats || null, trend.decimals, ' ' + trend.unit);

      const imgEl = document.getElementById('trend-img-' + trend.key);
      const card = imgEl ? imgEl.closest('.trend-card') : null;
      if (imgEl) {
        imgEl.classList.toggle('stale', stale);
      }
      if (card) {
        card.classList.toggle('stale-card', stale);
      }
      imgEl.src = '/chart/' + trend.key + '.png?minutes=' + trendMinutes + '&ts=' + cacheBust;
    }
    setLastUpdatedNow();
  } catch (err) {
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      console.error(err);
    }
  } finally {
    if (trendController === controller) {
      trendController = null;
    }
  }
}

async function downloadDiag() {
  const resp = await fetch('/api/diag');
  const txt = await resp.text();
  const blob = new Blob([txt], {type: 'text/plain'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'hermes-doctor.txt';
  a.click();
  URL.revokeObjectURL(a.href);
}

async function exportTableJson(tableName) {
  const state = tableState[tableName];
  if (!state) return;
  const rows = [];
  const displayKeys = Array.isArray(state.displayKeys) ? state.displayKeys : [];
  for (const key of displayKeys) {
    const row = state.rowData.get(key);
    if (row) rows.push(row);
  }
  const blob = new Blob([JSON.stringify(rows, null, 2)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = tableName + '-rows.json';
  a.click();
  URL.revokeObjectURL(a.href);
}

async function pollReady() {
  if (document.visibilityState === 'hidden') {
    return;
  }
  if (readyController) {
    readyController.abort();
  }
  const controller = new AbortController();
  readyController = controller;
  try {
    latestReady = await fetchJson('/api/ready', controller);
    updateReadyBanner();
    for (const tableName of tables) {
      updateTableStaleBadge(tableName);
    }
  } catch (err) {
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      console.error(err);
    }
  } finally {
    if (readyController === controller) {
      readyController = null;
    }
  }
}

async function pollEvents() {
  if (document.visibilityState === 'hidden') {
    return;
  }
  if (eventsController) {
    eventsController.abort();
  }
  const controller = new AbortController();
  eventsController = controller;
  try {
    const params = new URLSearchParams();
    params.set('limit', String(eventsState.limit));
    if (eventsState.severity) params.set('severity', eventsState.severity);
    if (eventsState.kind) params.set('kind', eventsState.kind);
    const data = await fetchJson('/api/events/latest?' + params.toString(), controller);
    eventsState.rows = Array.isArray(data.rows) ? data.rows : [];
    renderEvents(eventsState.rows);
  } catch (err) {
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      console.error(err);
    }
  } finally {
    if (eventsController === controller) {
      eventsController = null;
    }
  }
}

(async () => {
  initEvents();
  initTrends();
  setTrendMinutes(60);
  initTables();
  await pollReady();
  await pollStatus();
  await pollTables();
  await pollEvents();
  setInterval(pollStatus, 1000);
  setInterval(pollReady, 3000);
  setInterval(pollTables, 3000);
  setInterval(pollTrends, 7000);
  setInterval(pollEvents, 4000);
  setInterval(refreshRelativeTimes, 1000);
  setInterval(refreshLastUpdatedLabel, 1000);
})();
"""


@APP.get("/app.js")
def app_js() -> Response:
    return Response(JS_BUNDLE, media_type="application/javascript")


@APP.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(HTML_PAGE)


@APP.get("/healthz")
def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@APP.get("/readyz")
def readyz() -> JSONResponse:
    state = build_ready_state()
    status = 200 if bool(state.get("ready")) else 503
    return JSONResponse(content=state, status_code=status)


@APP.get("/api/ready")
def api_ready() -> JSONResponse:
  state = build_ready_state()
  return JSONResponse(content=state, status_code=200)


@APP.get("/metrics", response_class=PlainTextResponse)
def metrics() -> PlainTextResponse:
    state = build_ready_state()
    table_ages = state.get("table_age_seconds", {})
    with db_locked_count_lock:
        locked_total = int(db_locked_count)
    chart_p95 = get_chart_render_p95_ms()
    watchdog_restarts = get_watchdog_restart_count()

    lines = [
      "# HELP hermes_ready 1 if dashboard readiness checks pass, else 0",
      "# TYPE hermes_ready gauge",
      f"hermes_ready {1 if state.get('ready') else 0}",
      "# HELP hermes_last_ingest_age_seconds Age of newest row per table in seconds; -1 means missing/unknown",
      "# TYPE hermes_last_ingest_age_seconds gauge",
    ]
    for table in TABLES:
      value = float(table_ages.get(table, -1.0))
      lines.append(f'hermes_last_ingest_age_seconds{{table="{table}"}} {value:.3f}')
    lines.extend([
      "# HELP hermes_db_locked_total Count of sqlite locked events observed by dashboard queries",
      "# TYPE hermes_db_locked_total counter",
      f"hermes_db_locked_total {locked_total}",
      "# HELP hermes_chart_render_ms_p95 Rolling p95 chart render latency in milliseconds",
      "# TYPE hermes_chart_render_ms_p95 gauge",
      f"hermes_chart_render_ms_p95 {chart_p95:.3f}",
      "# HELP hermes_watchdog_restart_count Total dashboard restarts triggered by watchdog",
      "# TYPE hermes_watchdog_restart_count counter",
      f"hermes_watchdog_restart_count {watchdog_restarts}",
    ])
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
