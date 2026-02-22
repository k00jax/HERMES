import json
import base64
import re
import sqlite3
import subprocess
import os
import io
import csv
import math
import time
import uuid
import statistics
import datetime
import threading
import importlib.util
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt

APP = FastAPI(title="HERMES Dashboard", version="0.1.0")
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
APP.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

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
REPORTS_DIR = Path(os.environ.get("HERMES_REPORTS_DIR", "/home/odroid/hermes-data/reports"))
DB_TIMEOUT_SECS = float(os.environ.get("HERMES_DB_TIMEOUT_SECS", "2.0"))
MAX_CACHE_KEYS = int(os.environ.get("HERMES_CHART_CACHE_KEYS", "64"))
MAX_RANGE_DAYS = 31

TABLES = ("hb", "env", "air", "light", "mic_noise", "esp_net", "radar")
READY_TABLES = ("hb", "env", "air", "light", "mic_noise", "esp_net")
FRESHNESS_KEYS = ("HB", "ENV", "AIR", "LIGHT", "MIC", "ESP,NET", "RADAR")
NAV_LINKS = (
  ("Home", "/"),
  ("History", "/history"),
  ("Events", "/events"),
  ("Analytics", "/analytics"),
  ("Calibration", "/calibration"),
  ("Settings", "/settings"),
)
SETTINGS_DEFAULTS = {
  "field_mode_start": False,
  "units_distance": "cm",
  "chart_slot_a": "air_eco2",
  "chart_slot_b": "env_temp",
  "chart_slot_c": "env_hum",
  "chart_slot_d": "air_tvoc",
  "chime_event_startup": "startup_vault_boot",
  "chime_event_air_spike": "warn_radiation_spike",
  "chime_event_wifi_drop": "warn_low_power",
  "chime_event_reboot_detected": "warn_system_fault",
  "chime_event_presence_change": "none",
  "radar_self_suppress_enabled": False,
  "radar_self_suppress_near_cm": 80,
  "radar_self_suppress_persist_s": 20,
  "radar_self_suppress_jitter_cm": 15,
  "radar_presence_mode": "raw",
}

VALID_CHIME_KEYS = {
  "none",
  "startup_vault_boot",
  "startup_atomic_sunrise",
  "startup_radiant_bootloader",
  "startup_field_unit_online",
  "warn_radiation_spike",
  "warn_system_fault",
  "warn_low_power",
}

SERIES_MAP = {
  "env_temp": {"table": "env", "column": "temp_c", "label": "Temp (C)", "color": "#4fc3f7", "stepped": False},
  "env_hum": {"table": "env", "column": "hum_pct", "label": "Humidity (%)", "color": "#81c784", "stepped": False},
  "air_eco2": {"table": "air", "column": "eco2_ppm", "label": "eCO2 (ppm)", "color": "#ffb74d", "stepped": False},
  "air_tvoc": {"table": "air", "column": "tvoc_ppb", "label": "TVOC (ppb)", "color": "#ba68c8", "stepped": False},
  "esp_rssi": {"table": "esp_net", "column": "rssi", "label": "RSSI (dBm)", "color": "#ef5350", "stepped": False},
  "esp_wifist": {"table": "esp_net", "column": "wifist", "label": "WiFi State", "color": "#90a4ae", "stepped": True},
  "radar_target": {"table": "radar", "column": "target", "label": "Presence Target (0-3)", "color": "#66bb6a", "stepped": True},
  "radar_bodies": {"table": "radar", "column": "(CASE WHEN target = 3 THEN 2 WHEN target IN (1, 2) THEN 1 ELSE 0 END)", "label": "Human Presences (bodies)", "color": "#66bb6a", "stepped": True},
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
WIFI_CONNECTED_STATES = {1, 3}
RSSI_NOT_CONNECTED = 999
RSSI_MIN_DBM = -120
RSSI_MAX_DBM = 0

event_post_rate_lock = threading.Lock()
event_post_rate: Dict[str, List[float]] = {}

event_chime_lock = threading.Lock()
event_chime_initialized = False
event_chime_last_seen_id = 0
presence_change_last_chime_ts = 0.0

EVENT_KIND_TO_CHIME_SETTING = {
  "dashboard_restart": "chime_event_startup",
  "air_spike": "chime_event_air_spike",
  "wifi_drop": "chime_event_wifi_drop",
  "reboot_detected": "chime_event_reboot_detected",
}
STARTUP_CHIME_BOOTSTRAP_MAX_AGE_SECS = 180

radar_calibration_lock = threading.Lock()
radar_calibration_sessions: Dict[str, Dict[str, object]] = {}


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
    try:
      row = conn.execute(f"SELECT ts_utc FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError as exc:
      if "no such table" in str(exc).lower():
        ages[table] = -1.0
        continue
      raise
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


def ensure_radar_calibration_table(conn: sqlite3.Connection) -> None:
  conn.execute(
    """
    CREATE TABLE IF NOT EXISTS radar_calibration (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts_utc TEXT NOT NULL,
      ts_local TEXT,
      duration_s INTEGER NOT NULL,
      max_range_cm INTEGER NOT NULL,
      samples INTEGER NOT NULL,
      baseline_detect_cm REAL,
      noise_detect_cm REAL,
      false_presence_count INTEGER NOT NULL,
      notes TEXT
    );
    """
  )
  conn.execute("CREATE INDEX IF NOT EXISTS idx_radar_calibration_ts ON radar_calibration(ts_utc);")


def ensure_settings_table(conn: sqlite3.Connection) -> None:
  conn.execute(
    """
    CREATE TABLE IF NOT EXISTS settings (
      key TEXT PRIMARY KEY,
      value_json TEXT NOT NULL,
      updated_ts_utc TEXT NOT NULL
    );
    """
  )
  conn.execute("CREATE INDEX IF NOT EXISTS idx_settings_updated ON settings(updated_ts_utc);")


def get_settings_payload(conn: sqlite3.Connection) -> Dict[str, object]:
  ensure_settings_table(conn)
  rows = conn.execute("SELECT key, value_json FROM settings").fetchall()
  parsed: Dict[str, object] = {}
  for row in rows:
    key = str(row[0])
    value_json = row[1]
    try:
      parsed[key] = json.loads(value_json)
    except Exception:
      continue

  result = dict(SETTINGS_DEFAULTS)
  for key in SETTINGS_DEFAULTS:
    if key in parsed:
      result[key] = parsed[key]

  result["field_mode_start"] = bool(result.get("field_mode_start"))
  units = str(result.get("units_distance") or "cm").lower()
  result["units_distance"] = "m" if units == "m" else "cm"

  valid_chart = {"air_eco2", "env_temp", "env_hum", "air_tvoc"}
  for slot_key, fallback in (
      ("chart_slot_a", "air_eco2"),
      ("chart_slot_b", "env_temp"),
      ("chart_slot_c", "env_hum"),
      ("chart_slot_d", "air_tvoc"),
  ):
    chosen = str(result.get(slot_key) or fallback)
    result[slot_key] = chosen if chosen in valid_chart else fallback

  for chime_key, fallback in (
      ("chime_event_startup", "startup_vault_boot"),
      ("chime_event_air_spike", "warn_radiation_spike"),
      ("chime_event_wifi_drop", "warn_low_power"),
      ("chime_event_reboot_detected", "warn_system_fault"),
      ("chime_event_presence_change", "none"),
  ):
    chosen = str(result.get(chime_key) or fallback)
    result[chime_key] = chosen if chosen in VALID_CHIME_KEYS else fallback

  result["radar_self_suppress_enabled"] = bool(result.get("radar_self_suppress_enabled"))
  try:
    near_cm = int(result.get("radar_self_suppress_near_cm"))
  except Exception:
    near_cm = 80
  result["radar_self_suppress_near_cm"] = max(20, min(200, near_cm))

  try:
    persist_s = int(result.get("radar_self_suppress_persist_s"))
  except Exception:
    persist_s = 20
  result["radar_self_suppress_persist_s"] = max(5, min(120, persist_s))

  try:
    jitter_cm = int(result.get("radar_self_suppress_jitter_cm"))
  except Exception:
    jitter_cm = 15
  result["radar_self_suppress_jitter_cm"] = max(2, min(80, jitter_cm))

  mode = str(result.get("radar_presence_mode") or "raw").strip().lower()
  result["radar_presence_mode"] = "derived" if mode == "derived" else "raw"
  return result


def save_settings_payload(conn: sqlite3.Connection, updates: Dict[str, object]) -> Dict[str, object]:
  ensure_settings_table(conn)
  current = get_settings_payload(conn)
  merged = dict(current)
  for key, value in (updates or {}).items():
    if key not in SETTINGS_DEFAULTS:
      continue
    merged[key] = value

  if "field_mode_start" in updates:
    merged["field_mode_start"] = bool(updates.get("field_mode_start"))
  if "units_distance" in updates:
    units = str(updates.get("units_distance") or "cm").lower()
    merged["units_distance"] = "m" if units == "m" else "cm"

  valid_chart = {"air_eco2", "env_temp", "env_hum", "air_tvoc"}
  for slot_key, fallback in (
      ("chart_slot_a", "air_eco2"),
      ("chart_slot_b", "env_temp"),
      ("chart_slot_c", "env_hum"),
      ("chart_slot_d", "air_tvoc"),
  ):
    if slot_key in updates:
      chosen = str(updates.get(slot_key) or fallback)
      merged[slot_key] = chosen if chosen in valid_chart else fallback

  for chime_key, fallback in (
      ("chime_event_startup", "startup_vault_boot"),
      ("chime_event_air_spike", "warn_radiation_spike"),
      ("chime_event_wifi_drop", "warn_low_power"),
      ("chime_event_reboot_detected", "warn_system_fault"),
      ("chime_event_presence_change", "none"),
  ):
    if chime_key in updates:
      chosen = str(updates.get(chime_key) or fallback)
      merged[chime_key] = chosen if chosen in VALID_CHIME_KEYS else fallback

  if "radar_self_suppress_enabled" in updates:
    merged["radar_self_suppress_enabled"] = bool(updates.get("radar_self_suppress_enabled"))
  if "radar_self_suppress_near_cm" in updates:
    try:
      near_cm = int(updates.get("radar_self_suppress_near_cm"))
    except Exception:
      near_cm = int(current.get("radar_self_suppress_near_cm") or 80)
    merged["radar_self_suppress_near_cm"] = max(20, min(200, near_cm))
  if "radar_self_suppress_persist_s" in updates:
    try:
      persist_s = int(updates.get("radar_self_suppress_persist_s"))
    except Exception:
      persist_s = int(current.get("radar_self_suppress_persist_s") or 20)
    merged["radar_self_suppress_persist_s"] = max(5, min(120, persist_s))
  if "radar_self_suppress_jitter_cm" in updates:
    try:
      jitter_cm = int(updates.get("radar_self_suppress_jitter_cm"))
    except Exception:
      jitter_cm = int(current.get("radar_self_suppress_jitter_cm") or 15)
    merged["radar_self_suppress_jitter_cm"] = max(2, min(80, jitter_cm))

  if "radar_presence_mode" in updates:
    mode = str(updates.get("radar_presence_mode") or "raw").strip().lower()
    merged["radar_presence_mode"] = "derived" if mode == "derived" else "raw"

  if not bool(merged.get("radar_self_suppress_enabled")):
    merged["radar_presence_mode"] = "raw"
  if str(merged.get("radar_presence_mode") or "raw") == "derived":
    merged["radar_self_suppress_enabled"] = True

  now_ts = now_utc_iso()
  for key in SETTINGS_DEFAULTS:
    conn.execute(
      """
      INSERT INTO settings (key, value_json, updated_ts_utc)
      VALUES (?, ?, ?)
      ON CONFLICT(key) DO UPDATE SET
        value_json = excluded.value_json,
        updated_ts_utc = excluded.updated_ts_utc
      """,
      (key, json.dumps(merged[key], separators=(",", ":"), ensure_ascii=False), now_ts),
    )
  return merged


def reset_settings_payload(conn: sqlite3.Connection) -> Dict[str, object]:
  ensure_settings_table(conn)
  now_ts = now_utc_iso()
  for key, value in SETTINGS_DEFAULTS.items():
    conn.execute(
      """
      INSERT INTO settings (key, value_json, updated_ts_utc)
      VALUES (?, ?, ?)
      ON CONFLICT(key) DO UPDATE SET
        value_json = excluded.value_json,
        updated_ts_utc = excluded.updated_ts_utc
      """,
      (key, json.dumps(value, separators=(",", ":"), ensure_ascii=False), now_ts),
    )
  return dict(SETTINGS_DEFAULTS)


def radar_presence_mode(settings: Dict[str, object]) -> str:
  mode = str(settings.get("radar_presence_mode") or "raw").strip().lower()
  return "derived" if mode == "derived" else "raw"


def radar_present_sql_predicate(settings: Dict[str, object], alias: str = "") -> (str, List[object]):
  prefix = alias or ""
  mode = radar_presence_mode(settings)
  if mode == "derived":
    near_cm = int(settings.get("radar_self_suppress_near_cm") or 80)
    near_cm = max(20, min(200, near_cm))
    # Approximation mode for SQL-only derived presence using non-near distance signals.
    return (
      f"{prefix}alive=1 AND ({prefix}target!=0 AND (({prefix}detect_cm IS NOT NULL AND {prefix}detect_cm > ?) OR ({prefix}move_cm IS NOT NULL AND {prefix}move_cm > ?) OR ({prefix}stat_cm IS NOT NULL AND {prefix}stat_cm > ?)))",
      [near_cm, near_cm, near_cm],
    )
  return (f"{prefix}alive=1 AND {prefix}target!=0", [])


def ensure_reports_table(conn: sqlite3.Connection) -> None:
  conn.execute(
    """
    CREATE TABLE IF NOT EXISTS reports (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts_utc TEXT NOT NULL,
      ts_local TEXT,
      range_start_utc TEXT NOT NULL,
      range_end_utc TEXT NOT NULL,
      preset TEXT NOT NULL,
      options_json TEXT NOT NULL,
      file_path TEXT,
      status TEXT NOT NULL,
      notes TEXT
    );
    """
  )
  conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_ts ON reports(ts_utc);")
  conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_range ON reports(range_start_utc, range_end_utc);")


def now_local_iso() -> str:
  return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def parse_local_datetime_input(value: str) -> datetime.datetime:
  text = str(value or "").strip()
  if not text:
    raise ValueError("empty local datetime")
  dt = datetime.datetime.fromisoformat(text)
  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=datetime.datetime.now().astimezone().tzinfo)
  return dt.astimezone()


def resolve_range_utc_from_request(
    *,
    preset: str,
    start_local: Optional[str] = None,
    end_local: Optional[str] = None,
) -> Dict[str, str]:
  preset_norm = str(preset or "24h").strip().lower()
  now_local = datetime.datetime.now().astimezone().replace(microsecond=0)
  if preset_norm == "24h":
    start = now_local - datetime.timedelta(hours=24)
    end = now_local
  elif preset_norm == "3d":
    start = now_local - datetime.timedelta(days=3)
    end = now_local
  elif preset_norm == "1w":
    start = now_local - datetime.timedelta(days=7)
    end = now_local
  elif preset_norm == "1m":
    start = now_local - datetime.timedelta(days=30)
    end = now_local
  elif preset_norm == "custom":
    if not start_local or not end_local:
      raise HTTPException(status_code=400, detail="custom range requires start_local and end_local")
    try:
      start = parse_local_datetime_input(start_local)
      end = parse_local_datetime_input(end_local)
    except Exception as exc:
      raise HTTPException(status_code=400, detail=f"invalid custom datetime: {exc}")
  else:
    raise HTTPException(status_code=400, detail="invalid preset")

  if end <= start:
    raise HTTPException(status_code=400, detail="end must be after start")
  if (end - start) > datetime.timedelta(days=MAX_RANGE_DAYS):
    raise HTTPException(status_code=400, detail=f"range exceeds {MAX_RANGE_DAYS} days")

  start_utc = start.astimezone(datetime.timezone.utc)
  end_utc = end.astimezone(datetime.timezone.utc)
  return {
    "preset": preset_norm,
    "start_local": start.isoformat(),
    "end_local": end.isoformat(),
    "start_utc": start_utc.isoformat(),
    "end_utc": end_utc.isoformat(),
  }


def html_escape(text: object) -> str:
  s = str(text or "")
  return (
    s.replace("&", "&amp;")
    .replace("<", "&lt;")
    .replace(">", "&gt;")
    .replace('"', "&quot;")
    .replace("'", "&#39;")
  )


def resolve_report_logo_src() -> str:
  logo_path = STATIC_DIR / "hermes-logo-h.jpg"
  try:
    raw = logo_path.read_bytes()
  except Exception:
    return "/static/hermes-logo-h.jpg"
  encoded = base64.b64encode(raw).decode("ascii")
  return f"data:image/jpeg;base64,{encoded}"


def build_report_html(
    *,
    range_info: Dict[str, str],
    include_presence: bool,
    include_air: bool,
    include_events: bool,
    include_rssi: bool,
    device_summary: Dict[str, object],
    presence_summary: Dict[str, object],
    air_summary: Dict[str, object],
    events_summary: Dict[str, object],
    rssi_summary: Dict[str, object],
) -> str:
  created_utc = now_utc_iso()
  created_local = now_local_iso()
  sections: List[str] = []

  sections.append(
    "<section><h2>Header</h2>"
    f"<p><b>Created:</b> {html_escape(created_local)} ({html_escape(created_utc)})</p>"
    f"<p><b>Range:</b> {html_escape(range_info['start_local'])} → {html_escape(range_info['end_local'])}</p>"
    f"<p><b>Preset:</b> {html_escape(range_info['preset'])}</p>"
    f"<p><b>Device summary:</b> {html_escape(json.dumps(device_summary, ensure_ascii=False))}</p>"
    "</section>"
  )

  if include_presence:
    sections.append(
      "<section><h2>Presence Summary</h2>"
      f"<p>Total minutes present: <b>{presence_summary.get('minutes_present', 0):.2f}</b></p>"
      f"<p>Percent time present: <b>{presence_summary.get('percent_present', 0):.2f}%</b></p>"
      f"<p>Moving minutes: <b>{presence_summary.get('moving_minutes', 0):.2f}</b> | Still minutes: <b>{presence_summary.get('still_minutes', 0):.2f}</b></p>"
      "<p><i>Note: moving and still minutes can overlap during mixed-target frames.</i></p>"
      "</section>"
    )

  if include_air:
    sections.append(
      "<section><h2>Air Quality Summary</h2>"
      f"<p>Avg ECO2: <b>{air_summary.get('avg_eco2_ppm')}</b> ppm | Max ECO2: <b>{air_summary.get('max_eco2_ppm')}</b> ppm</p>"
      f"<p>Avg Temp: <b>{air_summary.get('avg_temp_c')}</b> °C | Avg Humidity: <b>{air_summary.get('avg_hum_pct')}</b> %</p>"
      "</section>"
    )

  if include_events:
    top_rows = events_summary.get("top_events", []) or []
    rows_html = "".join(
      "<tr>"
      f"<td>{html_escape(row.get('ts_local') or row.get('ts_utc') or '')}</td>"
      f"<td>{html_escape(row.get('severity') or '')}</td>"
      f"<td>{html_escape(row.get('kind') or '')}</td>"
      f"<td>{html_escape(row.get('message') or '')}</td>"
      "</tr>"
      for row in top_rows
    )
    sections.append(
      "<section><h2>Events Summary</h2>"
      f"<p>By severity: {html_escape(json.dumps(events_summary.get('by_severity', {}), ensure_ascii=False))}</p>"
      "<table><thead><tr><th>When</th><th>Severity</th><th>Kind</th><th>Message</th></tr></thead>"
      f"<tbody>{rows_html}</tbody></table>"
      "</section>"
    )

  if include_rssi:
    sections.append(
      "<section><h2>RSSI Summary</h2>"
      f"<p>Avg RSSI: <b>{rssi_summary.get('avg_rssi_dbm')}</b> dBm | Min RSSI: <b>{rssi_summary.get('min_rssi_dbm')}</b> dBm | Max RSSI: <b>{rssi_summary.get('max_rssi_dbm')}</b> dBm</p>"
      "</section>"
    )

  sections_html = "\n".join(sections)
  report_logo_src = resolve_report_logo_src()
  html_template = """
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <title>HERMES Report</title>
  <style>
    body { font-family: Inter, Arial, sans-serif; margin: 18px; color: #1e2937; }
    .report-brand { margin-bottom: 10px; }
    .report-brand img { height: 30px; width: auto; display: block; }
    h1 { margin-bottom: 8px; }
    h2 { margin: 18px 0 6px 0; font-size: 18px; }
    section { border: 1px solid #d6dee8; border-radius: 8px; padding: 10px 12px; margin-bottom: 10px; }
    p { margin: 6px 0; }
    table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }
    th, td { border-bottom: 1px solid #d6dee8; text-align: left; padding: 6px 8px; }
    th { background: #f8fbff; }
  </style>
</head>
<body>
  <div class="report-brand"><img src="__REPORT_LOGO_SRC__" alt="HERMES logo" /></div>
  <h1>HERMES Report</h1>
  __SECTIONS__
</body>
</html>
"""
  return (
    html_template
    .replace("__SECTIONS__", sections_html)
    .replace("__REPORT_LOGO_SRC__", report_logo_src)
  )


def insert_event_row(
    conn: sqlite3.Connection,
    *,
    kind: str,
    severity: str,
    source: str,
    message: str,
    data: Optional[Dict[str, object]] = None,
    dedupe_key: Optional[str] = None,
) -> int:
  ensure_events_table(conn)
  now_utc = now_utc_iso()
  now_local = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()
  payload = json.dumps(data or {}, separators=(",", ":"), ensure_ascii=False)
  cur = conn.execute(
    """
    INSERT INTO events (ts_utc, ts_local, kind, severity, source, message, data_json, dedupe_key)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """,
    (now_utc, now_local, kind, severity, source, message, payload, dedupe_key),
  )
  return int(cur.lastrowid or 0)


def send_buzzer_command(payload: str) -> Dict[str, object]:
  cmd = run_cmd(["python3", str(CLIENT_PATH), "send", payload], timeout_sec=2)
  return {
    "ok": bool(cmd.get("ok")),
    "code": int(cmd.get("code", 1)),
    "raw": (cmd.get("stdout") or cmd.get("stderr") or "")[:200],
  }


def trigger_radar_calibration_beep() -> Dict[str, object]:
  return send_buzzer_command("BUZZER,BEEP,100")


def trigger_radar_calibration_jingle() -> Dict[str, object]:
  return send_buzzer_command("BUZZER,JINGLE,cal_done")


def grade_calibration(false_presence_count: int) -> str:
  count = int(false_presence_count or 0)
  if count < 5:
    return "A"
  if count < 20:
    return "B"
  return "C"


def calibration_recommendation(grade: str) -> str:
  key = str(grade or "").strip().upper()
  if key == "A":
    return "Stable baseline. Calibration quality is excellent."
  if key == "B":
    return "Acceptable baseline. Consider rerunning if false triggers continue."
  return "Noisy baseline. Recalibrate in a quieter empty-room setup."


def decorate_calibration_row(row: Dict[str, object]) -> Dict[str, object]:
  out = dict(row or {})
  false_presence_count = int(out.get("false_presence_count") or 0)
  grade = grade_calibration(false_presence_count)
  out["calibration_grade"] = grade
  out["calibration_recommendation"] = calibration_recommendation(grade)
  return out


def chime_payload_from_key(chime_key: str) -> Optional[str]:
  key = str(chime_key or "none").strip().lower()
  if key == "none":
    return None
  if key in VALID_CHIME_KEYS:
    return f"BUZZER,JINGLE,{key}"
  return None


def maybe_play_event_chime(rows: List[Dict[str, object]], settings: Dict[str, object]) -> None:
  global event_chime_initialized
  global event_chime_last_seen_id

  valid_rows: List[Dict[str, object]] = []
  for row in rows or []:
    try:
      row_id = int(row.get("id") or 0)
    except Exception:
      row_id = 0
    if row_id > 0:
      valid_rows.append(row)

  if not valid_rows:
    return

  max_seen = max(int(row.get("id") or 0) for row in valid_rows)
  baseline = 0
  initialized_now = False
  bootstrap_payload: Optional[str] = None
  with event_chime_lock:
    if not event_chime_initialized:
      event_chime_initialized = True
      event_chime_last_seen_id = max_seen
      initialized_now = True
    baseline = int(event_chime_last_seen_id)
    if max_seen <= baseline:
      return
    event_chime_last_seen_id = max_seen

  if initialized_now:
    newest = max(valid_rows, key=lambda row: int(row.get("id") or 0))
    event_kind = str(newest.get("kind") or "")
    if event_kind == "dashboard_restart":
      settings_key = EVENT_KIND_TO_CHIME_SETTING.get(event_kind)
      if settings_key:
        payload = chime_payload_from_key(str(settings.get(settings_key) or "none"))
        if payload:
          ts_raw = str(newest.get("ts_utc") or "").strip()
          if ts_raw:
            try:
              age_s = (datetime.datetime.now(datetime.timezone.utc) - parse_iso8601_utc(ts_raw)).total_seconds()
            except Exception:
              age_s = float(STARTUP_CHIME_BOOTSTRAP_MAX_AGE_SECS + 1)
            if 0 <= age_s <= float(STARTUP_CHIME_BOOTSTRAP_MAX_AGE_SECS):
              bootstrap_payload = payload
          else:
            bootstrap_payload = payload
    if bootstrap_payload:
      send_buzzer_command(bootstrap_payload)
    return

  candidates = [row for row in valid_rows if int(row.get("id") or 0) > baseline]
  if not candidates:
    return

  newest = max(candidates, key=lambda row: int(row.get("id") or 0))
  event_kind = str(newest.get("kind") or "")
  settings_key = EVENT_KIND_TO_CHIME_SETTING.get(event_kind)
  if not settings_key:
    return
  payload = chime_payload_from_key(str(settings.get(settings_key) or "none"))
  if not payload:
    return
  send_buzzer_command(payload)


def maybe_play_presence_change_chime(settings: Dict[str, object]) -> None:
  key = str(settings.get("chime_event_presence_change") or "none")
  payload = chime_payload_from_key(key)
  if not payload:
    return
  global presence_change_last_chime_ts
  now_ts = time.time()
  with event_chime_lock:
    if (now_ts - float(presence_change_last_chime_ts)) < 3.0:
      return
    presence_change_last_chime_ts = now_ts
  send_buzzer_command(payload)


def should_log_presence_transition(conn: sqlite3.Connection, *, ts_utc: str, from_state: str, to_state: str) -> bool:
  ensure_state_events_table(conn)
  fp = f"{ts_utc}|{from_state}|{to_state}"
  row = conn.execute(
    "SELECT detail FROM state_events WHERE event_type='presence_change' ORDER BY id DESC LIMIT 1"
  ).fetchone()
  if not row:
    return True
  raw = row["detail"]
  if raw is None:
    return True
  try:
    payload = json.loads(str(raw))
    return str(payload.get("_fp") or "") != fp
  except Exception:
    return fp not in str(raw)


def summarize_radar_calibration_window(
    conn: sqlite3.Connection,
    *,
    start_ts_utc: str,
    end_ts_utc: str,
    duration_s: int,
    max_range_cm: int,
) -> Dict[str, object]:
  conn.row_factory = sqlite3.Row
  rows = conn.execute(
    """
    SELECT alive, target, detect_cm, move_cm, stat_cm, move_en, stat_en, ts_utc
    FROM radar
    WHERE ts_utc >= ? AND ts_utc <= ?
    ORDER BY ts_utc ASC
    """,
    (start_ts_utc, end_ts_utc),
  ).fetchall()

  alive_rows = [r for r in rows if int(r["alive"] or 0) == 1]
  samples = len(alive_rows)
  false_presence_count = sum(1 for r in alive_rows if int(r["target"] or 0) != 0)
  empty_detect = [float(r["detect_cm"]) for r in alive_rows if int(r["target"] or 0) == 0 and r["detect_cm"] is not None]
  all_detect = [float(r["detect_cm"]) for r in alive_rows if r["detect_cm"] is not None]

  notes: List[str] = []
  baseline_source = empty_detect
  if not baseline_source:
    baseline_source = all_detect
    if baseline_source:
      notes.append("no target==0 samples; baseline from all alive samples")
    else:
      notes.append("no alive detect samples")

  baseline_detect_cm: Optional[float] = None
  noise_detect_cm: Optional[float] = None
  if baseline_source:
    baseline_detect_cm = float(statistics.median(baseline_source))
    med = baseline_detect_cm
    abs_dev = [abs(v - med) for v in baseline_source]
    noise_detect_cm = float(statistics.median(abs_dev)) if abs_dev else 0.0

  now_utc = now_utc_iso()
  now_local = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()
  result = {
    "ts_utc": now_utc,
    "ts_local": now_local,
    "duration_s": int(duration_s),
    "max_range_cm": int(max_range_cm),
    "samples": int(samples),
    "baseline_detect_cm": baseline_detect_cm,
    "noise_detect_cm": noise_detect_cm,
    "false_presence_count": int(false_presence_count),
    "notes": "; ".join(notes) if notes else "",
  }
  return result


def finalize_radar_calibration_session(session_id: str) -> Dict[str, object]:
  with radar_calibration_lock:
    session = radar_calibration_sessions.get(session_id)
    if not session:
      raise HTTPException(status_code=404, detail="session not found")
    if session.get("status") in {"done", "cancelled", "error"}:
      return dict(session)
    session["status"] = "finalizing"

  try:
    with open_db() as conn:
      ensure_radar_calibration_table(conn)
      summary = summarize_radar_calibration_window(
        conn,
        start_ts_utc=str(session.get("capture_start_ts_utc") or session["start_ts_utc"]),
        end_ts_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        duration_s=int(session["duration_s"]),
        max_range_cm=int(session["max_range_cm"]),
      )
      cur = conn.execute(
        """
        INSERT INTO radar_calibration (
          ts_utc, ts_local, duration_s, max_range_cm, samples, baseline_detect_cm, noise_detect_cm,
          false_presence_count, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
          summary["ts_utc"],
          summary["ts_local"],
          summary["duration_s"],
          summary["max_range_cm"],
          summary["samples"],
          summary["baseline_detect_cm"],
          summary["noise_detect_cm"],
          summary["false_presence_count"],
          summary["notes"],
        ),
      )
      calibration_id = int(cur.lastrowid or 0)

      msg = (
        "Radar calibration complete: "
        f"samples={summary['samples']}, false_presence={summary['false_presence_count']}, "
        f"baseline_detect={summary['baseline_detect_cm'] if summary['baseline_detect_cm'] is not None else 'n/a'}, "
        f"noise={summary['noise_detect_cm'] if summary['noise_detect_cm'] is not None else 'n/a'}"
      )
      insert_event_row(
        conn,
        kind="radar_calibration",
        severity="info",
        source="dashboard",
        message=msg,
        data={"session_id": session_id, "calibration_id": calibration_id, **summary},
      )
      conn.commit()

    buzzer = trigger_radar_calibration_jingle()
    result = decorate_calibration_row({"id": calibration_id, **summary, "buzzer": buzzer})
    with radar_calibration_lock:
      session = radar_calibration_sessions.get(session_id, session)
      session["status"] = "done"
      session["result"] = result
      session["completed_ts_utc"] = now_utc_iso()
      radar_calibration_sessions[session_id] = session
    return dict(session)
  except Exception as exc:
    with radar_calibration_lock:
      session = radar_calibration_sessions.get(session_id, session)
      session["status"] = "error"
      session["error"] = str(exc)
      radar_calibration_sessions[session_id] = session
    raise


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


def ensure_state_events_table(conn: sqlite3.Connection) -> None:
  conn.execute(
    """
    CREATE TABLE IF NOT EXISTS state_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts_utc TEXT NOT NULL,
      event_type TEXT NOT NULL,
      detail TEXT
    );
    """
  )
  conn.execute("CREATE INDEX IF NOT EXISTS idx_state_events_ts ON state_events(ts_utc);")
  conn.execute("CREATE INDEX IF NOT EXISTS idx_state_events_type ON state_events(event_type);")


def insert_state_event(conn: sqlite3.Connection, *, event_type: str, detail: Dict[str, object]) -> int:
  ensure_state_events_table(conn)
  payload = json.dumps(detail or {}, separators=(",", ":"), ensure_ascii=False)
  cur = conn.execute(
    "INSERT INTO state_events (ts_utc, event_type, detail) VALUES (?, ?, ?)",
    (now_utc_iso(), str(event_type), payload),
  )
  return int(cur.lastrowid or 0)


def compute_air_anomaly_level_for_row(conn: sqlite3.Connection, row: Optional[sqlite3.Row]) -> str:
  if row is None:
    return "none"
  eco2 = row["eco2_ppm"]
  ts_utc = row["ts_utc"]
  if eco2 is None or not ts_utc:
    return "none"
  try:
    current_eco2 = float(eco2)
  except Exception:
    return "none"
  baseline_row = conn.execute(
    """
    SELECT AVG(eco2_ppm) AS avg_eco2
    FROM air
    WHERE eco2_ppm IS NOT NULL
      AND ts_utc >= datetime(?, '-30 minutes')
      AND ts_utc < ?
    """,
    (str(ts_utc), str(ts_utc)),
  ).fetchone()
  if not baseline_row or baseline_row[0] is None:
    return "none"
  try:
    baseline_eco2 = float(baseline_row[0])
  except Exception:
    return "none"
  delta = current_eco2 - baseline_eco2
  if delta > 300.0:
    return "high"
  if delta > 150.0:
    return "moderate"
  return "none"


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


def valid_rssi_dbm(rssi: object) -> bool:
  try:
    value = int(rssi)
  except Exception:
    return False
  if value == RSSI_NOT_CONNECTED:
    return False
  return RSSI_MIN_DBM <= value <= RSSI_MAX_DBM


def wifi_state_from_row(row: Optional[sqlite3.Row]) -> Dict[str, object]:
  if row is None:
    return {
      "present": False,
      "connected": False,
      "wifist": None,
      "rssi": None,
      "ip": None,
      "valid_rssi": False,
      "reason": "missing",
    }
  wifist = row["wifist"]
  rssi = row["rssi"]
  ip = row["ip"]
  connected = False
  try:
    connected = int(wifist) in WIFI_CONNECTED_STATES
  except Exception:
    connected = False
  valid_rssi = valid_rssi_dbm(rssi)
  reason = "ok"
  if not connected:
    reason = "disconnected"
  elif not valid_rssi:
    reason = "bad_rssi"
  return {
    "present": True,
    "connected": connected,
    "wifist": wifist,
    "rssi": rssi,
    "ip": ip,
    "valid_rssi": valid_rssi,
    "reason": reason,
  }


def build_ready_state() -> Dict[str, object]:
  failures: List[str] = []
  table_age_seconds: Dict[str, float] = {table: -1.0 for table in TABLES}
  db_exists = DB_PATH.exists()
  db_readable = False
  wifi_state: Dict[str, object] = {
    "present": False,
    "connected": False,
    "wifist": None,
    "rssi": None,
    "ip": None,
    "valid_rssi": False,
    "reason": "missing",
  }

  if not db_exists:
    failures.append("db_missing")
  else:
    try:
      with open_db() as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT 1").fetchone()
        table_age_seconds = current_table_ages(conn)
        esp_row = conn.execute("SELECT wifist, rssi, ip FROM esp_net ORDER BY id DESC LIMIT 1").fetchone()
        wifi_state = wifi_state_from_row(esp_row)
        db_readable = True
    except sqlite3.OperationalError as exc:
      if "locked" in str(exc).lower():
        failures.append("db_locked")
      else:
        failures.append("db_operational_error")
    except Exception:
      failures.append("db_unreadable")

  for table in READY_TABLES:
    age = table_age_seconds.get(table, -1.0)
    if age < 0:
      failures.append(f"stale_{table}:missing")
      continue
    if age > READY_MAX_AGE_SECS:
      failures.append(f"stale_{table}:{int(age)}s")

  status_cmd = run_cmd(["python3", str(CLIENT_PATH), "status"], timeout_sec=2)
  if not status_cmd["ok"]:
    failures.append("logger_status_failed")

  if not wifi_state.get("present"):
    failures.append("wifi_missing")
  else:
    if not wifi_state.get("connected"):
      failures.append(f"wifi_disconnected:wifist={wifi_state.get('wifist')}")
    elif not wifi_state.get("valid_rssi"):
      failures.append(f"wifi_bad_rssi:{wifi_state.get('rssi')}")

  return {
    "ready": len(failures) == 0,
    "failures": failures,
    "db_exists": db_exists,
    "db_readable": db_readable,
    "logger_ok": bool(status_cmd["ok"]),
    "wifi": wifi_state,
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
      if "no such table" in str(exc).lower():
        return []
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


def render_sparkline_png(series: str, minutes: int, points: List[Dict[str, object]], width_px: int, height_px: int, device_pixel_ratio: float) -> bytes:
  cfg = SERIES_MAP[series]
  render_start = time.perf_counter()
  width_px = max(180, min(2200, int(width_px or 528)))
  height_px = max(120, min(1400, int(height_px or 226)))
  dpr = max(1.0, min(3.0, float(device_pixel_ratio or 1.0)))
  base_dpi = 100
  fig_w = width_px / float(base_dpi)
  fig_h = height_px / float(base_dpi)
  render_dpi = int(round(base_dpi * dpr))
  try:
    with chart_render_lock:
      fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=render_dpi)
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
            "radar_target": 3.0,
            "radar_bodies": 2.0,
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

      if series == "radar_detect_cm":
        ax.scatter(x, y, s=12, color=cfg["color"], alpha=0.95)
      else:
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
    if points:
      lo, hi = ax.get_ylim()
      if math.isfinite(lo) and math.isfinite(hi) and hi > lo:
        tick_count = 4
        step = (hi - lo) / (tick_count - 1)
        ticks = [lo + (i * step) for i in range(tick_count)]
        ax.set_yticks(ticks)
        max_abs = max(abs(lo), abs(hi))
        decimals = 0 if max_abs >= 100 else 1
        ax.set_yticklabels([f"{t:.{decimals}f}" for t in ticks], color="#8ea1b3", fontsize=7)
        ax.tick_params(axis="y", colors="#8ea1b3", length=2, width=0.6)
        ax.grid(axis="y", color="#2a3440", alpha=0.45, linewidth=0.6)
      else:
        ax.set_yticks([])
    else:
      ax.set_yticks([])

    for name, spine in ax.spines.items():
      if name == "left":
        spine.set_visible(True)
        spine.set_color("#2a3440")
        spine.set_linewidth(0.8)
      else:
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
    freshness = parse_freshness(raw if isinstance(raw, str) else "")
    try:
      with open_db() as conn:
        conn.row_factory = sqlite3.Row
        esp_row = conn.execute("SELECT wifist, rssi, ip FROM esp_net ORDER BY id DESC LIMIT 1").fetchone()
        wifi_state = wifi_state_from_row(esp_row)
      if not wifi_state.get("connected") or not wifi_state.get("valid_rssi"):
        freshness["ESP,NET"] = "dead"
    except Exception:
      pass
    return {
        "ok": cmd["ok"],
        "code": cmd["code"],
        "raw": raw,
        "freshness": freshness,
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
    settings_payload: Dict[str, object] = dict(SETTINGS_DEFAULTS)
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
        settings_payload = get_settings_payload(conn)
    except sqlite3.OperationalError as exc:
      if "locked" in str(exc).lower():
        return {"limit": limit, "rows": []}
      raise
    maybe_play_event_chime(rows_out, settings_payload)
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


@APP.get("/api/integrity")
def api_integrity() -> Dict[str, object]:
  cmd = run_cmd(["python3", str(CLIENT_PATH), "INTEGRITY"], timeout_sec=2)
  if cmd.get("ok"):
    out = str(cmd.get("stdout") or "").strip()
    if out.startswith("{"):
      try:
        data = json.loads(out)
        if isinstance(data, dict):
          return data
      except Exception:
        pass
  try:
    from hermes.linux.logger.daemon import get_ingest_snapshot  # type: ignore
    return get_ingest_snapshot()
  except Exception:
    try:
      daemon_path = Path(__file__).resolve().parents[3] / "logger" / "daemon.py"
      spec = importlib.util.spec_from_file_location("hermes_logger_daemon_runtime", str(daemon_path))
      if spec is None or spec.loader is None:
        raise RuntimeError("spec_load_failed")
      module = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(module)
      getter = getattr(module, "get_ingest_snapshot", None)
      if callable(getter):
        return getter()
    except Exception as exc:
      return {"fps_1m": {}, "parse_fail": 0, "truncated": 0, "error": str(exc)}
  return {"fps_1m": {}, "parse_fail": 0, "truncated": 0}


@APP.get("/api/state_events")
def api_state_events(limit: int = Query(50, ge=1, le=200)) -> Dict[str, object]:
  if not DB_PATH.exists():
    return {"rows": []}
  try:
    with open_db() as conn:
      conn.row_factory = sqlite3.Row
      ensure_state_events_table(conn)
      rows = conn.execute(
        "SELECT id, ts_utc, event_type, detail FROM state_events ORDER BY id DESC LIMIT ?",
        (int(limit),),
      ).fetchall()
  except sqlite3.OperationalError as exc:
    if "no such table" in str(exc).lower() or "locked" in str(exc).lower():
      return {"rows": []}
    raise

  output: List[Dict[str, object]] = []
  for row in rows:
    detail_raw = row["detail"]
    detail_obj: object = detail_raw
    if isinstance(detail_raw, str) and detail_raw.strip():
      try:
        detail_obj = json.loads(detail_raw)
      except Exception:
        detail_obj = detail_raw
    output.append(
      {
        "id": int(row["id"] or 0),
        "ts_utc": row["ts_utc"],
        "event_type": str(row["event_type"] or ""),
        "detail": detail_obj,
      }
    )
  return {"rows": output}


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


def compute_presence_derived(latest_radar_row, settings, recent_radar_rows=None) -> Dict[str, object]:
    def _to_int(value) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    def _present_raw(row) -> bool:
        alive = _to_int(row["alive"] if row else None) or 0
        target = _to_int(row["target"] if row else None) or 0
        move_en = _to_int(row["move_en"] if row else None) or 0
        stat_en = _to_int(row["stat_en"] if row else None) or 0
        return (alive == 1 and target != 0) or (move_en == 1 or stat_en == 1)

    def _near_from_row(row, near_cm: int) -> bool:
        detect_cm = _to_int(row["detect_cm"] if row else None)
        move_cm = _to_int(row["move_cm"] if row else None)
        stat_cm = _to_int(row["stat_cm"] if row else None)
        if detect_cm is not None and detect_cm > 0 and detect_cm <= near_cm:
            return True
        candidates = [v for v in (move_cm, stat_cm) if v is not None and v > 0]
        if candidates and min(candidates) <= near_cm:
            return True
        return False

    result = {
        "present_raw": False,
        "present_derived": False,
        "self_suppressed": False,
        "self_reason": None,
    }
    if not latest_radar_row:
        return result

    present_raw = _present_raw(latest_radar_row)
    result["present_raw"] = bool(present_raw)
    result["present_derived"] = bool(present_raw)

    enabled = bool(settings.get("radar_self_suppress_enabled"))
    if not enabled or not present_raw:
        return result

    near_cm = max(20, min(200, _to_int(settings.get("radar_self_suppress_near_cm")) or 80))
    persist_s = max(5, min(120, _to_int(settings.get("radar_self_suppress_persist_s")) or 20))
    jitter_cm = max(2, min(80, _to_int(settings.get("radar_self_suppress_jitter_cm")) or 15))

    detect_now = _to_int(latest_radar_row["detect_cm"])
    near_now = _near_from_row(latest_radar_row, near_cm)
    if not near_now or detect_now is None or detect_now <= 0:
        return result

    rows = list(recent_radar_rows or [])
    if not rows:
        return result

    detect_samples: List[int] = []
    for row in rows:
        detect_val = _to_int(row["detect_cm"] if row else None)
        if detect_val is not None and detect_val > 0:
            detect_samples.append(detect_val)
    if not detect_samples:
        return result

    median_detect = float(statistics.median(detect_samples))
    stable_now = abs(float(detect_now) - median_detect) <= float(jitter_cm)
    if not stable_now:
        return result

    stable_near_times: List[datetime.datetime] = []
    latest_ts: Optional[datetime.datetime] = None
    for row in rows:
        ts_raw = row["ts_utc"] if row else None
        if not ts_raw:
            continue
        try:
            ts_val = parse_iso8601_utc(str(ts_raw))
        except Exception:
            continue
        if latest_ts is None:
            latest_ts = ts_val
        detect_val = _to_int(row["detect_cm"] if row else None)
        if detect_val is None or detect_val <= 0:
            continue
        near = _near_from_row(row, near_cm)
        stable = abs(float(detect_val) - median_detect) <= float(jitter_cm)
        if near and stable:
            stable_near_times.append(ts_val)

    if latest_ts is None or not stable_near_times:
        return result

    oldest_stable_near = min(stable_near_times)
    persisted_seconds = max(0.0, (latest_ts - oldest_stable_near).total_seconds())
    if persisted_seconds < float(persist_s):
        return result

    target_now = _to_int(latest_radar_row["target"]) or 0
    move_now = _to_int(latest_radar_row["move_cm"])
    stat_now = _to_int(latest_radar_row["stat_cm"])
    evidence_non_near = (
        ((detect_now is not None and detect_now > near_cm) and target_now != 0)
        or (move_now is not None and move_now > near_cm)
        or (stat_now is not None and stat_now > near_cm)
    )
    if evidence_non_near:
        return result

    result["present_derived"] = False
    result["self_suppressed"] = True
    result["self_reason"] = "persistent_near_stable_signature"
    return result


@APP.get("/api/radar/latest")
def api_radar_latest() -> Dict[str, object]:
  default = {
    "alive": 0,
    "target": 0,
    "detect_cm": 0,
    "move_cm": 0,
    "stat_cm": 0,
    "move_en": 0,
    "stat_en": 0,
    "ts_utc": None,
    "present_raw": False,
    "present_derived": False,
    "self_suppressed": False,
    "self_reason": None,
  }
  if not DB_PATH.exists():
    return default

  try:
    with open_db() as conn:
      conn.row_factory = sqlite3.Row
      ensure_state_events_table(conn)
      settings_payload: Dict[str, object] = get_settings_payload(conn)
      row = conn.execute(
        "SELECT alive, target, detect_cm, move_cm, stat_cm, move_en, stat_en, ts_utc "
        "FROM radar ORDER BY id DESC LIMIT 1"
      ).fetchone()
      if not row:
        return default

      prev_row = conn.execute(
        "SELECT alive, target, detect_cm, move_cm, stat_cm, move_en, stat_en, ts_utc "
        "FROM radar ORDER BY id DESC LIMIT 1 OFFSET 1"
      ).fetchone()
      wifi_latest_row = conn.execute(
        "SELECT wifist, rssi, ip, ts_utc FROM esp_net ORDER BY id DESC LIMIT 1"
      ).fetchone()
      wifi_prev_row = conn.execute(
        "SELECT wifist, rssi, ip, ts_utc FROM esp_net ORDER BY id DESC LIMIT 1 OFFSET 1"
      ).fetchone()
      air_latest_row = conn.execute(
        "SELECT id, ts_utc, eco2_ppm FROM air ORDER BY id DESC LIMIT 1"
      ).fetchone()
      air_prev_row = conn.execute(
        "SELECT id, ts_utc, eco2_ppm FROM air ORDER BY id DESC LIMIT 1 OFFSET 1"
      ).fetchone()

      recent_rows = []
      if row["ts_utc"]:
        try:
          latest_ts = parse_iso8601_utc(str(row["ts_utc"]))
          persist_s = int(settings_payload.get("radar_self_suppress_persist_s") or 20)
          window_s = max(60, min(120, persist_s * 2))
          cutoff_ts = (latest_ts - datetime.timedelta(seconds=window_s)).isoformat()
          recent_rows = conn.execute(
            "SELECT alive, target, detect_cm, move_cm, stat_cm, move_en, stat_en, ts_utc "
            "FROM radar WHERE ts_utc >= ? ORDER BY ts_utc DESC LIMIT 400",
            (cutoff_ts,),
          ).fetchall()
        except Exception:
          recent_rows = conn.execute(
            "SELECT alive, target, detect_cm, move_cm, stat_cm, move_en, stat_en, ts_utc "
            "FROM radar ORDER BY ts_utc DESC LIMIT 200",
          ).fetchall()

      payload = {
        "alive": int(row["alive"] or 0),
        "target": int(row["target"] or 0),
        "detect_cm": int(row["detect_cm"] or 0),
        "move_cm": int(row["move_cm"] or 0),
        "stat_cm": int(row["stat_cm"] or 0),
        "move_en": int(row["move_en"] or 0),
        "stat_en": int(row["stat_en"] or 0),
        "ts_utc": row["ts_utc"],
      }
      derived = compute_presence_derived(payload, settings_payload, recent_rows)
      payload.update(derived)
      mode = radar_presence_mode(settings_payload)
      pred_sql, pred_params = radar_present_sql_predicate(settings_payload)
      payload["presence_debug"] = {
        "mode": mode,
        "present_predicate_sql": pred_sql,
        "present_predicate_params": pred_params,
        "raw_present_bool": bool((int(payload.get("alive") or 0) == 1) and (int(payload.get("target") or 0) != 0)),
        "derived_present_bool": bool(payload.get("present_derived")) if "present_derived" in payload else None,
        "self_suppressed": bool(payload.get("self_suppressed")) if "self_suppressed" in payload else None,
      }

      wifi_state = wifi_state_from_row(wifi_latest_row)
      payload["wifi_connected"] = bool(wifi_state.get("connected"))
      payload["wifist"] = wifi_state.get("wifist")

      air_anomaly_level = compute_air_anomaly_level_for_row(conn, air_latest_row)
      payload["air_anomaly_level"] = air_anomaly_level

      if prev_row is not None:
        prev_payload = {
          "alive": int(prev_row["alive"] or 0),
          "target": int(prev_row["target"] or 0),
          "detect_cm": int(prev_row["detect_cm"] or 0),
          "move_cm": int(prev_row["move_cm"] or 0),
          "stat_cm": int(prev_row["stat_cm"] or 0),
          "move_en": int(prev_row["move_en"] or 0),
          "stat_en": int(prev_row["stat_en"] or 0),
          "ts_utc": prev_row["ts_utc"],
        }
        prev_derived = compute_presence_derived(prev_payload, settings_payload, [])
        prev_present = bool(prev_derived.get("present_derived"))
        cur_present = bool(payload.get("present_derived"))
        if prev_present != cur_present:
          from_state = "present" if prev_present else "absent"
          to_state = "present" if cur_present else "absent"
          ts_marker = str(payload.get("ts_utc") or "")
          if should_log_presence_transition(conn, ts_utc=ts_marker, from_state=from_state, to_state=to_state):
            insert_state_event(
              conn,
              event_type="presence_change",
              detail={
                "from": from_state,
                "to": to_state,
                "ts_utc": payload.get("ts_utc"),
                "_fp": f"{ts_marker}|{from_state}|{to_state}",
              },
            )
            maybe_play_presence_change_chime(settings_payload)

      if wifi_prev_row is not None:
        prev_wifi = wifi_state_from_row(wifi_prev_row)
        prev_up = bool(prev_wifi.get("connected"))
        cur_up = bool(wifi_state.get("connected"))
        if prev_up != cur_up:
          insert_state_event(
            conn,
            event_type="wifi_change",
            detail={
              "from": "up" if prev_up else "down",
              "to": "up" if cur_up else "down",
              "wifist": wifi_state.get("wifist"),
              "rssi": wifi_state.get("rssi"),
              "ip": wifi_state.get("ip"),
            },
          )

      if air_prev_row is not None:
        prev_air_level = compute_air_anomaly_level_for_row(conn, air_prev_row)
        prev_raised = prev_air_level != "none"
        cur_raised = air_anomaly_level != "none"
        if prev_raised != cur_raised:
          insert_state_event(
            conn,
            event_type="air_anomaly_change",
            detail={
              "from": "raised" if prev_raised else "cleared",
              "to": "raised" if cur_raised else "cleared",
              "level": air_anomaly_level,
            },
          )

      conn.commit()
      return payload
  except sqlite3.OperationalError as exc:
    if "no such table" in str(exc).lower() or "locked" in str(exc).lower():
      return default
    raise


@APP.post("/api/radar/calibrate")
def api_radar_calibrate_start(payload: Dict[str, object] = Body(default={})) -> Dict[str, object]:
    duration_s = int(payload.get("duration_s", 60) or 60)
    max_range_cm = int(payload.get("max_range_cm", 600) or 600)
    pre_countdown_s = 10
    duration_s = max(10, min(duration_s, 300))
    max_range_cm = max(100, min(max_range_cm, 1200))

    if not DB_PATH.exists():
      raise HTTPException(status_code=409, detail="radar_stream_dead: database missing")

    try:
      with open_db() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT ts_utc FROM radar ORDER BY id DESC LIMIT 1").fetchone()
    except Exception:
      row = None
    if not row or not row["ts_utc"]:
      raise HTTPException(status_code=409, detail="radar_stream_dead: no radar samples yet")
    try:
      ts_last = parse_iso8601_utc(str(row["ts_utc"]))
      age_s = max(0.0, (datetime.datetime.now(datetime.timezone.utc) - ts_last).total_seconds())
    except Exception:
      age_s = 9999.0
    if age_s > 12.0:
      raise HTTPException(status_code=409, detail=f"radar_stream_dead: latest sample age={age_s:.1f}s")

    session_id = uuid.uuid4().hex[:12]
    started_at = datetime.datetime.now(datetime.timezone.utc)
    capture_start_at = started_at + datetime.timedelta(seconds=pre_countdown_s)
    ends_at = capture_start_at + datetime.timedelta(seconds=duration_s)
    session = {
      "session_id": session_id,
      "status": "prepare",
      "duration_s": duration_s,
      "pre_countdown_s": pre_countdown_s,
      "max_range_cm": max_range_cm,
      "start_ts_utc": started_at.replace(microsecond=0).isoformat(),
      "capture_start_ts_utc": capture_start_at.replace(microsecond=0).isoformat(),
      "start_monotonic": time.monotonic(),
      "next_beep_capture_s": 10,
      "ends_at_ts_utc": ends_at.replace(microsecond=0).isoformat(),
      "result": None,
    }
    with radar_calibration_lock:
      radar_calibration_sessions[session_id] = session
    return {
      "session_id": session_id,
      "status": "prepare",
      "duration_s": duration_s,
      "pre_countdown_s": pre_countdown_s,
      "max_range_cm": max_range_cm,
      "capture_starts_at": session["capture_start_ts_utc"],
      "ends_at": session["ends_at_ts_utc"],
    }


@APP.get("/api/radar/calibrate/{session_id}")
def api_radar_calibrate_status(session_id: str) -> Dict[str, object]:
    with radar_calibration_lock:
      session = radar_calibration_sessions.get(session_id)
    if not session:
      raise HTTPException(status_code=404, detail="session not found")

    status = str(session.get("status"))
    elapsed_total_s = max(0.0, time.monotonic() - float(session.get("start_monotonic", time.monotonic())))
    duration_s = int(session.get("duration_s", 60))
    pre_countdown_s = int(session.get("pre_countdown_s", 10))
    elapsed_capture_s = max(0.0, elapsed_total_s - pre_countdown_s)
    capture_started = elapsed_total_s >= pre_countdown_s

    if status in {"prepare", "running"}:
      status = "running" if capture_started else "prepare"

    if status == "running" and elapsed_capture_s >= duration_s:
      session = finalize_radar_calibration_session(session_id)
      status = str(session.get("status"))

    if status == "running":
      with radar_calibration_lock:
        session_live = radar_calibration_sessions.get(session_id)
        if session_live and session_live.get("status") == "running":
          next_beep_capture_s = int(session_live.get("next_beep_capture_s", 10))
          while elapsed_capture_s >= next_beep_capture_s and next_beep_capture_s <= duration_s:
            trigger_radar_calibration_beep()
            next_beep_capture_s += 10
          session_live["next_beep_capture_s"] = next_beep_capture_s
          radar_calibration_sessions[session_id] = session_live

    samples = 0
    false_presence_count = 0
    if status == "running":
      try:
        with open_db() as conn:
          conn.row_factory = sqlite3.Row
          settings_payload = get_settings_payload(conn)
          present_predicate, present_params = radar_present_sql_predicate(settings_payload)
          row = conn.execute(
            f"""
            SELECT
              SUM(CASE WHEN alive=1 THEN 1 ELSE 0 END) AS samples,
              SUM(CASE WHEN {present_predicate} THEN 1 ELSE 0 END) AS false_presence_count
            FROM radar
            WHERE ts_utc >= ? AND ts_utc <= ?
            """,
            (*present_params, str(session.get("capture_start_ts_utc")), datetime.datetime.now(datetime.timezone.utc).isoformat()),
          ).fetchone()
          if row:
            samples = int(row["samples"] or 0)
            false_presence_count = int(row["false_presence_count"] or 0)
      except Exception:
        pass

    remaining_s = 0
    if status == "prepare":
      remaining_s = max(0, int(round(pre_countdown_s - elapsed_total_s)))
    elif status == "running":
      remaining_s = max(0, int(round(duration_s - elapsed_capture_s)))

    response = {
      "session_id": session_id,
      "status": status,
      "phase": status,
      "elapsed_s": int(max(0, round(min(elapsed_capture_s, duration_s)))) if status == "running" else 0,
      "remaining_s": remaining_s,
      "pre_countdown_s": pre_countdown_s,
      "pre_remaining_s": max(0, int(round(pre_countdown_s - elapsed_total_s))) if status == "prepare" else 0,
      "samples": samples,
      "false_presence_count": false_presence_count,
    }
    if status == "done":
      response["result"] = session.get("result")
    if status == "error":
      response["error"] = session.get("error")
    if status == "cancelled":
      response["cancelled_ts_utc"] = session.get("cancelled_ts_utc")
    return response


@APP.post("/api/radar/calibrate/{session_id}/cancel")
def api_radar_calibrate_cancel(session_id: str) -> Dict[str, object]:
    with radar_calibration_lock:
      session = radar_calibration_sessions.get(session_id)
      if not session:
        raise HTTPException(status_code=404, detail="session not found")
      if session.get("status") in {"running", "prepare"}:
        session["status"] = "cancelled"
        session["cancelled_ts_utc"] = now_utc_iso()
        radar_calibration_sessions[session_id] = session
    return {"session_id": session_id, "status": str(session.get("status"))}


@APP.post("/api/radar/calibration/{calibration_id}/note")
def api_radar_calibration_note(calibration_id: int, payload: Dict[str, object] = Body(default={})) -> Dict[str, object]:
    note = str(payload.get("note") or "").strip()
    if len(note) > 400:
      raise HTTPException(status_code=400, detail="note too long")
    with open_db() as conn:
      ensure_radar_calibration_table(conn)
      cur = conn.execute("UPDATE radar_calibration SET notes=? WHERE id=?", (note, int(calibration_id)))
      conn.commit()
      if int(cur.rowcount or 0) == 0:
        raise HTTPException(status_code=404, detail="calibration not found")
    return {"ok": True, "id": int(calibration_id), "note": note}


@APP.get("/api/radar/calibration/history")
def api_radar_calibration_history(limit: int = Query(10, ge=1, le=100)) -> Dict[str, object]:
    if not DB_PATH.exists():
      return {"rows": []}
    try:
      with open_db() as conn:
        ensure_radar_calibration_table(conn)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM radar_calibration ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    except sqlite3.OperationalError as exc:
      if "locked" in str(exc).lower():
        return {"rows": []}
      raise
    return {"rows": [decorate_calibration_row(dict(r)) for r in rows]}


@APP.get("/api/radar/calibration/latest")
def api_radar_calibration_latest() -> Dict[str, object]:
    payload = api_radar_calibration_history(limit=1)
    rows = payload.get("rows") or []
    return {"row": rows[0] if rows else None}


@APP.get("/chart/{series}.png")
def chart_png(
  series: str,
  minutes: int = Query(60, ge=1, le=24 * 60),
  w: int = Query(528, ge=120, le=2400),
  h: int = Query(226, ge=100, le=1600),
  dpr: float = Query(1.0, ge=1.0, le=3.0),
) -> Response:
    if series not in SERIES_MAP:
        raise HTTPException(status_code=404, detail="series not allowed")

    if minutes <= 10:
        minutes = 5
    elif minutes <= 90:
        minutes = 60
    else:
        minutes = 240

    width_px = int(w)
    height_px = int(h)
    dpr_norm = float(dpr)

    cache_key = f"{series}:{minutes}:{width_px}:{height_px}:{dpr_norm:.2f}"
    now = time.time()
    with chart_cache_lock:
        cached = chart_cache.get(cache_key)
        if cached and (now - cached[0]) < CHART_CACHE_TTL_SECS:
            return Response(content=cached[1], media_type="image/png", headers={"Cache-Control": "no-store"})

    points = query_series(series, minutes)
    payload = render_sparkline_png(series, minutes, points, width_px, height_px, dpr_norm)

    with chart_cache_lock:
        chart_cache[cache_key] = (now, payload)
        if len(chart_cache) > MAX_CACHE_KEYS:
            oldest = sorted(chart_cache.items(), key=lambda kv: kv[1][0])[: max(1, len(chart_cache) - MAX_CACHE_KEYS)]
            for k, _ in oldest:
                chart_cache.pop(k, None)

    return Response(content=payload, media_type="image/png", headers={"Cache-Control": "no-store"})


@APP.get("/api/settings")
def api_settings_get() -> Dict[str, object]:
  if not DB_PATH.exists():
    return dict(SETTINGS_DEFAULTS)
  with open_db() as conn:
    return get_settings_payload(conn)


@APP.post("/api/settings")
def api_settings_post(payload: Dict[str, object] = Body(default={})) -> Dict[str, object]:
  if not isinstance(payload, dict):
    raise HTTPException(status_code=400, detail="payload must be an object")
  with open_db() as conn:
    result = save_settings_payload(conn, payload)
    conn.commit()
  return {"ok": True, "settings": result}


@APP.post("/api/settings/reset")
def api_settings_reset() -> Dict[str, object]:
  with open_db() as conn:
    result = reset_settings_payload(conn)
    conn.commit()
  return {"ok": True, "settings": result}


@APP.post("/api/chime/preview")
def api_chime_preview(payload: Dict[str, object] = Body(default={})) -> Dict[str, object]:
  if not isinstance(payload, dict):
    raise HTTPException(status_code=400, detail="payload must be an object")
  chime_key = str(payload.get("key") or "none")
  cmd_payload = chime_payload_from_key(chime_key)
  if not cmd_payload:
    return {"ok": True, "played": False, "key": chime_key}
  cmd = send_buzzer_command(cmd_payload)
  return {"ok": bool(cmd.get("ok")), "played": True, "key": chime_key, "command": cmd}


def ensure_analytics_indexes(conn: sqlite3.Connection) -> None:
  conn.execute("CREATE INDEX IF NOT EXISTS idx_radar_ts_utc ON radar(ts_utc);")
  conn.execute("CREATE INDEX IF NOT EXISTS idx_air_ts_utc ON air(ts_utc);")
  conn.execute("CREATE INDEX IF NOT EXISTS idx_env_ts_utc ON env(ts_utc);")
  conn.execute("CREATE INDEX IF NOT EXISTS idx_esp_net_ts_utc ON esp_net(ts_utc);")
  conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts_utc ON events(ts_utc);")


def estimate_radar_sample_seconds(conn: sqlite3.Connection) -> float:
  conn.row_factory = sqlite3.Row
  rows = conn.execute(
    """
    SELECT ts_utc
    FROM radar
    WHERE alive=1
    ORDER BY id DESC
    LIMIT 400
    """
  ).fetchall()
  if len(rows) < 2:
    return 0.1
  parsed: List[datetime.datetime] = []
  for row in rows:
    raw = row["ts_utc"]
    if not raw:
      continue
    try:
      parsed.append(parse_iso8601_utc(str(raw)))
    except Exception:
      continue
  if len(parsed) < 2:
    return 0.1
  deltas: List[float] = []
  for idx in range(1, len(parsed)):
    diff = abs((parsed[idx - 1] - parsed[idx]).total_seconds())
    if 0.01 <= diff <= 2.0:
      deltas.append(diff)
  if not deltas:
    return 0.1
  return float(statistics.median(deltas))


@APP.get("/api/analytics/presence_by_hour")
def api_analytics_presence_by_hour(hours: int = Query(24, ge=1, le=168)) -> Dict[str, object]:
  cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
  cutoff_iso_utc = cutoff.isoformat()
  with open_db() as conn:
    ensure_analytics_indexes(conn)
    conn.row_factory = sqlite3.Row
    settings_payload = get_settings_payload(conn)
    present_predicate, present_params = radar_present_sql_predicate(settings_payload)
    sample_sec = estimate_radar_sample_seconds(conn)
    rows = conn.execute(
      f"""
      SELECT
        CAST(strftime('%H', COALESCE(ts_local, ts_utc)) AS INTEGER) AS hour_local,
        COUNT(*) AS present_samples
      FROM radar
      WHERE ts_utc >= ? AND {present_predicate}
      GROUP BY hour_local
      ORDER BY hour_local ASC
      """,
      (cutoff_iso_utc, *present_params),
    ).fetchall()

  buckets = {hour: 0.0 for hour in range(24)}
  for row in rows:
    hour = int(row["hour_local"] or 0)
    if 0 <= hour <= 23:
      buckets[hour] = float(row["present_samples"] or 0) * sample_sec / 60.0
  series = [{"hour": hour, "minutes_present": round(buckets[hour], 2)} for hour in range(24)]
  return {
    "hours": int(hours),
    "sample_seconds": round(sample_sec, 4),
    "series": series,
  }


@APP.get("/api/analytics/eco2_vs_presence")
def api_analytics_eco2_vs_presence(hours: int = Query(24, ge=1, le=168)) -> Dict[str, object]:
  cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
  cutoff_iso_utc = cutoff.isoformat()
  with open_db() as conn:
    ensure_analytics_indexes(conn)
    conn.row_factory = sqlite3.Row
    settings_payload = get_settings_payload(conn)
    mode = radar_presence_mode(settings_payload)
    near_cm = int(settings_payload.get("radar_self_suppress_near_cm") or 80)
    near_cm = max(20, min(200, near_cm))
    if mode == "derived":
      present_case = """
            WHEN (
              SELECT COALESCE(r.alive, 0)
              FROM radar r
              WHERE r.ts_utc <= a.ts_utc
              ORDER BY r.ts_utc DESC
              LIMIT 1
            ) = 1
            AND (
              SELECT COALESCE(r.target, 0)
              FROM radar r
              WHERE r.ts_utc <= a.ts_utc
              ORDER BY r.ts_utc DESC
              LIMIT 1
            ) != 0
            AND (
              SELECT COALESCE(r.detect_cm, 0)
              FROM radar r
              WHERE r.ts_utc <= a.ts_utc
              ORDER BY r.ts_utc DESC
              LIMIT 1
            ) > ?
      """
      query_params = (cutoff_iso_utc, near_cm)
    else:
      present_case = """
            WHEN (
              SELECT COALESCE(r.alive, 0)
              FROM radar r
              WHERE r.ts_utc <= a.ts_utc
              ORDER BY r.ts_utc DESC
              LIMIT 1
            ) = 1
            AND (
              SELECT COALESCE(r.target, 0)
              FROM radar r
              WHERE r.ts_utc <= a.ts_utc
              ORDER BY r.ts_utc DESC
              LIMIT 1
            ) != 0
      """
      query_params = (cutoff_iso_utc,)
    row = conn.execute(
      f"""
      WITH air_window AS (
        SELECT ts_utc, eco2_ppm
        FROM air
        WHERE ts_utc >= ?
      ),
      classified AS (
        SELECT
          a.eco2_ppm AS eco2_ppm,
          CASE
            {present_case}
            THEN 1
            ELSE 0
          END AS present
        FROM air_window a
        WHERE a.eco2_ppm IS NOT NULL
      )
      SELECT
        AVG(CASE WHEN present=1 THEN eco2_ppm END) AS avg_present,
        AVG(CASE WHEN present=0 THEN eco2_ppm END) AS avg_absent,
        SUM(CASE WHEN present=1 THEN 1 ELSE 0 END) AS samples_present,
        SUM(CASE WHEN present=0 THEN 1 ELSE 0 END) AS samples_absent
      FROM classified
      """,
      query_params,
    ).fetchone()

  avg_present = float(row["avg_present"]) if row and row["avg_present"] is not None else None
  avg_absent = float(row["avg_absent"]) if row and row["avg_absent"] is not None else None
  delta = None
  if avg_present is not None and avg_absent is not None:
    delta = avg_present - avg_absent
  return {
    "hours": int(hours),
    "avg_eco2_present": round(avg_present, 2) if avg_present is not None else None,
    "avg_eco2_absent": round(avg_absent, 2) if avg_absent is not None else None,
    "delta_present_minus_absent": round(delta, 2) if delta is not None else None,
    "samples_present": int(row["samples_present"] or 0) if row else 0,
    "samples_absent": int(row["samples_absent"] or 0) if row else 0,
  }


@APP.get("/api/analytics/event_counts")
def api_analytics_event_counts(days: int = Query(7, ge=1, le=90)) -> Dict[str, object]:
  cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
  cutoff_iso_utc = cutoff.isoformat()
  with open_db() as conn:
    ensure_events_table(conn)
    ensure_analytics_indexes(conn)
    conn.row_factory = sqlite3.Row
    sev_rows = conn.execute(
      """
      SELECT severity, COUNT(*) AS count
      FROM events
      WHERE ts_utc >= ?
      GROUP BY severity
      ORDER BY count DESC
      """,
      (cutoff_iso_utc,),
    ).fetchall()
    kind_rows = conn.execute(
      """
      SELECT kind, COUNT(*) AS count
      FROM events
      WHERE ts_utc >= ?
      GROUP BY kind
      ORDER BY count DESC
      LIMIT 5
      """,
      (cutoff_iso_utc,),
    ).fetchall()
  by_severity = {str(row["severity"] or "unknown"): int(row["count"] or 0) for row in sev_rows}
  top_kinds = [{"kind": str(row["kind"] or "unknown"), "count": int(row["count"] or 0)} for row in kind_rows]
  return {
    "days": int(days),
    "by_severity": by_severity,
    "top_kinds": top_kinds,
  }


@APP.post("/api/buzzer/chime")
def api_buzzer_chime(payload: Dict[str, object] = Body(default={})) -> Dict[str, object]:
  pattern = str(payload.get("pattern") or "cal_done").strip().lower()
  if pattern != "cal_done":
    raise HTTPException(status_code=400, detail="unsupported pattern")
  result = trigger_radar_calibration_jingle()
  return {"ok": bool(result.get("ok")), "pattern": pattern, "result": result}


@APP.post("/api/reports/generate")
def api_reports_generate(payload: Dict[str, object] = Body(default={})) -> Dict[str, object]:
  preset = str(payload.get("preset") or "24h")
  start_local = payload.get("start_local")
  end_local = payload.get("end_local")
  include = payload.get("include") if isinstance(payload.get("include"), dict) else {}
  include_presence = bool(include.get("presence", True))
  include_air = bool(include.get("air", True))
  include_events = bool(include.get("events", True))
  include_rssi = bool(include.get("rssi", False))

  range_info = resolve_range_utc_from_request(
    preset=preset,
    start_local=str(start_local) if start_local is not None else None,
    end_local=str(end_local) if end_local is not None else None,
  )

  REPORTS_DIR.mkdir(parents=True, exist_ok=True)

  with open_db() as conn:
    ensure_reports_table(conn)
    ensure_analytics_indexes(conn)
    ensure_events_table(conn)
    conn.row_factory = sqlite3.Row

    options_json = json.dumps(
      {
        "include": {
          "presence": include_presence,
          "air": include_air,
          "events": include_events,
          "rssi": include_rssi,
        }
      },
      separators=(",", ":"),
      ensure_ascii=False,
    )
    cur = conn.execute(
      """
      INSERT INTO reports (
        ts_utc, ts_local, range_start_utc, range_end_utc, preset, options_json, file_path, status, notes
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
      """,
      (
        now_utc_iso(),
        now_local_iso(),
        range_info["start_utc"],
        range_info["end_utc"],
        range_info["preset"],
        options_json,
        "",
        "running",
        "",
      ),
    )
    report_id = int(cur.lastrowid or 0)
    conn.commit()

    settings_payload = get_settings_payload(conn)
    present_predicate, present_params = radar_present_sql_predicate(settings_payload)
    sample_sec = estimate_radar_sample_seconds(conn)
    radar_row = conn.execute(
      f"""
      SELECT
        SUM(CASE WHEN {present_predicate} THEN 1 ELSE 0 END) AS present_samples,
        SUM(CASE WHEN alive=1 THEN 1 ELSE 0 END) AS alive_samples,
        SUM(CASE WHEN alive=1 AND move_en>0 THEN 1 ELSE 0 END) AS moving_samples,
        SUM(CASE WHEN alive=1 AND stat_en>0 THEN 1 ELSE 0 END) AS still_samples
      FROM radar
      WHERE ts_utc >= ? AND ts_utc <= ?
      """,
      (*present_params, range_info["start_utc"], range_info["end_utc"]),
    ).fetchone()

    present_samples = int(radar_row["present_samples"] or 0) if radar_row else 0
    alive_samples = int(radar_row["alive_samples"] or 0) if radar_row else 0
    moving_samples = int(radar_row["moving_samples"] or 0) if radar_row else 0
    still_samples = int(radar_row["still_samples"] or 0) if radar_row else 0
    minutes_present = (present_samples * sample_sec) / 60.0
    total_minutes = max(1e-9, (parse_iso8601_utc(range_info["end_utc"]) - parse_iso8601_utc(range_info["start_utc"])).total_seconds() / 60.0)
    percent_present = (minutes_present / total_minutes) * 100.0
    presence_summary = {
      "minutes_present": round(minutes_present, 2),
      "percent_present": round(percent_present, 2),
      "moving_minutes": round((moving_samples * sample_sec) / 60.0, 2),
      "still_minutes": round((still_samples * sample_sec) / 60.0, 2),
      "alive_samples": alive_samples,
    }

    air_row = conn.execute(
      """
      SELECT
        AVG(a.eco2_ppm) AS avg_eco2,
        MAX(a.eco2_ppm) AS max_eco2,
        AVG(e.temp_c) AS avg_temp,
        AVG(e.hum_pct) AS avg_hum
      FROM air a
      LEFT JOIN env e ON e.ts_utc = (
        SELECT e2.ts_utc FROM env e2 WHERE e2.ts_utc <= a.ts_utc ORDER BY e2.ts_utc DESC LIMIT 1
      )
      WHERE a.ts_utc >= ? AND a.ts_utc <= ?
      """,
      (range_info["start_utc"], range_info["end_utc"]),
    ).fetchone()
    air_summary = {
      "avg_eco2_ppm": round(float(air_row["avg_eco2"]), 2) if air_row and air_row["avg_eco2"] is not None else None,
      "max_eco2_ppm": int(air_row["max_eco2"]) if air_row and air_row["max_eco2"] is not None else None,
      "avg_temp_c": round(float(air_row["avg_temp"]), 2) if air_row and air_row["avg_temp"] is not None else None,
      "avg_hum_pct": round(float(air_row["avg_hum"]), 2) if air_row and air_row["avg_hum"] is not None else None,
    }

    sev_rows = conn.execute(
      "SELECT severity, COUNT(*) AS count FROM events WHERE ts_utc >= ? AND ts_utc <= ? GROUP BY severity",
      (range_info["start_utc"], range_info["end_utc"]),
    ).fetchall()
    by_severity = {str(row["severity"] or "unknown"): int(row["count"] or 0) for row in sev_rows}
    top_events = conn.execute(
      """
      SELECT ts_utc, ts_local, kind, severity, message
      FROM events
      WHERE ts_utc >= ? AND ts_utc <= ?
      ORDER BY id DESC
      LIMIT 10
      """,
      (range_info["start_utc"], range_info["end_utc"]),
    ).fetchall()
    events_summary = {
      "by_severity": by_severity,
      "top_events": [dict(row) for row in top_events],
    }

    rssi_row = conn.execute(
      """
      SELECT AVG(rssi) AS avg_rssi, MIN(rssi) AS min_rssi, MAX(rssi) AS max_rssi
      FROM esp_net
      WHERE ts_utc >= ? AND ts_utc <= ? AND rssi IS NOT NULL AND rssi != ?
      """,
      (range_info["start_utc"], range_info["end_utc"], RSSI_NOT_CONNECTED),
    ).fetchone()
    rssi_summary = {
      "avg_rssi_dbm": round(float(rssi_row["avg_rssi"]), 2) if rssi_row and rssi_row["avg_rssi"] is not None else None,
      "min_rssi_dbm": int(rssi_row["min_rssi"]) if rssi_row and rssi_row["min_rssi"] is not None else None,
      "max_rssi_dbm": int(rssi_row["max_rssi"]) if rssi_row and rssi_row["max_rssi"] is not None else None,
    }

    health = api_health()
    device_summary = {
      "freshness": health.get("freshness", {}),
      "ok": bool(health.get("ok")),
    }

    html_content = build_report_html(
      range_info=range_info,
      include_presence=include_presence,
      include_air=include_air,
      include_events=include_events,
      include_rssi=include_rssi,
      device_summary=device_summary,
      presence_summary=presence_summary,
      air_summary=air_summary,
      events_summary=events_summary,
      rssi_summary=rssi_summary,
    )

    filename = f"report_{report_id}_{int(time.time())}.html"
    out_path = REPORTS_DIR / filename
    out_path.write_text(html_content, encoding="utf-8")

    conn.execute(
      "UPDATE reports SET file_path=?, status=?, notes=? WHERE id=?",
      (str(out_path), "done", "", report_id),
    )
    conn.commit()

  return {
    "report_id": report_id,
    "status": "done",
    "download_url": f"/api/reports/{report_id}/download",
  }


@APP.get("/api/reports")
def api_reports_list(limit: int = Query(10, ge=1, le=50)) -> Dict[str, object]:
  with open_db() as conn:
    ensure_reports_table(conn)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
      """
      SELECT id, ts_utc, ts_local, range_start_utc, range_end_utc, preset, options_json, file_path, status, notes
      FROM reports
      ORDER BY id DESC
      LIMIT ?
      """,
      (limit,),
    ).fetchall()
  out = []
  for row in rows:
    item = dict(row)
    item["download_url"] = f"/api/reports/{item['id']}/download"
    out.append(item)
  return {"rows": out}


@APP.get("/api/reports/{report_id}/download")
def api_reports_download(report_id: int) -> FileResponse:
  with open_db() as conn:
    ensure_reports_table(conn)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
      "SELECT id, file_path, status FROM reports WHERE id=?",
      (int(report_id),),
    ).fetchone()
  if not row:
    raise HTTPException(status_code=404, detail="report not found")
  if str(row["status"] or "") != "done":
    raise HTTPException(status_code=409, detail="report not ready")
  file_path = Path(str(row["file_path"] or ""))
  if not file_path.exists() or not file_path.is_file():
    raise HTTPException(status_code=404, detail="report file missing")
  return FileResponse(path=str(file_path), media_type="text/html", filename=file_path.name)


@APP.get("/api/history")
def api_history(
    kind: str = Query("radar"),
    range: str = Query("24h"),
    limit: int = Query(500, ge=1, le=5000),
    q: str = Query(""),
    start_local: str = Query(""),
    end_local: str = Query(""),
) -> Dict[str, object]:
  table_by_kind = {
    "radar": "radar",
    "events": "events",
    "env": "env",
  }
  kind_norm = str(kind or "radar").strip().lower()
  if kind_norm not in table_by_kind:
    raise HTTPException(status_code=400, detail="kind must be one of radar, events, env")

  range_info = resolve_range_utc_from_request(
    preset=str(range or "24h"),
    start_local=start_local or None,
    end_local=end_local or None,
  )

  table = table_by_kind[kind_norm]
  with open_db() as conn:
    conn.row_factory = sqlite3.Row
    where = ["ts_utc >= ?", "ts_utc <= ?"]
    params: List[object] = [range_info["start_utc"], range_info["end_utc"]]
    q_norm = str(q or "").strip()
    if q_norm:
      like = f"%{q_norm}%"
      if table == "events":
        where.append("(message LIKE ? OR kind LIKE ? OR source LIKE ?)")
        params.extend([like, like, like])
      elif table == "radar":
        where.append("(CAST(target AS TEXT) LIKE ? OR CAST(detect_cm AS TEXT) LIKE ? OR CAST(move_en AS TEXT) LIKE ? OR CAST(stat_en AS TEXT) LIKE ?)")
        params.extend([like, like, like, like])
      else:
        where.append("(CAST(temp_c AS TEXT) LIKE ? OR CAST(hum_pct AS TEXT) LIKE ?)")
        params.extend([like, like])

    sql = f"SELECT * FROM {table} WHERE " + " AND ".join(where) + " ORDER BY id DESC LIMIT ?"
    rows = conn.execute(sql, (*params, int(limit))).fetchall()
  return {
    "kind": kind_norm,
    "range": range_info,
    "limit": int(limit),
    "rows": [dict(row) for row in rows],
  }


@APP.get("/api/history/export.csv")
def api_history_export_csv(
    kind: str = Query("radar"),
    range: str = Query("24h"),
    limit: int = Query(500, ge=1, le=5000),
    q: str = Query(""),
    start_local: str = Query(""),
    end_local: str = Query(""),
) -> Response:
  payload = api_history(kind=kind, range=range, limit=limit, q=q, start_local=start_local, end_local=end_local)
  rows = payload.get("rows", []) or []
  output = io.StringIO()
  if rows:
    fieldnames = list(rows[0].keys())
  else:
    fieldnames = ["id", "ts_utc"]
  writer = csv.DictWriter(output, fieldnames=fieldnames)
  writer.writeheader()
  for row in rows:
    writer.writerow({k: row.get(k) for k in fieldnames})
  csv_text = output.getvalue()
  filename = f"history_{payload.get('kind', 'data')}_{int(time.time())}.csv"
  headers = {"Content-Disposition": f"attachment; filename={filename}"}
  return Response(content=csv_text, media_type="text/csv", headers=headers)


HTML_PAGE = """
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>HERMES Dashboard</title>
  <style>
    body {
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji";
      font-weight: 450;
      letter-spacing: 0.2px;
      margin: 16px;
      background: #0b0f14;
      color: #e8eef5;
    }
    h1 { margin: 0 0 4px 98px; }
    .top-nav { margin: 10px 0 14px 98px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .brand-link {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      position: fixed;
      top: 16px;
      left: 16px;
      width: 72px;
      height: 72px;
      text-decoration: none;
      background: #111820;
      border: 1px solid #26313d;
      border-radius: 12px;
      z-index: 20;
    }
    .brand-logo { display: block; height: 48px; width: auto; border-radius: 3px; }
    .nav-link {
      display: inline-flex;
      align-items: center;
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid #26313d;
      background: #111820;
      color: #9fb3c8;
      font-size: 12px;
      text-decoration: none;
    }
    .nav-link.active { background: #1f5f99; border-color: #1f5f99; color: #fff; }
    .nav-ticker { margin-left: auto; max-width: 620px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 12px; opacity: 0.9; padding: 6px 10px; border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; }
    .ticker-dot { display:inline-block; width:8px; height:8px; border-radius:50%; background: rgba(120,180,255,0.9); margin-right: 8px; vertical-align: middle; }
    .home-top-grid { display: grid; grid-template-columns: 1fr 420px; gap: 14px; align-items: start; }
    .section-title { font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; opacity: 0.8; margin: 6px 0 10px; }
    .sys-cards { display: grid; grid-template-columns: repeat(5, minmax(160px, 1fr)); gap: 12px; }
    .card-compact { padding: 6px 8px; min-height: 70px; }
    .card-compact .card-title { font-size: 12px; opacity: 0.85; margin-bottom: 6px; }
    .card-compact .card-value { font-size: 22px; font-weight: 700; }
    .card-compact .card-sub { font-size: 12px; opacity: 0.75; margin-top: 4px; }
    .health-side { position: sticky; top: 16px; }
    .stream-row { display: grid; grid-template-columns: repeat(7, minmax(120px, 1fr)); gap: 10px; }
    .stream-row .tile { width: auto; }
    .stream-row .card { padding: 8px 10px; }
    .field-entry { margin: 6px 0 12px 0; }
    .field-entry a {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      text-decoration: none;
      background: #1f5f99;
      color: #fff;
      border-radius: 999px;
      padding: 9px 14px;
      border: 1px solid #1f5f99;
      font-size: 14px;
      font-weight: 650;
    }
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
    @media (max-width: 1400px) {
      .sys-cards { grid-template-columns: repeat(3, minmax(160px, 1fr)); }
    }
    @media (max-width: 1100px) {
      .home-top-grid { grid-template-columns: 1fr; }
      .stream-row { grid-template-columns: repeat(3, minmax(120px, 1fr)); }
      .trend-window { width: 100%; max-width: 100%; }
      .health-side .card { min-height: 0; }
      #integrityFps { min-height: 0; }
    }
    @media (max-width: 900px) {
      .sys-cards { grid-template-columns: repeat(2, minmax(160px, 1fr)); }
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
    .trend-card { min-width: 260px; flex: 1 1 320px; display: flex; flex-direction: column; gap: 0.5rem; }
    .card.chart { height: auto; }
    .trend-card.chart { height: auto; }
    @media (min-width: 1400px) {
      .trend-card.chart { height: auto; }
    }
    .metric-value, .card h2 { font-weight: 650; }
    .trend-value { font-size: 1.125rem; line-height: 1.2; font-weight: 650; margin: 0.2rem 0 0.35rem 0; }
    .card.chart .card-header { padding: 12px 14px 8px 14px; margin: -10px -12px 0 -12px; }
    .chart-wrap { flex: 1 1 auto; width: 100%; }
    .card.chart .plot { position: relative; width: 100%; aspect-ratio: 16 / 9; min-height: 240px; max-height: 380px; }
    .card.chart canvas, .card.chart svg { height: 100% !important; width: 100% !important; }
    .trend-img { width: 100%; height: 100%; border-radius: 8px; border: 1px solid #26313d; display: block; }
    .trend-top { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
    .trend-badges { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
    .pill-row { gap: 6px; }
    .badge, .pill { font-size: 12px; color: #9fb3c8; border: 1px solid #26313d; padding: 3px 8px; border-radius: 999px; background: #111820; }
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
    .hp-card { min-width: 320px; }
    .hp-head { align-items: center; }
    .hp-tabs { display: flex; gap: 8px; align-items: center; margin-left: auto; }
    .hp-tab { flex: 0 0 auto; padding: 6px 10px; border-radius: 999px; white-space: nowrap; background: #111820; color: #9fb3c8; border: 1px solid #26313d; cursor: pointer; }
    .hp-tab.active { background: #1f5f99; color: #fff; border-color: #1f5f99; }
    .hp-badges { justify-content: flex-start; }
    .radar-now-wrap { margin-top: 4px; display: flex; flex-direction: column; gap: 8px; }
    .range-strip { position: relative; padding: 14px 8px 22px 8px; border-radius: 8px; border: 1px solid #26313d; background: #0f1620; }
    .range-track { height: 8px; border-radius: 999px; background: #0b121b; border: 1px solid #1d2a38; }
    .marker { position: absolute; pointer-events: none; }
    .marker.hidden { display: none; }
    .marker.detect { top: 8px; width: 14px; height: 14px; border-radius: 999px; background: rgba(132, 210, 255, 0.95); box-shadow: 0 0 0 4px rgba(76, 153, 220, 0.18); }
    .marker.detect.pulse-fast { animation: detectPulseFast 0.95s infinite ease-in-out; }
    .marker.detect.pulse-slow { animation: detectPulseSlow 1.8s infinite ease-in-out; }
    .marker.move { top: 2px; width: 0; height: 0; border-left: 6px solid transparent; border-right: 6px solid transparent; border-bottom: 9px solid rgba(140, 210, 255, 0.95); }
    .marker.stat { top: 22px; width: 0; height: 0; border-left: 6px solid transparent; border-right: 6px solid transparent; border-top: 9px solid rgba(62, 112, 182, 0.98); }
    .range-labels { margin-top: 8px; display: flex; justify-content: space-between; font-size: 11px; color: #8ea1b3; }
    .conf-row { display: grid; grid-template-columns: 95px 1fr 52px; align-items: center; gap: 8px; }
    .conf-bar { height: 8px; border-radius: 999px; background: #0b121b; border: 1px solid #1d2a38; overflow: hidden; }
    .conf-fill { height: 100%; width: 0%; }
    .conf-fill.move { background: linear-gradient(90deg, #69b8ff, #9dd9ff); }
    .conf-fill.stat { background: linear-gradient(90deg, #2f5f9b, #5b82be); }
    @keyframes detectPulseFast {
      0%, 100% { transform: scale(1.0); box-shadow: 0 0 0 3px rgba(76, 153, 220, 0.14); }
      50% { transform: scale(1.16); box-shadow: 0 0 0 8px rgba(76, 153, 220, 0.20); }
    }
    @keyframes detectPulseSlow {
      0%, 100% { transform: scale(1.0); box-shadow: 0 0 0 3px rgba(54, 110, 182, 0.14); }
      50% { transform: scale(1.10); box-shadow: 0 0 0 6px rgba(54, 110, 182, 0.20); }
    }
    .radar-readout { margin-top: 8px; font-size: 12px; color: #b8c7d8; line-height: 1.6; }
    .radar-line { display: flex; justify-content: space-between; gap: 10px; }
    .radar-label { color: #8ea1b3; }
    .radar-state { margin-bottom: 6px; font-weight: 700; color: #d8e6f4; }
    .chart-slot-controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
    .trend-window { width: calc(100% - 434px); max-width: calc(100% - 434px); display: flex; flex-direction: column; padding: 6px 8px; }
    .trend-head { display:flex; align-items:flex-end; justify-content:space-between; gap:12px; }
    .trend-range-pills { display: inline-flex; }
    .trend-slots { display:flex; gap:12px; flex-wrap:wrap; margin-top: 10px; }
    .trend-actions { margin-top: 10px; }
    .slot-ctrl { display: inline-flex; align-items: center; gap: 6px; }
    .slot-ctrl label { font-size: 12px; color: #9fb3c8; }
    .slot-ctrl select { background: #111820; color: #d9e6f3; border: 1px solid #26313d; border-radius: 8px; padding: 5px 8px; }
    .health-side .card { min-height: 280px; }
    #integrityFps { min-height: 210px; }
    .btn-diagnostics { padding: 6px 10px; font-size: 12px; }
    .rssi-box { border: 2px solid rgba(255,255,255,0.08); }
    .rssi-good { border-color: rgba(0, 200, 100, 0.70); box-shadow: 0 0 0 1px rgba(0,200,100,0.15) inset; }
    .rssi-med  { border-color: rgba(255, 180, 0, 0.70); box-shadow: 0 0 0 1px rgba(255,180,0,0.12) inset; }
    .rssi-poor { border-color: rgba(255, 70, 70, 0.75); box-shadow: 0 0 0 1px rgba(255,70,70,0.12) inset; }
    .status-pill { display: inline-flex; align-items: center; padding: 3px 10px; border-radius: 999px; border: 1px solid #2a3b4f; background: #111820; color: #c3d4e6; font-size: 11px; font-weight: 650; }
    .fresh-age { margin-top: 4px; font-size: 11px; color: #9fb3c8; }
    .fresh-age.good { color: #7ed48b; }
    .fresh-age.warn { color: #f0c36d; }
    .fresh-age.bad { color: #f08f8f; }
    .integrity-table { width: 100%; border-collapse: collapse; margin-top: 6px; }
    .integrity-table td { padding: 2px 4px; font-size: 12px; border-bottom: 1px solid #22303d; }
    .integrity-table td:last-child { text-align: right; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .state-offline { border-color: #5a6775; color: #bdc8d4; }
    .state-none { border-color: #2f5f9b; color: #a7c4e5; }
    .state-moving { border-color: #4ca9ff; color: #c8e6ff; }
    .state-still { border-color: #3e6fad; color: #b7d0ea; }
    .state-both { border-color: #66bb6a; color: #d2f0d5; }
    .cal-btn { margin-left: 8px; padding: 5px 9px; font-size: 11px; border-radius: 999px; }
    .cal-panel { border: 1px solid #2a3440; border-radius: 8px; background: #101722; padding: 10px; font-size: 12px; }
    .cal-instruction { color: #c6d5e7; margin-bottom: 8px; }
    .cal-guidance { color: #9fb3c8; font-size: 11px; margin-top: 6px; }
    .cal-counters { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-bottom: 8px; }
    .cal-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
    .cal-history { margin-top: 8px; color: #9fb3c8; font-size: 11px; max-height: 100px; overflow: auto; }
  </style>
</head>
<body>
  <h1>HERMES Dashboard</h1>
  {{TOP_NAV}}
  <div id="fieldModeEntry" class="field-entry hidden"><a href="/field">Enter Field Mode</a></div>
  <div id="readyBanner" class="banner hidden">NOT READY</div>
  <div id="lastUpdated" class="muted small">Last updated: never</div>
  <div id="dbg" class="muted small" style="margin-top:6px;">
    dbg: <span id="dbg-js">booting</span> |
    origin: <span id="dbg-origin">?</span> |
    last fetch: <span id="dbg-fetch">none</span>
  </div>
  <div class="home-top-grid">
    <div class="home-top-main">
      <div class="section-title">System Health</div>
      <div class="sys-cards">
        <div class="card status card-compact">
          <div class="card-title">Daemon</div>
          <div class="card-value" id="daemon">loading...</div>
        </div>
        <div class="card status card-compact">
          <div class="card-title">Port</div>
          <div class="card-value" id="port">-</div>
        </div>
        <div class="card status card-compact">
          <div class="card-title">Lines In</div>
          <div class="card-value" id="lines">-</div>
        </div>
        <div class="card status card-compact">
          <div class="card-title">Last Error</div>
          <div class="card-value" id="error">-</div>
        </div>
        <div id="rssiBox" class="card status rssi-box card-compact">
          <div class="card-title">RSSI</div>
          <div class="card-value" id="top-rssi">--</div>
        </div>
      </div>
      <div style="height: 10px"></div>
      <div class="section-title">Telemetry Streams</div>
      <div id="freshness" class="stream-row"></div>
    </div>
    <div class="home-top-side health-side">
      <div class="section-title">Telemetry Integrity</div>
      <div class="card">
        <div class="card-title"><span class="ticker-dot" id="integrityDot"></span>Telemetry Integrity</div>
        <div id="integrityMeta" class="muted small" style="margin-top:6px">loading...</div>
        <div id="integrityFps"></div>
      </div>
    </div>
  </div>

  {{HOME_CHARTS_SECTION}}

  {{EVENTS_DATA_SECTION}}

<script src="/app.js"></script>
</body>
</html>
"""


def render_top_nav(active_path: str) -> str:
  links: List[str] = []
  for label, href in NAV_LINKS:
    is_active = href == active_path
    cls = "nav-link active" if is_active else "nav-link"
    links.append(f'<a href="{href}" class="{cls}">{label}</a>')
  brand = '<a href="/" class="brand-link" aria-label="HERMES Home"><img src="/static/hermes-logo-h.jpg" class="brand-logo" alt="HERMES logo" /></a>'
  ticker = ""
  if active_path == "/":
    ticker = '<div class="nav-ticker" id="navTicker"><span class="ticker-dot" id="tickerDot"></span><span class="ticker-text" id="tickerText">Loading transitions…</span></div>'
  return '<nav class="top-nav" aria-label="Primary">' + brand + "".join(links) + ticker + "</nav>"


def render_dashboard_page(active_path: str) -> str:
  home_charts_section = """
  <div class=\"row\">
    <div class=\"card trend-window\">
      <div class="trend-head">
        <div>
          <div class="card-title">Trend window</div>
          <div class="card-sub muted small">Affects sparklines and badges</div>
        </div>
        <div class="trend-range-pills">
          <div class=\"seg\">
            <button id=\"win-5\" onclick=\"setTrendMinutes(5)\">5m</button>
            <button id=\"win-60\" onclick=\"setTrendMinutes(60)\">60m</button>
            <button id=\"win-240\" onclick=\"setTrendMinutes(240)\">4h</button>
          </div>
        </div>
      </div>
      <div class="trend-slots">
        <div id=\"chartSlotControls\" class=\"chart-slot-controls\"></div>
      </div>
    </div>
  </div>

  <div class="section-title">Human Presences & Trends</div>
  <div class=\"row\" id=\"trends\"></div>
  """

  events_data_section = """
  <div class=\"row\">
    <div class=\"card events-card\">
      <div style=\"display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap;\">
        <div>
          <b>Events</b>
          <span class=\"muted small\">(last 50)</span>
          <div id="events-last" class="event-summary">Last event: loading...</div>
        </div>
        <div style=\"display:flex; align-items:center; gap:8px;\">
          <button class=\"btn-diagnostics\" onclick=\"downloadDiag()\">Download diagnostics</button>
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
  """

  show_home_charts = active_path == "/"
  show_events_data = active_path == "/events"
  return (
    HTML_PAGE
    .replace("{{TOP_NAV}}", render_top_nav(active_path))
    .replace("{{HOME_CHARTS_SECTION}}", home_charts_section if show_home_charts else "")
    .replace("{{EVENTS_DATA_SECTION}}", events_data_section if show_events_data else "")
  )


def render_shell_page(active_path: str, title: str, body_html: str, script_js: str, extra_style: str = "") -> str:
  nav = render_top_nav(active_path)
  return f"""
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{title}</title>
  <style>
    body {{
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, \"Segoe UI\", Roboto, Helvetica, Arial;
      margin: 16px;
      background: #0b0f14;
      color: #e8eef5;
    }}
    h1 {{ margin: 0 0 4px 98px; }}
    .muted {{ color: #9fb3c8; }}
    .top-nav {{ margin: 10px 0 14px 98px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
    .brand-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      position: fixed;
      top: 16px;
      left: 16px;
      width: 72px;
      height: 72px;
      text-decoration: none;
      background: #111820;
      border: 1px solid #26313d;
      border-radius: 12px;
      z-index: 20;
    }}
    .brand-logo {{ display: block; height: 48px; width: auto; border-radius: 3px; }}
    .nav-link {{
      display: inline-flex;
      align-items: center;
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid #26313d;
      background: #111820;
      color: #9fb3c8;
      font-size: 12px;
      text-decoration: none;
    }}
    .nav-link.active {{ background: #1f5f99; border-color: #1f5f99; color: #fff; }}
    .card {{ background: #151c24; border: 1px solid #26313d; border-radius: 10px; padding: 12px; margin-bottom: 12px; }}
    button {{ background: #1f5f99; color: #fff; border: 0; border-radius: 8px; padding: 8px 12px; cursor: pointer; }}
    input, select {{ background: #111820; color: #d9e6f3; border: 1px solid #26313d; border-radius: 8px; padding: 7px 9px; }}
    {extra_style}
  </style>
</head>
<body>
  <h1>{title}</h1>
  {nav}
  {body_html}
  <script>
  {script_js}
  </script>
</body>
</html>
"""


def render_analytics_page() -> str:
  body = """
  <div class=\"card\"><button onclick=\"window.location.href='/reports'\">Report</button></div>
  <div class=\"card\">
    <b>Presence Minutes by Hour (last 24h)</b>
    <div class=\"muted\" style=\"margin-top:4px\">Estimated minutes with alive presence by local hour.</div>
    <div id=\"presenceByHour\" style=\"margin-top:10px\"></div>
  </div>
  <div class=\"card\">
    <b>ECO2 vs Presence (last 24h)</b>
    <div id=\"eco2Corr\" style=\"display:grid;grid-template-columns:repeat(3,minmax(160px,1fr));gap:10px;margin-top:10px\"></div>
  </div>
  <div class=\"card\">
    <b>Top Events (last 7d)</b>
    <div id=\"eventSeverity\" class=\"muted\" style=\"margin-top:8px\"></div>
    <div id=\"eventKinds\" style=\"margin-top:10px\"></div>
  </div>
  """
  script = """
  function barRow(label, value, maxValue) {
    const safeMax = Math.max(1, Number(maxValue || 1));
    const width = Math.max(0, Math.min(100, (Number(value || 0) / safeMax) * 100));
    return '<div style="display:grid;grid-template-columns:52px 1fr 58px;gap:8px;align-items:center;margin:4px 0">'
      + '<span class="muted">' + String(label).padStart(2, '0') + ':00</span>'
      + '<div style="height:10px;border:1px solid #26313d;border-radius:999px;background:#111820;overflow:hidden"><div style="height:100%;width:' + width.toFixed(1) + '%;background:#4ca9ff"></div></div>'
      + '<span>' + Number(value || 0).toFixed(1) + 'm</span>'
      + '</div>';
  }

  async function fetchJson(url) {
    const r = await fetch(url, { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + url);
    return await r.json();
  }

  async function loadAnalytics() {
    const [presence, eco2, events] = await Promise.all([
      fetchJson('/api/analytics/presence_by_hour?hours=24'),
      fetchJson('/api/analytics/eco2_vs_presence?hours=24'),
      fetchJson('/api/analytics/event_counts?days=7'),
    ]);

    const series = Array.isArray(presence.series) ? presence.series : [];
    const maxM = series.reduce((m, row) => Math.max(m, Number(row.minutes_present || 0)), 0);
    const byHour = document.getElementById('presenceByHour');
    if (byHour) {
      byHour.innerHTML = series.map((row) => barRow(row.hour, row.minutes_present, maxM)).join('');
    }

    const corr = document.getElementById('eco2Corr');
    if (corr) {
      const present = eco2.avg_eco2_present == null ? 'n/a' : Number(eco2.avg_eco2_present).toFixed(1) + ' ppm';
      const absent = eco2.avg_eco2_absent == null ? 'n/a' : Number(eco2.avg_eco2_absent).toFixed(1) + ' ppm';
      const delta = eco2.delta_present_minus_absent == null ? 'n/a' : Number(eco2.delta_present_minus_absent).toFixed(1) + ' ppm';
      corr.innerHTML =
        '<div class="card" style="margin:0"><div class="muted">Avg ECO2 (presence)</div><div style="font-size:28px;font-weight:700">' + present + '</div></div>' +
        '<div class="card" style="margin:0"><div class="muted">Avg ECO2 (no presence)</div><div style="font-size:28px;font-weight:700">' + absent + '</div></div>' +
        '<div class="card" style="margin:0"><div class="muted">Delta (present - absent)</div><div style="font-size:28px;font-weight:700">' + delta + '</div></div>';
    }

    const sev = document.getElementById('eventSeverity');
    if (sev) {
      const bySev = events.by_severity || {};
      const parts = Object.keys(bySev).sort().map((k) => k + ': ' + bySev[k]);
      sev.textContent = parts.length ? ('Severity counts — ' + parts.join(' · ')) : 'No events in window.';
    }

    const kinds = document.getElementById('eventKinds');
    if (kinds) {
      const top = Array.isArray(events.top_kinds) ? events.top_kinds : [];
      kinds.innerHTML = top.map((row, idx) =>
        '<div style="display:flex;justify-content:space-between;border-bottom:1px solid #26313d;padding:6px 0">'
        + '<span>' + String(idx + 1) + '. ' + String(row.kind || 'unknown') + '</span>'
        + '<b>' + String(row.count || 0) + '</b>'
        + '</div>'
      ).join('');
    }
  }

  loadAnalytics().catch((err) => {
    const roots = ['presenceByHour', 'eco2Corr', 'eventKinds'];
    for (const id of roots) {
      const el = document.getElementById(id);
      if (el) el.innerHTML = '<div class="muted">Failed to load analytics: ' + String(err && err.message ? err.message : err) + '</div>';
    }
  });
  """
  return render_shell_page("/analytics", "HERMES Analytics", body, script)


def render_field_page() -> str:
  body = """
  <div class=\"card\" style=\"padding:14px\">
    <div id=\"fieldStatus\" class=\"status-pill state-offline\">RADAR OFFLINE</div>
    <div style=\"margin-top:8px;display:flex;align-items:center;gap:10px\">
      <label class=\"muted\" style=\"display:flex;align-items:center;gap:6px\"><input id=\"fieldUseDerived\" type=\"checkbox\" /> Use derived presence</label>
      <span id=\"fieldSelfSuppressed\" class=\"muted\" style=\"font-size:12px\"></span>
    </div>
    <div style=\"margin-top:12px\">
      <div class=\"muted\">Detect Distance</div>
      <div id=\"fieldDetect\" style=\"font-size:64px;line-height:1.05;font-weight:750\">--</div>
    </div>
  </div>
  <div class=\"tile-grid\">
    <div class=\"card tile\"><div class=\"muted\">ECO2</div><div id=\"tileEco2\" class=\"tile-val\">--</div></div>
    <div class=\"card tile\"><div class=\"muted\">Temp</div><div id=\"tileTemp\" class=\"tile-val\">--</div></div>
    <div class=\"card tile\"><div class=\"muted\">Humidity</div><div id=\"tileHum\" class=\"tile-val\">--</div></div>
    <div id=\"tileRssiWrap\" class=\"card tile rssi-box\"><div class=\"muted\">RSSI</div><div id=\"tileRssi\" class=\"tile-val\">--</div></div>
  </div>
  <div class=\"muted\" id=\"fieldFooter\" style=\"margin-top:10px\">Last updated: never · System: --</div>
  """
  style = """
    .tile-grid { display:grid; grid-template-columns: repeat(4,minmax(180px,1fr)); gap:10px; }
    @media (max-width: 1100px) { .tile-grid { grid-template-columns: repeat(2,minmax(180px,1fr)); } }
    .tile { min-height: 120px; }
    .tile-val { font-size: 42px; font-weight: 750; margin-top: 8px; }
    .status-pill { display:inline-flex; align-items:center; padding:8px 16px; border-radius:999px; border:1px solid #2a3b4f; background:#111820; color:#c3d4e6; font-size:24px; font-weight:700; }
    .state-offline { border-color:#5a6775; color:#bdc8d4; }
    .state-none { border-color:#2f5f9b; color:#a7c4e5; }
    .state-moving { border-color:#4ca9ff; color:#c8e6ff; }
    .state-still { border-color:#3e6fad; color:#b7d0ea; }
    .state-both { border-color:#66bb6a; color:#d2f0d5; }
    .rssi-box { border: 2px solid rgba(255,255,255,0.08); }
    .rssi-good { border-color: rgba(0, 200, 100, 0.70); box-shadow: 0 0 0 1px rgba(0,200,100,0.15) inset; }
    .rssi-med  { border-color: rgba(255, 180, 0, 0.70); box-shadow: 0 0 0 1px rgba(255,180,0,0.12) inset; }
    .rssi-poor { border-color: rgba(255, 70, 70, 0.75); box-shadow: 0 0 0 1px rgba(255,70,70,0.12) inset; }
  """
  script = """
  let unitDistance = 'cm';
  let useDerivedPresence = false;

  function fmtDetect(cm) {
    const value = Number(cm || 0);
    if (!Number.isFinite(value)) return '--';
    if (unitDistance === 'm') return (value / 100).toFixed(2) + ' m';
    return Math.round(value) + ' cm';
  }

  function toPresenceStatus(radar, useDerived) {
    const alive = Number(radar.alive || 0) === 1;
    if (!alive) return { text: 'RADAR OFFLINE', cls: 'state-offline' };
    const hasDerived = Object.prototype.hasOwnProperty.call(radar || {}, 'present_derived');
    if (useDerived && hasDerived && !Boolean(radar.present_derived)) {
      return { text: 'NO PRESENCE', cls: 'state-none' };
    }
    const target = Number(radar.target || 0);
    const moving = Number(radar.move_en || 0) > 0;
    const still = Number(radar.stat_en || 0) > 0;
    if (target === 0) return { text: 'NO PRESENCE', cls: 'state-none' };
    if (moving && still) return { text: 'MOVING + STILL', cls: 'state-both' };
    if (moving && !still) return { text: 'MOVING PRESENCE', cls: 'state-moving' };
    if (!moving && still) return { text: 'STILL PRESENCE', cls: 'state-still' };
    return { text: 'NO PRESENCE', cls: 'state-none' };
  }

  function rssiClass(rssi, wifist) {
    if (!Number.isFinite(rssi) || rssi === 999 || rssi > 0 || rssi < -130) return 'rssi-poor';
    if (Number.isFinite(wifist) && wifist !== 1 && wifist !== 3) return 'rssi-poor';
    if (rssi >= -65) return 'rssi-good';
    if (rssi >= -80) return 'rssi-med';
    return 'rssi-poor';
  }

  async function fetchJson(url) {
    const r = await fetch(url, { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + url);
    return await r.json();
  }

  async function loadSettings() {
    try {
      const settings = await fetchJson('/api/settings');
      unitDistance = String(settings.units_distance || 'cm') === 'm' ? 'm' : 'cm';
      useDerivedPresence = !!settings.radar_self_suppress_enabled;
    } catch (_err) {
      unitDistance = 'cm';
      useDerivedPresence = false;
    }
  }

  async function refreshField() {
    const [radarResp, airResp, envResp, espResp, healthResp] = await Promise.all([
      fetchJson('/api/radar/latest'),
      fetchJson('/api/latest/air?limit=1'),
      fetchJson('/api/latest/env?limit=1'),
      fetchJson('/api/latest/esp_net?limit=1'),
      fetchJson('/api/health'),
    ]);

    const radar = radarResp || {};
    const air = (airResp.rows || [])[0] || {};
    const env = (envResp.rows || [])[0] || {};
    const esp = (espResp.rows || [])[0] || {};
    const freshness = (healthResp && healthResp.freshness) ? healthResp.freshness : {};

    const useDerived = !!document.getElementById('fieldUseDerived')?.checked;
    useDerivedPresence = useDerived;
    const st = toPresenceStatus(radar, useDerived);
    const statusEl = document.getElementById('fieldStatus');
    if (statusEl) {
      statusEl.textContent = st.text;
      statusEl.className = 'status-pill ' + st.cls;
    }
    const selfNoteEl = document.getElementById('fieldSelfSuppressed');
    if (selfNoteEl) {
      const show = useDerived && !!radar.self_suppressed;
      selfNoteEl.textContent = show ? 'Self suppressed' : '';
    }

    const detectEl = document.getElementById('fieldDetect');
    if (detectEl) detectEl.textContent = fmtDetect(radar.detect_cm);

    const eco2El = document.getElementById('tileEco2');
    if (eco2El) eco2El.textContent = (air.eco2_ppm == null ? '--' : (Math.round(Number(air.eco2_ppm)) + ' ppm'));
    const tempEl = document.getElementById('tileTemp');
    if (tempEl) tempEl.textContent = (env.temp_c == null ? '--' : (Number(env.temp_c).toFixed(1) + '°C'));
    const humEl = document.getElementById('tileHum');
    if (humEl) humEl.textContent = (env.hum_pct == null ? '--' : (Number(env.hum_pct).toFixed(1) + '%'));

    const rssiVal = Number(esp.rssi);
    const wifist = Number(esp.wifist);
    const rssiEl = document.getElementById('tileRssi');
    if (rssiEl) {
      if (!Number.isFinite(rssiVal) || rssiVal === 999 || rssiVal > 0 || rssiVal < -130) rssiEl.textContent = 'n/a';
      else if (wifist !== 1 && wifist !== 3) rssiEl.textContent = 'offline';
      else rssiEl.textContent = Math.round(rssiVal) + ' dBm';
    }
    const rssiWrap = document.getElementById('tileRssiWrap');
    if (rssiWrap) {
      rssiWrap.classList.remove('rssi-good', 'rssi-med', 'rssi-poor');
      rssiWrap.classList.add(rssiClass(rssiVal, wifist));
    }

    const footer = document.getElementById('fieldFooter');
    if (footer) {
      const nowIso = new Date().toLocaleTimeString();
      const deadKeys = Object.keys(freshness).filter((k) => String(freshness[k]) !== 'ok');
      const sys = deadKeys.length ? ('DEGRADED (' + deadKeys.join(', ') + ')') : 'OK';
      footer.textContent = 'Last updated: ' + nowIso + ' · System: ' + sys;
    }
  }

  (async () => {
    await loadSettings();
    const derivedEl = document.getElementById('fieldUseDerived');
    if (derivedEl) {
      derivedEl.checked = !!useDerivedPresence;
      derivedEl.addEventListener('change', () => {
        useDerivedPresence = !!derivedEl.checked;
        refreshField().catch(() => {});
      });
    }
    await refreshField();
    setInterval(refreshField, 1000);
  })();
  """
  return render_shell_page("/field", "HERMES Field Mode", body, script, extra_style=style)


def render_settings_page() -> str:
  body = """
  <div class=\"card\">
    <div style=\"display:grid;grid-template-columns:repeat(2,minmax(260px,1fr));gap:12px\">
      <label style=\"display:flex;align-items:center;gap:8px\"><input id=\"fieldModeStart\" type=\"checkbox\" /> Field mode quick-entry on Home</label>
      <label>Distance units
        <select id=\"unitsDistance\" style=\"margin-left:8px\"><option value=\"cm\">cm</option><option value=\"m\">m</option></select>
      </label>
    </div>
  </div>
  <div class=\"card\">
    <b>Home chart slots</b>
    <div class=\"muted\" style=\"margin-top:4px\">Persisted immediately and used on Home load.</div>
    <div style=\"display:grid;grid-template-columns:repeat(2,minmax(260px,1fr));gap:12px;margin-top:10px\">
      <label>Slot A <select id=\"slotA\"></select></label>
      <label>Slot B <select id=\"slotB\"></select></label>
      <label>Slot C <select id=\"slotC\"></select></label>
      <label>Slot D <select id=\"slotD\"></select></label>
    </div>
    <div style=\"margin-top:12px;display:flex;gap:8px\">
      <button id=\"saveBtn\">Save</button>
      <button id=\"resetBtn\">Reset to defaults</button>
      <span id=\"saveMsg\" class=\"muted\"></span>
    </div>
  </div>
  <div class=\"card\">
    <b>Chime assignments</b>
    <div class=\"muted\" style=\"margin-top:4px\">Assign a melody to each event type.</div>
    <div style=\"display:grid;grid-template-columns:repeat(2,minmax(260px,1fr));gap:12px;margin-top:10px\">
      <label>Startup
        <div class="muted" style="margin-top:2px">Plays when dashboard restart/startup is detected.</div>
        <div style="display:flex;gap:8px;align-items:center;margin-top:4px">
          <select id="chimeStartup" style="flex:1"></select>
          <button id="previewStartup" type="button">Preview</button>
        </div>
      </label>
      <label>Air spike
        <div style=\"display:flex;gap:8px;align-items:center;margin-top:4px\">
          <select id=\"chimeAirSpike\" style=\"flex:1\"></select>
          <button id=\"previewAirSpike\" type=\"button\">Preview</button>
        </div>
      </label>
      <label>WiFi drop
        <div style=\"display:flex;gap:8px;align-items:center;margin-top:4px\">
          <select id=\"chimeWifiDrop\" style=\"flex:1\"></select>
          <button id=\"previewWifiDrop\" type=\"button\">Preview</button>
        </div>
      </label>
      <label>Reboot detected
        <div style=\"display:flex;gap:8px;align-items:center;margin-top:4px\">
          <select id=\"chimeRebootDetected\" style=\"flex:1\"></select>
          <button id=\"previewRebootDetected\" type=\"button\">Preview</button>
        </div>
      </label>
      <label>Presence change
        <div class="muted" style="margin-top:2px">Plays when presence transitions Present ↔ Clear.</div>
        <div style="display:flex;gap:8px;align-items:center;margin-top:4px">
          <select id="chimePresenceChange" style="flex:1"></select>
          <button id="previewPresenceChange" type="button">Preview</button>
        </div>
      </label>
    </div>
  </div>
  <div class=\"card\">
    <b>Radar Self Suppression</b>
    <div class=\"muted\" style=\"margin-top:4px\">Treat a persistent near-field signature as 'self' and ignore it in derived presence.</div>
    <div style=\"display:grid;grid-template-columns:repeat(2,minmax(260px,1fr));gap:12px;margin-top:10px\">
      <label style=\"display:flex;align-items:center;gap:8px\"><input id=\"radarSelfSuppressEnabled\" type=\"checkbox\" /> Enable self suppression</label>
      <label>Presence mode
        <select id=\"radarPresenceMode\" style=\"margin-left:8px\"><option value=\"raw\">Raw (default)</option><option value=\"derived\">Derived (ignore self)</option></select>
      </label>
      <div id=\"radarPresenceModeHelp\" class=\"muted\" style=\"grid-column:1 / -1\">Derived mode requires self suppression.</div>
      <label>Near distance (cm)
        <input id=\"radarSelfSuppressNearCm\" type=\"number\" min=\"20\" max=\"200\" step=\"1\" style=\"margin-left:8px;width:120px\" />
      </label>
      <label>Persist time (seconds)
        <input id=\"radarSelfSuppressPersistS\" type=\"number\" min=\"5\" max=\"120\" step=\"1\" style=\"margin-left:8px;width:120px\" />
      </label>
      <label>Jitter threshold (cm)
        <input id=\"radarSelfSuppressJitterCm\" type=\"number\" min=\"2\" max=\"80\" step=\"1\" style=\"margin-left:8px;width:120px\" />
      </label>
    </div>
  </div>
  """
  script = """
  const trends = [
    { key: 'air_eco2', title: 'ECO2' },
    { key: 'env_temp', title: 'Temp' },
    { key: 'env_hum', title: 'Humidity' },
    { key: 'air_tvoc', title: 'TVOC' },
  ];

  const chimeOptions = [
    { key: 'none', title: 'None' },
    { key: 'startup_vault_boot', title: 'Startup · Vault Boot' },
    { key: 'startup_atomic_sunrise', title: 'Startup · Atomic Sunrise' },
    { key: 'startup_radiant_bootloader', title: 'Startup · Radiant Bootloader' },
    { key: 'startup_field_unit_online', title: 'Startup · Field Unit Online' },
    { key: 'warn_radiation_spike', title: 'Warning · Radiation Spike' },
    { key: 'warn_system_fault', title: 'Warning · System Fault' },
    { key: 'warn_low_power', title: 'Warning · Low Power' },
  ];

  function fillTrendSelect(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = '';
    for (const t of trends) {
      const opt = document.createElement('option');
      opt.value = t.key;
      opt.textContent = t.title;
      el.appendChild(opt);
    }
  }

  function fillChimeSelect(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = '';
    for (const c of chimeOptions) {
      const opt = document.createElement('option');
      opt.value = c.key;
      opt.textContent = c.title;
      el.appendChild(opt);
    }
  }

  async function fetchJson(url, options) {
    const r = await fetch(url, options || { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + url);
    return await r.json();
  }

  function getPayload() {
    const suppressEnabled = !!document.getElementById('radarSelfSuppressEnabled')?.checked;
    const requestedMode = (String(document.getElementById('radarPresenceMode')?.value || 'raw').toLowerCase() === 'derived') ? 'derived' : 'raw';
    const effectiveMode = suppressEnabled ? requestedMode : 'raw';
    return {
      field_mode_start: !!document.getElementById('fieldModeStart')?.checked,
      units_distance: document.getElementById('unitsDistance')?.value === 'm' ? 'm' : 'cm',
      chart_slot_a: document.getElementById('slotA')?.value || 'air_eco2',
      chart_slot_b: document.getElementById('slotB')?.value || 'env_temp',
      chart_slot_c: document.getElementById('slotC')?.value || 'env_hum',
      chart_slot_d: document.getElementById('slotD')?.value || 'air_tvoc',
      chime_event_startup: document.getElementById('chimeStartup')?.value || 'startup_vault_boot',
      chime_event_air_spike: document.getElementById('chimeAirSpike')?.value || 'warn_radiation_spike',
      chime_event_wifi_drop: document.getElementById('chimeWifiDrop')?.value || 'warn_low_power',
      chime_event_reboot_detected: document.getElementById('chimeRebootDetected')?.value || 'warn_system_fault',
      chime_event_presence_change: document.getElementById('chimePresenceChange')?.value || 'none',
      radar_self_suppress_enabled: suppressEnabled || (effectiveMode === 'derived'),
      radar_presence_mode: effectiveMode,
      radar_self_suppress_near_cm: Number(document.getElementById('radarSelfSuppressNearCm')?.value || 80),
      radar_self_suppress_persist_s: Number(document.getElementById('radarSelfSuppressPersistS')?.value || 20),
      radar_self_suppress_jitter_cm: Number(document.getElementById('radarSelfSuppressJitterCm')?.value || 15),
    };
  }

  function syncRadarPresenceModeUi() {
    const enabledEl = document.getElementById('radarSelfSuppressEnabled');
    const modeEl = document.getElementById('radarPresenceMode');
    const helpEl = document.getElementById('radarPresenceModeHelp');
    const enabled = !!enabledEl?.checked;
    if (modeEl) {
      modeEl.disabled = !enabled;
      if (!enabled) modeEl.value = 'raw';
    }
    if (helpEl) {
      helpEl.style.opacity = enabled ? '0.65' : '1';
    }
  }

  function applySettings(s) {
    document.getElementById('fieldModeStart').checked = !!s.field_mode_start;
    document.getElementById('unitsDistance').value = String(s.units_distance || 'cm') === 'm' ? 'm' : 'cm';
    document.getElementById('slotA').value = s.chart_slot_a || 'air_eco2';
    document.getElementById('slotB').value = s.chart_slot_b || 'env_temp';
    document.getElementById('slotC').value = s.chart_slot_c || 'env_hum';
    document.getElementById('slotD').value = s.chart_slot_d || 'air_tvoc';
    document.getElementById('chimeStartup').value = s.chime_event_startup || 'startup_vault_boot';
    document.getElementById('chimeAirSpike').value = s.chime_event_air_spike || 'warn_radiation_spike';
    document.getElementById('chimeWifiDrop').value = s.chime_event_wifi_drop || 'warn_low_power';
    document.getElementById('chimeRebootDetected').value = s.chime_event_reboot_detected || 'warn_system_fault';
    document.getElementById('chimePresenceChange').value = s.chime_event_presence_change || 'none';
    document.getElementById('radarSelfSuppressEnabled').checked = !!s.radar_self_suppress_enabled;
    document.getElementById('radarPresenceMode').value = String(s.radar_presence_mode || 'raw') === 'derived' ? 'derived' : 'raw';
    document.getElementById('radarSelfSuppressNearCm').value = String(Number(s.radar_self_suppress_near_cm || 80));
    document.getElementById('radarSelfSuppressPersistS').value = String(Number(s.radar_self_suppress_persist_s || 20));
    document.getElementById('radarSelfSuppressJitterCm').value = String(Number(s.radar_self_suppress_jitter_cm || 15));
    syncRadarPresenceModeUi();
  }

  async function saveSettings() {
    const msg = document.getElementById('saveMsg');
    const payload = getPayload();
    await fetchJson('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (msg) msg.textContent = 'Saved.';
  }

  async function resetSettings() {
    const msg = document.getElementById('saveMsg');
    const data = await fetchJson('/api/settings/reset', { method: 'POST' });
    applySettings(data.settings || {});
    if (msg) msg.textContent = 'Reset to defaults.';
  }

  let previewBusyUntil = 0;

  async function previewChimeBySelect(selectId) {
    const nowMs = Date.now();
    if (nowMs < previewBusyUntil) return;
    previewBusyUntil = nowMs + 500;
    const selectEl = document.getElementById(selectId);
    const msg = document.getElementById('saveMsg');
    const key = selectEl?.value || 'none';
    const resp = await fetchJson('/api/chime/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key }),
    });
    if (msg) msg.textContent = resp && resp.played ? 'Preview played.' : 'Preview skipped (None).';
  }

  (async () => {
    ['slotA', 'slotB', 'slotC', 'slotD'].forEach(fillTrendSelect);
    ['chimeStartup', 'chimeAirSpike', 'chimeWifiDrop', 'chimeRebootDetected', 'chimePresenceChange'].forEach(fillChimeSelect);
    const data = await fetchJson('/api/settings');
    applySettings(data || {});
    document.getElementById('radarSelfSuppressEnabled')?.addEventListener('change', () => syncRadarPresenceModeUi());
    document.getElementById('saveBtn')?.addEventListener('click', () => saveSettings().catch((e) => alert(String(e.message || e))));
    document.getElementById('resetBtn')?.addEventListener('click', () => resetSettings().catch((e) => alert(String(e.message || e))));
    document.getElementById('previewStartup')?.addEventListener('click', () => previewChimeBySelect('chimeStartup').catch((e) => alert(String(e.message || e))));
    document.getElementById('previewAirSpike')?.addEventListener('click', () => previewChimeBySelect('chimeAirSpike').catch((e) => alert(String(e.message || e))));
    document.getElementById('previewWifiDrop')?.addEventListener('click', () => previewChimeBySelect('chimeWifiDrop').catch((e) => alert(String(e.message || e))));
    document.getElementById('previewRebootDetected')?.addEventListener('click', () => previewChimeBySelect('chimeRebootDetected').catch((e) => alert(String(e.message || e))));
    document.getElementById('previewPresenceChange')?.addEventListener('click', () => previewChimeBySelect('chimePresenceChange').catch((e) => alert(String(e.message || e))));
  })();
  """
  return render_shell_page("/settings", "HERMES Settings", body, script)


def render_reports_page() -> str:
  body = """
  <div class=\"card\">
    <div style=\"display:grid;grid-template-columns:repeat(2,minmax(240px,1fr));gap:10px\">
      <label>Preset
        <select id=\"reportPreset\">
          <option value=\"24h\">24h</option>
          <option value=\"3d\">3d</option>
          <option value=\"1w\">1w</option>
          <option value=\"1m\">1m</option>
          <option value=\"custom\">Custom</option>
        </select>
      </label>
      <label>Start (custom)
        <input id=\"reportStart\" type=\"datetime-local\" />
      </label>
      <label>End (custom)
        <input id=\"reportEnd\" type=\"datetime-local\" />
      </label>
    </div>
    <div style=\"margin-top:10px;display:flex;gap:12px;flex-wrap:wrap\">
      <label><input id=\"incPresence\" type=\"checkbox\" checked /> Presence summary</label>
      <label><input id=\"incAir\" type=\"checkbox\" checked /> Air quality</label>
      <label><input id=\"incEvents\" type=\"checkbox\" checked /> Events summary</label>
      <label><input id=\"incRssi\" type=\"checkbox\" /> RSSI summary</label>
    </div>
    <div style=\"margin-top:12px;display:flex;gap:8px;align-items:center\">
      <button id=\"genReportBtn\">Generate</button>
      <span id=\"reportMsg\" class=\"muted\"></span>
    </div>
  </div>

  <div class=\"card\">
    <b>Recent reports</b>
    <div id=\"reportsList\" style=\"margin-top:10px\" class=\"muted\">loading...</div>
  </div>
  """
  script = """
  function customVisible() {
    const custom = (document.getElementById('reportPreset')?.value || '') === 'custom';
    document.getElementById('reportStart').disabled = !custom;
    document.getElementById('reportEnd').disabled = !custom;
  }

  async function fetchJson(url, opts) {
    const r = await fetch(url, opts || { cache: 'no-store' });
    if (!r.ok) {
      const txt = await r.text();
      throw new Error('HTTP ' + r.status + ' ' + url + ' :: ' + txt.slice(0, 200));
    }
    return await r.json();
  }

  async function loadReports() {
    const data = await fetchJson('/api/reports?limit=10');
    const rows = Array.isArray(data.rows) ? data.rows : [];
    const root = document.getElementById('reportsList');
    if (!root) return;
    if (!rows.length) {
      root.textContent = 'No reports yet.';
      return;
    }
    root.innerHTML = rows.map((row) =>
      '<div style="border-bottom:1px solid #26313d;padding:8px 0">'
      + '<div><b>#' + row.id + '</b> · ' + (row.preset || '?') + ' · ' + (row.range_start_utc || '') + ' → ' + (row.range_end_utc || '') + '</div>'
      + '<div class="muted">Created ' + (row.ts_local || row.ts_utc || '') + ' · status=' + (row.status || '') + '</div>'
      + '<div style="margin-top:4px"><a href="' + row.download_url + '">Download</a></div>'
      + '</div>'
    ).join('');
  }

  async function generateReport() {
    const msg = document.getElementById('reportMsg');
    if (msg) msg.textContent = 'Generating...';
    const payload = {
      preset: document.getElementById('reportPreset')?.value || '24h',
      start_local: document.getElementById('reportStart')?.value || null,
      end_local: document.getElementById('reportEnd')?.value || null,
      include: {
        presence: !!document.getElementById('incPresence')?.checked,
        air: !!document.getElementById('incAir')?.checked,
        events: !!document.getElementById('incEvents')?.checked,
        rssi: !!document.getElementById('incRssi')?.checked,
      },
    };
    const out = await fetchJson('/api/reports/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (msg) msg.innerHTML = 'Done. <a href="' + out.download_url + '">Download report</a>';
    await loadReports();
  }

  (async () => {
    const preset = document.getElementById('reportPreset');
    if (preset) preset.addEventListener('change', customVisible);
    document.getElementById('genReportBtn')?.addEventListener('click', () => generateReport().catch((e) => {
      const msg = document.getElementById('reportMsg');
      if (msg) msg.textContent = 'Failed: ' + String(e.message || e);
    }));
    customVisible();
    await loadReports();
  })();
  """
  return render_shell_page("/reports", "HERMES Reports", body, script)


def render_calibration_page() -> str:
  body = """
  <div class=\"card\">
    <b>Empty-room calibration</b>
    <div class=\"muted\" style=\"margin-top:5px\">Clear humans within 6m for 60 seconds.</div>
    <div style=\"margin-top:10px;display:flex;gap:8px;flex-wrap:wrap\">
      <button id=\"calStart\">Start</button>
      <button id=\"calCancel\">Cancel</button>
      <button id=\"calSelfCheck\">Run quick sensor self-check</button>
      <span id=\"calMsg\" class=\"muted\"></span>
    </div>
    <div style=\"margin-top:12px;border:1px solid #26313d;border-radius:999px;height:12px;background:#111820;overflow:hidden\"><div id=\"calBar\" style=\"height:100%;width:0%;background:#4ca9ff\"></div></div>
    <div style=\"margin-top:10px;display:grid;grid-template-columns:repeat(4,minmax(130px,1fr));gap:10px\">
      <div><span class=\"muted\">Status</span><div id=\"calStatus\">idle</div></div>
      <div><span class=\"muted\">Countdown</span><div id=\"calRemain\">--</div></div>
      <div><span class=\"muted\">Samples</span><div id=\"calSamples\">0</div></div>
      <div><span class=\"muted\">False positives</span><div id=\"calFalse\">0</div></div>
    </div>
    <div style=\"margin-top:10px\"><span class=\"muted\">Result</span><div id=\"calResult\">-</div></div>
    <div style=\"margin-top:10px;display:grid;grid-template-columns:repeat(2,minmax(180px,1fr));gap:10px\">
      <div><span class=\"muted\">Grade</span><div id=\"calGrade\">--</div></div>
      <div><span class=\"muted\">Recommendation</span><div id=\"calReco\">--</div></div>
    </div>
  </div>

  <div class=\"card\">
    <b>Calibration history (last 10)</b>
    <div id=\"calHistory\" style=\"margin-top:10px\" class=\"muted\">loading...</div>
  </div>
  """
  script = """
  let calSessionId = '';
  let calPoll = null;
  let calTotal = 70;

  async function fetchJson(url, opts) {
    const r = await fetch(url, opts || { cache: 'no-store' });
    if (!r.ok) {
      const txt = await r.text();
      throw new Error('HTTP ' + r.status + ' ' + url + ' :: ' + txt.slice(0, 200));
    }
    return await r.json();
  }

  function renderProgress(data) {
    const st = String(data.status || 'idle');
    const rem = Number(data.remaining_s || 0);
    const pre = Number(data.pre_remaining_s || 0);
    const phase = String(data.phase || st);
    const elapsed = phase === 'prepare' ? (10 - pre) : (10 + (Number(data.duration_s || 60) - rem));
    const pct = Math.max(0, Math.min(100, (elapsed / Math.max(1, calTotal)) * 100));
    const bar = document.getElementById('calBar');
    if (bar) bar.style.width = pct.toFixed(1) + '%';
    const statusEl = document.getElementById('calStatus');
    if (statusEl) statusEl.textContent = phase;
    const remEl = document.getElementById('calRemain');
    if (remEl) remEl.textContent = String(rem);
    const sEl = document.getElementById('calSamples');
    if (sEl) sEl.textContent = String(data.samples || 0);
    const fEl = document.getElementById('calFalse');
    if (fEl) fEl.textContent = String(data.false_presence_count || 0);
    if (st === 'done' && data.result) {
      const r = data.result;
      const resultEl = document.getElementById('calResult');
      if (resultEl) resultEl.textContent = 'baseline=' + (r.baseline_detect_cm ?? 'n/a') + 'cm, noise=' + (r.noise_detect_cm ?? 'n/a') + ', false=' + (r.false_presence_count ?? 0);
      const gradeEl = document.getElementById('calGrade');
      const recoEl = document.getElementById('calReco');
      if (gradeEl) gradeEl.textContent = String(r.calibration_grade || '--');
      if (recoEl) recoEl.textContent = String(r.calibration_recommendation || '--');
    }
  }

  async function loadHistory() {
    const data = await fetchJson('/api/radar/calibration/history?limit=10');
    const rows = Array.isArray(data.rows) ? data.rows : [];
    const el = document.getElementById('calHistory');
    if (!el) return;
    if (!rows.length) {
      el.textContent = 'No calibration rows yet.';
      return;
    }
    el.innerHTML = rows.map((row) =>
      '<div style="border-bottom:1px solid #26313d;padding:6px 0">#' + row.id
      + ' · ' + (row.ts_local || row.ts_utc || '')
      + ' · baseline ' + (row.baseline_detect_cm ?? 'n/a')
      + ' · noise ' + (row.noise_detect_cm ?? 'n/a')
      + ' · false ' + (row.false_presence_count ?? 0)
      + ' · grade ' + String(row.calibration_grade || '--')
      + '</div>'
    ).join('');
  }

  async function pollCal() {
    if (!calSessionId) return;
    const data = await fetchJson('/api/radar/calibrate/' + calSessionId);
    renderProgress(data);
    if (String(data.status || '') === 'done' || String(data.status || '') === 'cancelled' || String(data.status || '') === 'error') {
      if (calPoll) { clearInterval(calPoll); calPoll = null; }
      calSessionId = '';
      await loadHistory();
    }
  }

  async function startCal() {
    const msg = document.getElementById('calMsg');
    if (msg) msg.textContent = 'Starting...';
    const data = await fetchJson('/api/radar/calibrate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ duration_s: 60, max_range_cm: 600 }),
    });
    calSessionId = String(data.session_id || '');
    calTotal = Number((data.duration_s || 60) + (data.pre_countdown_s || 10));
    if (calPoll) clearInterval(calPoll);
    calPoll = setInterval(() => pollCal().catch((e) => {
      const msg2 = document.getElementById('calMsg');
      if (msg2) msg2.textContent = 'Poll failed: ' + String(e.message || e);
    }), 1000);
    await pollCal();
    if (msg) msg.textContent = 'Running.';
  }

  async function cancelCal() {
    if (!calSessionId) return;
    await fetchJson('/api/radar/calibrate/' + calSessionId + '/cancel', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    await pollCal();
  }

  async function selfCheck() {
    const [radar, env, health] = await Promise.all([
      fetchJson('/api/radar/latest'),
      fetchJson('/api/latest/env?limit=1'),
      fetchJson('/api/health'),
    ]);
    const okRadar = Number(radar.alive || 0) === 1;
    const okEnv = Array.isArray(env.rows) && env.rows.length > 0;
    const f = health.freshness || {};
    const okHealth = String(f['RADAR'] || '') === 'ok' || String(f['RADAR'] || '') === 'stale';
    const msg = document.getElementById('calMsg');
    if (msg) msg.textContent = (okRadar && okEnv && okHealth) ? 'Self-check: GREEN' : 'Self-check: RED';
  }

  (async () => {
    document.getElementById('calStart')?.addEventListener('click', () => startCal().catch((e) => {
      const msg = document.getElementById('calMsg');
      if (msg) msg.textContent = 'Start failed: ' + String(e.message || e);
    }));
    document.getElementById('calCancel')?.addEventListener('click', () => cancelCal().catch((e) => {
      const msg = document.getElementById('calMsg');
      if (msg) msg.textContent = 'Cancel failed: ' + String(e.message || e);
    }));
    document.getElementById('calSelfCheck')?.addEventListener('click', () => selfCheck().catch((e) => {
      const msg = document.getElementById('calMsg');
      if (msg) msg.textContent = 'Self-check failed: ' + String(e.message || e);
    }));
    await loadHistory();
  })();
  """
  return render_shell_page("/calibration", "HERMES Calibration", body, script)


def render_history_page() -> str:
  body = """
  <div class=\"card\">
    <div style=\"display:grid;grid-template-columns:repeat(3,minmax(220px,1fr));gap:10px\">
      <label>Kind
        <select id=\"histKind\"><option value=\"radar\">radar</option><option value=\"events\">events</option><option value=\"env\">env</option></select>
      </label>
      <label>Range
        <select id=\"histRange\"><option value=\"24h\">24h</option><option value=\"3d\">3d</option><option value=\"1w\">1w</option><option value=\"1m\">1m</option><option value=\"custom\">Custom</option></select>
      </label>
      <label>Limit
        <select id=\"histLimit\"><option>200</option><option selected>500</option><option>1000</option><option>5000</option></select>
      </label>
      <label>Custom start
        <input id=\"histStart\" type=\"datetime-local\" />
      </label>
      <label>Custom end
        <input id=\"histEnd\" type=\"datetime-local\" />
      </label>
      <label>Search
        <input id=\"histQ\" type=\"text\" placeholder=\"message/fields\" />
      </label>
    </div>
    <div style=\"margin-top:10px;display:flex;gap:8px;align-items:center\">
      <button id=\"histRun\">Run</button>
      <button id=\"histCsv\">Export CSV</button>
      <span id=\"histMsg\" class=\"muted\"></span>
    </div>
  </div>
  <div class=\"card\">
    <div id=\"histMeta\" class=\"muted\"></div>
    <div style=\"overflow:auto;margin-top:8px\"><table id=\"histTable\" style=\"width:100%;border-collapse:collapse\"></table></div>
  </div>
  """
  script = """
  function customEnabled() {
    const custom = (document.getElementById('histRange')?.value || '') === 'custom';
    document.getElementById('histStart').disabled = !custom;
    document.getElementById('histEnd').disabled = !custom;
  }

  function buildParams() {
    const p = new URLSearchParams();
    p.set('kind', document.getElementById('histKind')?.value || 'radar');
    p.set('range', document.getElementById('histRange')?.value || '24h');
    p.set('limit', document.getElementById('histLimit')?.value || '500');
    const q = document.getElementById('histQ')?.value || '';
    if (q.trim()) p.set('q', q.trim());
    const start = document.getElementById('histStart')?.value || '';
    const end = document.getElementById('histEnd')?.value || '';
    if (start) p.set('start_local', start);
    if (end) p.set('end_local', end);
    return p;
  }

  async function fetchJson(url) {
    const r = await fetch(url, { cache: 'no-store' });
    if (!r.ok) {
      const txt = await r.text();
      throw new Error('HTTP ' + r.status + ' ' + url + ' :: ' + txt.slice(0, 200));
    }
    return await r.json();
  }

  function renderRows(rows) {
    const table = document.getElementById('histTable');
    if (!table) return;
    if (!rows.length) {
      table.innerHTML = '<tr><td class="muted">No rows.</td></tr>';
      return;
    }
    const cols = Object.keys(rows[0]);
    const head = '<thead><tr>' + cols.map((c) => '<th style="text-align:left;border-bottom:1px solid #26313d;padding:6px 8px">' + c + '</th>').join('') + '</tr></thead>';
    const body = '<tbody>' + rows.map((row) => '<tr>' + cols.map((c) => '<td style="border-bottom:1px solid #26313d;padding:6px 8px;white-space:nowrap">' + String(row[c] ?? '') + '</td>').join('') + '</tr>').join('') + '</tbody>';
    table.innerHTML = head + body;
  }

  async function runHistory() {
    const msg = document.getElementById('histMsg');
    if (msg) msg.textContent = 'Loading...';
    const params = buildParams();
    const data = await fetchJson('/api/history?' + params.toString());
    renderRows(Array.isArray(data.rows) ? data.rows : []);
    const meta = document.getElementById('histMeta');
    if (meta) {
      const r = data.range || {};
      meta.textContent = 'Rows: ' + (data.rows || []).length + ' · Range: ' + (r.start_local || '') + ' → ' + (r.end_local || '');
    }
    if (msg) msg.textContent = 'Done.';
  }

  function exportCsv() {
    const params = buildParams();
    window.location.href = '/api/history/export.csv?' + params.toString();
  }

  (async () => {
    document.getElementById('histRange')?.addEventListener('change', customEnabled);
    document.getElementById('histRun')?.addEventListener('click', () => runHistory().catch((e) => {
      const msg = document.getElementById('histMsg');
      if (msg) msg.textContent = 'Failed: ' + String(e.message || e);
    }));
    document.getElementById('histCsv')?.addEventListener('click', exportCsv);
    customEnabled();
    await runHistory();
  })();
  """
  return render_shell_page("/history", "HERMES History", body, script)


JS_BUNDLE = r"""
const tables = ['hb','env','air','light','mic_noise','esp_net','radar'];
const tableLabels = {
  hb: 'Heartbeat',
  env: 'Environment',
  air: 'Air Quality',
  light: 'Light',
  mic_noise: 'Microphone',
  esp_net: 'Wi-Fi',
  radar: 'Human Presence',
};
const displayFresh = ['HB','ENV','AIR','LIGHT','MIC','ESP,NET','RADAR'];
const EXPECTED_INTERVALS = {
  radar: 1,
  env: 5,
  air: 5,
  tof: 1,
  therm: 1,
  light: 5,
  mic: 5,
  esp_net: 5,
  hb: 1,
};
const FRESHNESS_TABLE_BY_PREFIX = {
  'HB': 'hb',
  'ENV': 'env',
  'AIR': 'air',
  'LIGHT': 'light',
  'MIC': 'mic_noise',
  'ESP,NET': 'esp_net',
  'RADAR': 'radar',
};
const EXPECTED_KEY_BY_PREFIX = {
  'HB': 'hb',
  'ENV': 'env',
  'AIR': 'air',
  'LIGHT': 'light',
  'MIC': 'mic',
  'ESP,NET': 'esp_net',
  'RADAR': 'radar',
};
const radarTrend = { key: 'radar_bodies', title: 'Human Presences', unit: 'bodies', decimals: 0, table: 'radar' };
const chartTrendOptions = [
  { key: 'air_eco2', title: 'ECO2', unit: 'ppm', decimals: 0, table: 'air' },
  { key: 'env_temp', title: 'Temp', unit: '°C', decimals: 1, table: 'env' },
  { key: 'env_hum', title: 'Humidity', unit: '%', decimals: 1, table: 'env' },
  { key: 'air_tvoc', title: 'TVOC', unit: 'ppb', decimals: 0, table: 'air' },
];
const trendSeries = [radarTrend, ...chartTrendOptions];
const chartSlotDefaults = { A: 'air_eco2', B: 'env_temp', C: 'env_hum', D: 'air_tvoc' };
const chartSlotsStorageKey = 'chartSlots';
const chartSlotOrder = ['A', 'B', 'C', 'D'];
let chartSlots = { ...chartSlotDefaults };
const dashboardSettingDefaults = {
  field_mode_start: false,
  units_distance: 'cm',
  chart_slot_a: chartSlotDefaults.A,
  chart_slot_b: chartSlotDefaults.B,
  chart_slot_c: chartSlotDefaults.C,
  chart_slot_d: chartSlotDefaults.D,
  radar_self_suppress_enabled: false,
  radar_presence_mode: 'raw',
};
let dashboardSettings = { ...dashboardSettingDefaults };
let useDerivedPresence = false;
const radarNow = {
  enabled: true,
  view: 'now',
  maxRangeCm: 300,
  state: {
    alive: 0,
    target: 0,
    detect_cm: 0,
    move_cm: 0,
    stat_cm: 0,
    move_en: 0,
    stat_en: 0,
    ts_utc: null,
  },
};
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
let integrityController = null;
let tickerController = null;
let lastUpdatedMs = 0;
let trendMinutes = 60;
let chartResizeObserver = null;
const pendingChartRedrawIds = new Set();
let pendingChartRedrawTimer = null;
const radarCalibration = {
  sessionId: null,
  status: 'idle',
  latestResult: null,
  pollHandle: null,
};
const radarBodiesState = {
  initialized: false,
  stableBodies: 0,
  pendingBodies: 0,
  pendingSinceMs: 0,
};
let integrityWarningActive = false;
let tickerHealthy = true;

function updateTickerDots() {
  const tickerDot = document.getElementById('tickerDot');
  const integrityDot = document.getElementById('integrityDot');
  let color = 'rgba(120,180,255,0.9)';
  if (integrityWarningActive) {
    color = 'rgba(255,180,0,0.95)';
  } else if (!tickerHealthy) {
    color = 'rgba(140,150,165,0.55)';
  }
  if (tickerDot) tickerDot.style.background = color;
  if (integrityDot) integrityDot.style.background = color;
}

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

function applyFieldModeEntryVisibility() {
  const el = document.getElementById('fieldModeEntry');
  if (!el) return;
  el.classList.toggle('hidden', !dashboardSettings.field_mode_start);
}

async function loadDashboardSettings() {
  try {
    const resp = await fetch('/api/settings', { cache: 'no-store' });
    if (!resp.ok) return;
    const data = await resp.json();
    dashboardSettings = { ...dashboardSettingDefaults, ...(data || {}) };
    useDerivedPresence = !!dashboardSettings.radar_self_suppress_enabled;
    chartSlots.A = getTrendByKey(String(dashboardSettings.chart_slot_a || chartSlotDefaults.A)).key;
    chartSlots.B = getTrendByKey(String(dashboardSettings.chart_slot_b || chartSlotDefaults.B)).key;
    chartSlots.C = getTrendByKey(String(dashboardSettings.chart_slot_c || chartSlotDefaults.C)).key;
    chartSlots.D = getTrendByKey(String(dashboardSettings.chart_slot_d || chartSlotDefaults.D)).key;
    saveChartSlots();
    applyFieldModeEntryVisibility();
  } catch (_err) {
  }
}

async function refreshDashboardSettingsLight() {
  try {
    const resp = await fetch('/api/settings', { cache: 'no-store' });
    if (!resp.ok) return;
    const data = await resp.json();
    if (!data || typeof data !== 'object') return;
    dashboardSettings.units_distance = String(data.units_distance || 'cm') === 'm' ? 'm' : 'cm';
    dashboardSettings.field_mode_start = !!data.field_mode_start;
    applyFieldModeEntryVisibility();
    drawRadarScope(radarNow.state || {});
  } catch (_err) {
  }
}

function bindPresenceToggleUi() {
  const toggleEl = document.getElementById('radar-use-derived');
  if (!toggleEl) return;
  toggleEl.checked = !!useDerivedPresence;
  toggleEl.addEventListener('change', () => {
    useDerivedPresence = !!toggleEl.checked;
    drawRadarScope(radarNow.state || {});
  });
}

function getChartRenderSize(imgEl) {
  if (!imgEl) return null;
  const wrap = imgEl.closest('.chart-wrap.plot');
  if (!wrap) return null;
  const rect = wrap.getBoundingClientRect();
  const width = Math.max(160, Math.round(rect.width || 0));
  const height = Math.max(120, Math.round(rect.height || 0));
  if (!Number.isFinite(width) || !Number.isFinite(height)) return null;
  return { width, height };
}

function buildChartImageUrl(trendKey, cacheBust, imgEl) {
  const size = getChartRenderSize(imgEl);
  const dpr = Math.max(1, Math.min(3, Number(window.devicePixelRatio || 1)));
  const width = size ? size.width : 528;
  const height = size ? size.height : 226;
  return '/chart/' + trendKey + '.png?minutes=' + trendMinutes + '&w=' + width + '&h=' + height + '&dpr=' + dpr.toFixed(2) + '&ts=' + cacheBust;
}

function setTrendImageSrc(imgEl, trendKey, cacheBust) {
  if (!imgEl || !trendKey) return;
  imgEl.src = buildChartImageUrl(trendKey, cacheBust, imgEl);
}

function primeTrendImages(attempt = 0) {
  const images = Array.from(document.querySelectorAll('img.trend-img'));
  if (!images.length) return;
  const cacheBust = Date.now();
  let needsRetry = false;
  for (const imgEl of images) {
    const trendKey = String(imgEl.dataset.trendKey || '').trim();
    if (!trendKey) continue;
    const size = getChartRenderSize(imgEl);
    if (!size || size.width <= 180 || size.height <= 120) {
      needsRetry = true;
      continue;
    }
    setTrendImageSrc(imgEl, trendKey, cacheBust);
  }
  if (needsRetry && attempt < 12) {
    window.requestAnimationFrame(() => primeTrendImages(attempt + 1));
  }
}

function resizeAndRedrawChart(chartId) {
  const imgEl = document.getElementById(chartId);
  if (!imgEl) return;
  const trendKey = String(imgEl.dataset.trendKey || '').trim();
  if (!trendKey) return;
  setTrendImageSrc(imgEl, trendKey, Date.now());
}

function queueChartRedraw(chartId) {
  if (!chartId) return;
  pendingChartRedrawIds.add(chartId);
  if (pendingChartRedrawTimer) return;
  pendingChartRedrawTimer = setTimeout(() => {
    const ids = Array.from(pendingChartRedrawIds);
    pendingChartRedrawIds.clear();
    pendingChartRedrawTimer = null;
    for (const id of ids) {
      resizeAndRedrawChart(id);
    }
  }, 120);
}

function initChartResizeObserver() {
  if (chartResizeObserver) {
    chartResizeObserver.disconnect();
    chartResizeObserver = null;
  }
  if (typeof ResizeObserver === 'undefined') return;
  chartResizeObserver = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const wrap = entry.target;
      const imgEl = wrap.querySelector('img.trend-img');
      if (imgEl && imgEl.id) {
        queueChartRedraw(imgEl.id);
      }
    }
  });
  document.querySelectorAll('.chart-wrap.plot').forEach((wrap) => {
    chartResizeObserver.observe(wrap);
  });
}

async function persistChartSlotsToSettings() {
  try {
    await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chart_slot_a: chartSlots.A,
        chart_slot_b: chartSlots.B,
        chart_slot_c: chartSlots.C,
        chart_slot_d: chartSlots.D,
      }),
    });
  } catch (_err) {
  }
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

function getTrendByKey(key) {
  return chartTrendOptions.find((item) => item.key === key) || chartTrendOptions[0];
}

function loadChartSlots() {
  try {
    const raw = localStorage.getItem(chartSlotsStorageKey);
    if (!raw) return { ...chartSlotDefaults };
    const parsed = JSON.parse(raw);
    const resolved = { ...chartSlotDefaults };
    for (const slot of chartSlotOrder) {
      const requested = parsed && typeof parsed === 'object' ? String(parsed[slot] || '') : '';
      resolved[slot] = getTrendByKey(requested).key;
    }
    return resolved;
  } catch (_err) {
    return { ...chartSlotDefaults };
  }
}

function saveChartSlots() {
  try {
    localStorage.setItem(chartSlotsStorageKey, JSON.stringify(chartSlots));
  } catch (_err) {
  }
}

function renderChartSlotControls() {
  const root = document.getElementById('chartSlotControls');
  if (!root) return;
  root.innerHTML = '';
  for (const slot of chartSlotOrder) {
    if (slot === 'D') {
      const reportWrap = document.createElement('span');
      reportWrap.className = 'slot-ctrl';
      const reportBtn = document.createElement('button');
      reportBtn.type = 'button';
      reportBtn.textContent = 'Generate report';
      reportBtn.onclick = () => { window.location.href = '/reports'; };
      reportWrap.appendChild(reportBtn);
      root.appendChild(reportWrap);
    }
    const wrap = document.createElement('span');
    wrap.className = 'slot-ctrl';
    const label = document.createElement('label');
    label.setAttribute('for', 'chartSel' + slot);
    label.textContent = 'Slot ' + slot;
    const select = document.createElement('select');
    select.id = 'chartSel' + slot;
    for (const trend of chartTrendOptions) {
      const opt = document.createElement('option');
      opt.value = trend.key;
      opt.textContent = trend.title;
      select.appendChild(opt);
    }
    select.value = getTrendByKey(chartSlots[slot]).key;
    select.onchange = async () => {
      chartSlots[slot] = getTrendByKey(select.value).key;
      saveChartSlots();
      await persistChartSlotsToSettings();
      initTrends();
      await pollTrends();
    };
    wrap.appendChild(label);
    wrap.appendChild(select);
    root.appendChild(wrap);
  }
}

function clamp(value, min, max) {
  const v = Number(value);
  if (!Number.isFinite(v)) return min;
  return Math.min(max, Math.max(min, v));
}

function angleDiffDeg(a, b) {
  let diff = (a - b) % 360;
  if (diff > 180) diff -= 360;
  if (diff < -180) diff += 360;
  return Math.abs(diff);
}

function stableBodiesFromTarget(target) {
  return target === 3 ? 2 : (target === 0 ? 0 : 1);
}

function getStableBodies(target) {
  const now = Date.now();
  const rawBodies = stableBodiesFromTarget(target);
  if (!radarBodiesState.initialized) {
    radarBodiesState.initialized = true;
    radarBodiesState.stableBodies = rawBodies;
    radarBodiesState.pendingBodies = rawBodies;
    radarBodiesState.pendingSinceMs = now;
    return rawBodies;
  }
  if (rawBodies === radarBodiesState.stableBodies) {
    radarBodiesState.pendingBodies = rawBodies;
    radarBodiesState.pendingSinceMs = now;
    return radarBodiesState.stableBodies;
  }
  if (rawBodies !== radarBodiesState.pendingBodies) {
    radarBodiesState.pendingBodies = rawBodies;
    radarBodiesState.pendingSinceMs = now;
    return radarBodiesState.stableBodies;
  }
  if ((now - radarBodiesState.pendingSinceMs) >= 500) {
    radarBodiesState.stableBodies = rawBodies;
  }
  return radarBodiesState.stableBodies;
}

function markerLeft(cm, maxRange, markerHalfPx) {
  const pct = clamp((Number(cm || 0) / maxRange) * 100, 0, 100);
  return `calc(${pct}% - ${markerHalfPx}px)`;
}

function currentDistanceUnit() {
  return String((dashboardSettings && dashboardSettings.units_distance) || 'cm') === 'm' ? 'm' : 'cm';
}

function formatDistance(cm, units) {
  const value = Number(cm);
  if (!Number.isFinite(value) || value <= 0) return '—';
  if (String(units || 'cm') === 'm') return (value / 100).toFixed(2) + ' m';
  return Math.round(value) + ' cm';
}

function radarViewButtonsActive() {
  const nowBtn = document.getElementById('radar-view-now');
  const historyBtn = document.getElementById('radar-view-history');
  const nowPane = document.getElementById('radar-now-pane');
  const historyPane = document.getElementById('radar-history-pane');
  const showNow = radarNow.view === 'now';
  if (nowBtn) nowBtn.classList.toggle('active', showNow);
  if (historyBtn) historyBtn.classList.toggle('active', !showNow);
  if (nowPane) nowPane.classList.toggle('hidden', !showNow);
  if (historyPane) historyPane.classList.toggle('hidden', showNow);
}

window.setRadarView = function setRadarView(view) {
  radarNow.view = (view === 'history') ? 'history' : 'now';
  radarViewButtonsActive();
};

function setRadarCalibrationPanelVisible(visible) {
  const panel = document.getElementById('radar-cal-panel');
  if (panel) panel.classList.toggle('hidden', !visible);
}

function renderRadarCalibrationHistory(rows) {
  const el = document.getElementById('radar-cal-history');
  if (!el) return;
  const items = Array.isArray(rows) ? rows : [];
  if (!items.length) {
    el.textContent = 'No calibration history yet.';
    return;
  }
  el.innerHTML = items.slice(0, 5).map((row) => {
    const ts = row.ts_local || row.ts_utc || '';
    const baseline = row.baseline_detect_cm == null ? 'n/a' : Number(row.baseline_detect_cm).toFixed(1);
    const noise = row.noise_detect_cm == null ? 'n/a' : Number(row.noise_detect_cm).toFixed(1);
    const grade = String(row.calibration_grade || '--');
    return '<div>#' + row.id + ' ' + escapeHtml(formatDateTime(ts)) + ' · baseline ' + baseline + 'cm · noise ' + noise + ' · false ' + Number(row.false_presence_count || 0) + ' · grade ' + escapeHtml(grade) + '</div>';
  }).join('');
}

async function loadRadarCalibrationHistory() {
  try {
    const data = await fetchJson('/api/radar/calibration/history?limit=5', new AbortController());
    renderRadarCalibrationHistory(data.rows || []);
  } catch (_err) {
  }
}

function renderRadarCalibrationStatus(data) {
  const instructionEl = document.getElementById('radar-cal-instruction');
  const statusEl = document.getElementById('radar-cal-status');
  const remEl = document.getElementById('radar-cal-remaining');
  const samplesEl = document.getElementById('radar-cal-samples');
  const falseEl = document.getElementById('radar-cal-false');
  const baselineEl = document.getElementById('radar-cal-baseline');
  const noiseEl = document.getElementById('radar-cal-noise');
  const gradeEl = document.getElementById('radar-cal-grade');
  const recoEl = document.getElementById('radar-cal-reco');
  const phase = String(data.phase || data.status || 'idle');
  if (instructionEl) {
    if (phase === 'prepare') {
      instructionEl.textContent = 'Move away from the device. Calibration begins in ' + String(data.pre_remaining_s ?? data.remaining_s ?? '--') + 's.';
    } else if (phase === 'running') {
      instructionEl.textContent = 'Calibration capture in progress. Keep the area clear within 6m.';
    } else if (phase === 'done') {
      instructionEl.textContent = 'Calibration complete.';
    } else {
      instructionEl.textContent = 'Clear area within 6m for 60 seconds.';
    }
  }
  if (statusEl) statusEl.textContent = phase;
  if (remEl) remEl.textContent = (phase === 'running' || phase === 'prepare') ? String(data.remaining_s ?? '--') + 's' : '--';
  if (samplesEl) samplesEl.textContent = String(data.samples ?? 0);
  if (falseEl) falseEl.textContent = String(data.false_presence_count ?? 0);
  if (data.result) {
    const base = data.result.baseline_detect_cm;
    const noise = data.result.noise_detect_cm;
    if (baselineEl) baselineEl.textContent = base == null ? 'n/a' : Number(base).toFixed(1) + 'cm';
    if (noiseEl) noiseEl.textContent = noise == null ? 'n/a' : Number(noise).toFixed(1) + 'cm';
    if (gradeEl) gradeEl.textContent = String(data.result.calibration_grade || '--');
    if (recoEl) recoEl.textContent = String(data.result.calibration_recommendation || '--');
  } else {
    if (gradeEl) gradeEl.textContent = '--';
    if (recoEl) recoEl.textContent = '--';
  }
}

async function pollRadarCalibrationStatus() {
  if (!radarCalibration.sessionId) return;
  try {
    const data = await fetchJson('/api/radar/calibrate/' + radarCalibration.sessionId, new AbortController());
    radarCalibration.status = String(data.status || 'idle');
    if (radarCalibration.status === 'done') {
      radarCalibration.latestResult = data.result || null;
      if (radarCalibration.pollHandle) {
        clearInterval(radarCalibration.pollHandle);
        radarCalibration.pollHandle = null;
      }
      await loadRadarCalibrationHistory();
    } else if (radarCalibration.status === 'cancelled' || radarCalibration.status === 'error') {
      if (radarCalibration.pollHandle) {
        clearInterval(radarCalibration.pollHandle);
        radarCalibration.pollHandle = null;
      }
    }
    renderRadarCalibrationStatus(data);
  } catch (_err) {
  }
}

window.startRadarCalibration = async function startRadarCalibration() {
  try {
    const data = await postJson('/api/radar/calibrate', { duration_s: 60, max_range_cm: 600 });
    radarCalibration.sessionId = String(data.session_id || '');
    radarCalibration.status = String(data.status || 'prepare');
    radarCalibration.latestResult = null;
    setRadarCalibrationPanelVisible(true);
    renderRadarCalibrationStatus({ status: radarCalibration.status, phase: radarCalibration.status, remaining_s: 10, pre_remaining_s: 10, samples: 0, false_presence_count: 0 });
    if (radarCalibration.pollHandle) clearInterval(radarCalibration.pollHandle);
    radarCalibration.pollHandle = setInterval(pollRadarCalibrationStatus, 1000);
    await pollRadarCalibrationStatus();
  } catch (err) {
    console.error(err);
    alert('Calibration start failed: ' + (err && err.message ? err.message : err));
  }
};

window.cancelRadarCalibration = async function cancelRadarCalibration() {
  if (!radarCalibration.sessionId) {
    setRadarCalibrationPanelVisible(false);
    return;
  }
  try {
    await postJson('/api/radar/calibrate/' + radarCalibration.sessionId + '/cancel', {});
    radarCalibration.status = 'cancelled';
    if (radarCalibration.pollHandle) {
      clearInterval(radarCalibration.pollHandle);
      radarCalibration.pollHandle = null;
    }
    await pollRadarCalibrationStatus();
  } catch (err) {
    console.error(err);
  }
};

window.saveRadarCalibrationNote = async function saveRadarCalibrationNote() {
  const noteEl = document.getElementById('radar-cal-note');
  const note = noteEl ? String(noteEl.value || '').trim() : '';
  const latest = radarCalibration.latestResult;
  if (!latest || !latest.id) {
    alert('No completed calibration to annotate yet.');
    return;
  }
  try {
    await postJson('/api/radar/calibration/' + latest.id + '/note', { note });
    await loadRadarCalibrationHistory();
  } catch (err) {
    console.error(err);
    alert('Failed to save note');
  }
};

function updateRadarReadout(state) {
  const alive = Number(state.alive || 0) === 1;
  const target = Number(state.target || 0);
  const moveMetric = clamp(Number(state.move_en || 0), 0, 100);
  const statMetric = clamp(Number(state.stat_en || 0), 0, 100);
  const detectCm = Math.round(clamp(Number(state.detect_cm || 0), 0, radarNow.maxRangeCm));
  const moveCm = Math.round(clamp(Number(state.move_cm || 0), 0, radarNow.maxRangeCm));
  const statCm = Math.round(clamp(Number(state.stat_cm || 0), 0, radarNow.maxRangeCm));
  const moveActive = alive && (moveMetric > 0 || target === 1 || target === 3);
  const statActive = alive && (statMetric > 0 || target === 2 || target === 3);
  const bodyCount = getStableBodies(target);

  const stateEl = document.getElementById('radar-now-state');
  const statePillEl = document.getElementById('radar-now-state-pill');
  const selfNoteEl = document.getElementById('radar-now-self-note');
  const bodiesEl = document.getElementById('radar-now-bodies');
  const detectEl = document.getElementById('radar-now-detect');
  const moveSigEl = document.getElementById('radar-now-move-sig');
  const statSigEl = document.getElementById('radar-now-stat-sig');
  const targetEl = document.getElementById('radar-now-target');
  const units = currentDistanceUnit();
  const hasDerived = Object.prototype.hasOwnProperty.call(state || {}, 'present_derived');
  const derivedEnabled = !!useDerivedPresence && hasDerived;
  const derivedPresent = derivedEnabled ? !!state.present_derived : false;
  const selfSuppressed = derivedEnabled && !!state.self_suppressed;
  const effectiveTarget = (derivedEnabled && !derivedPresent) ? 0 : target;
  const moveState = (derivedEnabled && !derivedPresent) ? false : (moveMetric > 0);
  const statState = (derivedEnabled && !derivedPresent) ? false : (statMetric > 0);

  if (stateEl) {
    if (!alive) stateEl.textContent = 'Radar offline';
    else if (effectiveTarget === 0) stateEl.textContent = 'No presence';
    else if (moveState && !statState) stateEl.textContent = 'Moving presence';
    else if (!moveState && statState) stateEl.textContent = 'Still presence';
    else stateEl.textContent = 'Moving + still';
  }
  if (statePillEl) {
    statePillEl.className = 'status-pill';
    if (!alive) {
      statePillEl.textContent = 'RADAR OFFLINE';
      statePillEl.classList.add('state-offline');
    } else if (effectiveTarget === 0) {
      statePillEl.textContent = 'NO PRESENCE';
      statePillEl.classList.add('state-none');
    } else if (moveState && !statState) {
      statePillEl.textContent = 'MOVING PRESENCE';
      statePillEl.classList.add('state-moving');
    } else if (!moveState && statState) {
      statePillEl.textContent = 'STILL PRESENCE';
      statePillEl.classList.add('state-still');
    } else {
      statePillEl.textContent = 'MOVING + STILL';
      statePillEl.classList.add('state-both');
    }
  }
  if (selfNoteEl) {
    selfNoteEl.textContent = selfSuppressed ? 'Self suppressed' : '';
  }
  if (bodiesEl) bodiesEl.textContent = alive ? `${bodyCount}` : '--';
  if (detectEl) detectEl.textContent = alive && target !== 0 ? formatDistance(detectCm, units) : '—';
  if (moveSigEl) moveSigEl.textContent = moveState ? formatDistance(moveCm, units) : '—';
  if (statSigEl) statSigEl.textContent = statState ? formatDistance(statCm, units) : '—';
  if (targetEl) targetEl.textContent = `${target}/3`;
}

function drawRadarScope(state) {
  const alive = Number(state.alive || 0) === 1;
  const target = Number(state.target || 0);
  const noContact = (!alive || target === 0);
  const detectCm = clamp(Number(state.detect_cm || 0), 0, radarNow.maxRangeCm);
  const moveMetric = clamp(Number(state.move_en || 0), 0, 100);
  const statMetric = clamp(Number(state.stat_en || 0), 0, 100);
  const moveCm = clamp(Number(state.move_cm || detectCm), 0, radarNow.maxRangeCm);
  const statCm = clamp(Number(state.stat_cm || detectCm), 0, radarNow.maxRangeCm);
  const moveActive = alive && (moveMetric > 0 || target === 1 || target === 3);
  const statActive = alive && (statMetric > 0 || target === 2 || target === 3);

  const stripEl = document.getElementById('range-strip');
  const detectMarkerEl = document.getElementById('range-marker-detect');
  const moveMarkerEl = document.getElementById('range-marker-move');
  const statMarkerEl = document.getElementById('range-marker-stat');
  const minRangeEl = document.getElementById('range-min-label');
  const maxRangeEl = document.getElementById('range-max-label');
  const units = currentDistanceUnit();

  if (minRangeEl) minRangeEl.textContent = units === 'm' ? '0.00m' : '0cm';
  if (maxRangeEl) {
    maxRangeEl.textContent = units === 'm'
      ? `${(radarNow.maxRangeCm / 100).toFixed(2)}m`
      : `${radarNow.maxRangeCm}cm`;
  }
  if (!stripEl || !detectMarkerEl || !moveMarkerEl || !statMarkerEl) {
    updateRadarReadout(state);
    return;
  }

  if (noContact) {
    detectMarkerEl.classList.add('hidden');
    moveMarkerEl.classList.add('hidden');
    statMarkerEl.classList.add('hidden');
  } else {
    detectMarkerEl.classList.remove('hidden');
    detectMarkerEl.style.left = markerLeft(detectCm, radarNow.maxRangeCm, 7);
    detectMarkerEl.classList.remove('pulse-fast', 'pulse-slow');
    if (moveMetric > 0) detectMarkerEl.classList.add('pulse-fast');
    else detectMarkerEl.classList.add('pulse-slow');

    if (moveActive) {
      moveMarkerEl.classList.remove('hidden');
      moveMarkerEl.style.left = markerLeft(moveCm, radarNow.maxRangeCm, 6);
    } else {
      moveMarkerEl.classList.add('hidden');
    }

    if (statActive) {
      statMarkerEl.classList.remove('hidden');
      statMarkerEl.style.left = markerLeft(statCm, radarNow.maxRangeCm, 6);
    } else {
      statMarkerEl.classList.add('hidden');
    }
  }

  updateRadarReadout(state);
}

function animateRadarScope() {
  return;
}

function renderBadgesToEl(el, trend, stats, decimals, unit) {
  if (!el || !trend) return;
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

function renderBadges(seriesKey, stats, decimals, unit) {
  const el = document.getElementById('trend-badges-' + seriesKey);
  const trend = trendSeries.find((item) => item.key === seriesKey);
  renderBadgesToEl(el, trend, stats, decimals, unit);
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
    const tableName = FRESHNESS_TABLE_BY_PREFIX[k] || '';
    const expectedKey = EXPECTED_KEY_BY_PREFIX[k] || '';
    const expectedInterval = Number(EXPECTED_INTERVALS[expectedKey] || 0);
    const ageSec = latestReady && latestReady.table_age_seconds ? Number(latestReady.table_age_seconds[tableName]) : NaN;
    const ratio = (Number.isFinite(ageSec) && expectedInterval > 0) ? (ageSec / expectedInterval) : NaN;
    let ageClass = 'bad';
    if (Number.isFinite(ratio)) {
      if (ratio < 2) ageClass = 'good';
      else if (ratio < 5) ageClass = 'warn';
    }
    const ageText = (Number.isFinite(ageSec) && Number.isFinite(ratio))
      ? ('Age: ' + ageSec.toFixed(1) + 's (' + ratio.toFixed(1) + 'x)')
      : 'Age: n/a';
    const d = document.createElement('div');
    d.className = 'card tile ' + clsFor(v);
    d.innerHTML = '<div><b>' + k + '</b></div><div>' + v + '</div><div class="fresh-age ' + ageClass + '">' + ageText + '</div>';
    root.appendChild(d);
  }
}

function renderIntegrity(data) {
  const metaEl = document.getElementById('integrityMeta');
  const fpsEl = document.getElementById('integrityFps');
  if (!metaEl || !fpsEl) return;
  const parseFail = Number(data && data.parse_fail || 0);
  const truncated = Number(data && data.truncated || 0);
  metaEl.textContent = 'parse_fail: ' + parseFail + ' | truncated: ' + truncated;
  integrityWarningActive = parseFail > 0 || truncated > 0;
  updateTickerDots();

  const windows = data && data.windows && typeof data.windows === 'object' ? data.windows : {};
  const w10 = windows['10'] && typeof windows['10'] === 'object' ? windows['10'] : {};
  const w60 = windows['60'] && typeof windows['60'] === 'object' ? windows['60'] : {};
  const c10 = w10.count && typeof w10.count === 'object' ? w10.count : {};
  const f10 = w10.fps && typeof w10.fps === 'object' ? w10.fps : {};
  const c60 = w60.count && typeof w60.count === 'object' ? w60.count : {};
  const f60 = w60.fps && typeof w60.fps === 'object' ? w60.fps : {};

  const allPrefixes = new Set([
    ...Object.keys(c10),
    ...Object.keys(f10),
    ...Object.keys(c60),
    ...Object.keys(f60),
  ]);

  let rows = Array.from(allPrefixes)
    .sort((a, b) => String(a).localeCompare(String(b)))
    .map((prefix) => {
      const tenCount = Number(c10[prefix] || 0);
      const tenFps = Number(f10[prefix] || 0);
      const sixtyCount = Number(c60[prefix] || 0);
      const sixtyFps = Number(f60[prefix] || 0);
      return [
        prefix,
        tenCount + ' (' + tenFps.toFixed(2) + '/s)',
        sixtyCount + ' (' + sixtyFps.toFixed(2) + '/s)',
      ];
    });

  if (!rows.length) {
    const legacy = data && data.fps_1m && typeof data.fps_1m === 'object' ? data.fps_1m : {};
    rows = Object.entries(legacy)
      .sort((a, b) => String(a[0]).localeCompare(String(b[0])))
      .map(([prefix, count]) => [String(prefix), 'n/a', String(count) + '/min']);
  }

  if (!rows.length) {
    fpsEl.innerHTML = '<div class="muted small" style="margin-top:6px">No fps data yet.</div>';
    return;
  }
  fpsEl.innerHTML =
    '<table class="integrity-table"><thead><tr><th>Prefix</th><th>10s</th><th>60s</th></tr></thead><tbody>' +
    rows.map(([prefix, ten, sixty]) => '<tr><td>' + escapeHtml(prefix) + '</td><td>' + escapeHtml(String(ten)) + '</td><td>' + escapeHtml(String(sixty)) + '</td></tr>').join('') +
    '</tbody></table>';
}

function summarizeTickerTransition(row) {
  if (!row || typeof row !== 'object') return 'No transitions';
  const tsText = row.ts_utc ? relativeAge(row.ts_utc) : 'n/a';
  const typeText = String(row.event_type || 'unknown').replaceAll('_', ' ');
  let detailText = '';
  if (row.detail && typeof row.detail === 'object') {
    if (Object.prototype.hasOwnProperty.call(row.detail, 'from') || Object.prototype.hasOwnProperty.call(row.detail, 'to')) {
      const from = Object.prototype.hasOwnProperty.call(row.detail, 'from') ? String(row.detail.from) : '?';
      const to = Object.prototype.hasOwnProperty.call(row.detail, 'to') ? String(row.detail.to) : '?';
      detailText = from + ' → ' + to;
    } else {
      detailText = JSON.stringify(row.detail);
    }
  } else if (row.detail != null) {
    detailText = String(row.detail);
  }
  if (detailText.length > 80) detailText = detailText.slice(0, 77) + '...';
  return tsText + ' · ' + typeText + (detailText ? (' · ' + detailText) : '');
}

async function refreshTicker() {
  if (document.visibilityState === 'hidden') {
    return;
  }
  if (tickerController) {
    tickerController.abort();
  }
  const controller = new AbortController();
  tickerController = controller;
  try {
    const data = await fetchJson('/api/state_events?limit=5', controller);
    const rows = (data && Array.isArray(data.rows)) ? data.rows : [];
    const tickerText = document.getElementById('tickerText');
    if (tickerText) {
      tickerText.textContent = rows.length ? summarizeTickerTransition(rows[0]) : 'No transitions';
    }
    tickerHealthy = true;
    updateTickerDots();
  } catch (err) {
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      const tickerText = document.getElementById('tickerText');
      if (tickerText) tickerText.textContent = 'No transitions';
      tickerHealthy = false;
      updateTickerDots();
    }
  } finally {
    if (tickerController === controller) {
      tickerController = null;
    }
  }
}

async function pollIntegrity() {
  if (document.visibilityState === 'hidden') {
    return;
  }
  if (integrityController) {
    integrityController.abort();
  }
  const controller = new AbortController();
  integrityController = controller;
  try {
    const data = await fetchJson('/api/integrity', controller);
    renderIntegrity(data || {});
  } catch (err) {
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      const metaEl = document.getElementById('integrityMeta');
      if (metaEl) metaEl.textContent = 'integrity fetch failed';
    }
  } finally {
    if (integrityController === controller) {
      integrityController = null;
    }
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
  if (!root) return;
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
  const radarCard = document.createElement('div');
  radarCard.className = 'card trend-card chart hp-card';
  radarCard.innerHTML =
    '<div class="trend-top card-header hp-head">' +
      '<div><b>' + radarTrend.title + '</b><button class="cal-btn" onclick="startRadarCalibration()">Calibration</button></div>' +
      '<div class="hp-tabs">' +
        '<button id="radar-view-now" class="hp-tab active" onclick="setRadarView(\'now\')">Now</button>' +
        '<button id="radar-view-history" class="hp-tab" onclick="setRadarView(\'history\')">History</button>' +
      '</div>' +
    '</div>' +
    '<div id="trend-badges-' + radarTrend.key + '" class="trend-badges hp-badges pill-row"></div>' +
    '<div id="trend-value-' + radarTrend.key + '" class="trend-value metric-value">n/a</div>' +
    '<div id="radar-now-pane" class="radar-now-wrap">' +
      '<div id="radar-cal-panel" class="cal-panel hidden">' +
        '<div id="radar-cal-instruction" class="cal-instruction">Clear area within 6m for 60 seconds.</div>' +
        '<div class="cal-guidance">Grade thresholds: A &lt; 5 false, B &lt; 20 false, C ≥ 20 false.</div>' +
        '<div class="cal-counters">' +
          '<div><span class="muted">Status</span><div id="radar-cal-status">idle</div></div>' +
          '<div><span class="muted">Countdown</span><div id="radar-cal-remaining">--</div></div>' +
          '<div><span class="muted">Samples</span><div id="radar-cal-samples">0</div></div>' +
        '</div>' +
        '<div class="cal-counters">' +
          '<div><span class="muted">False presence</span><div id="radar-cal-false">0</div></div>' +
          '<div><span class="muted">Baseline</span><div id="radar-cal-baseline">--</div></div>' +
          '<div><span class="muted">Noise</span><div id="radar-cal-noise">--</div></div>' +
        '</div>' +
        '<div class="cal-counters">' +
          '<div><span class="muted">Grade</span><div id="radar-cal-grade">--</div></div>' +
          '<div style="grid-column: span 2"><span class="muted">Recommendation</span><div id="radar-cal-reco">--</div></div>' +
        '</div>' +
        '<div class="cal-actions">' +
          '<button onclick="cancelRadarCalibration()">Cancel</button>' +
          '<button onclick="startRadarCalibration()">Run again</button>' +
        '</div>' +
        '<div style="margin-top:8px"><input id="radar-cal-note" type="text" placeholder="Save note (optional)" style="width:100%; padding:6px 8px; border-radius:8px; border:1px solid #26313d; background:#0f1620; color:#d9e6f3;" /></div>' +
        '<div class="cal-actions"><button onclick="saveRadarCalibrationNote()">Save note</button></div>' +
        '<div id="radar-cal-history" class="cal-history"></div>' +
      '</div>' +
      '<div id="range-strip" class="range-strip">' +
        '<div class="range-track"></div>' +
        '<div id="range-marker-detect" class="marker detect hidden"></div>' +
        '<div id="range-marker-move" class="marker move hidden"></div>' +
        '<div id="range-marker-stat" class="marker stat hidden"></div>' +
        '<div class="range-labels"><span id="range-min-label">0cm</span><span id="range-max-label">300cm</span></div>' +
      '</div>' +
      '<div class="radar-readout">' +
        '<div id="radar-now-state-pill" class="status-pill state-offline">RADAR OFFLINE</div>' +
        '<div style="margin-top:6px;display:flex;align-items:center;gap:10px">' +
          '<label class="muted" style="display:flex;align-items:center;gap:6px"><input id="radar-use-derived" type="checkbox" /> Use derived presence</label>' +
          '<span id="radar-now-self-note" class="muted" style="font-size:12px"></span>' +
        '</div>' +
        '<div id="radar-now-state" class="radar-state">Radar offline</div>' +
        '<div class="radar-line"><span class="radar-label">Bodies</span><span id="radar-now-bodies">--</span></div>' +
        '<div class="radar-line"><span class="radar-label">Detect</span><span id="radar-now-detect">--</span></div>' +
        '<div class="radar-line"><span class="radar-label">Motion signature</span><span id="radar-now-move-sig">--</span></div>' +
        '<div class="radar-line"><span class="radar-label">Still signature</span><span id="radar-now-stat-sig">--</span></div>' +
        '<div class="radar-line"><span class="radar-label">Target</span><span id="radar-now-target">0/3</span></div>' +
      '</div>' +
    '</div>' +
    '<div id="radar-history-pane" class="hidden">' +
      '<div class="chart-wrap plot">' +
        '<img id="trend-img-' + radarTrend.key + '" class="trend-img" data-trend-key="' + radarTrend.key + '" alt="' + radarTrend.title + ' trend" src="" />' +
      '</div>' +
    '</div>';
  root.appendChild(radarCard);

  for (const slot of chartSlotOrder) {
    const trend = getTrendByKey(chartSlots[slot]);
    const card = document.createElement('div');
    card.className = 'card trend-card chart';
    card.dataset.slot = slot;
    card.dataset.trendKey = trend.key;
    card.innerHTML =
      '<div class="trend-top card-header">' +
        '<div><b id="trend-title-slot-' + slot + '">' + trend.title + '</b></div>' +
        '<div id="trend-badges-slot-' + slot + '" class="trend-badges pill-row"></div>' +
      '</div>' +
      '<div id="trend-value-slot-' + slot + '" class="trend-value metric-value">n/a</div>' +
      '<div class="chart-wrap plot">' +
        '<img id="trend-img-slot-' + slot + '" class="trend-img" data-trend-key="' + trend.key + '" alt="' + trend.title + ' trend" src="" />' +
      '</div>';
    root.appendChild(card);
  }
  radarViewButtonsActive();
  initChartResizeObserver();
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
  if (tableName === 'esp_net') {
    const rssiEl = document.getElementById('top-rssi');
    const rssiBoxEl = document.getElementById('rssiBox');
    if (rssiEl) {
      const latest = Array.isArray(rows) && rows.length ? rows[0] : null;
      const rssi = latest ? Number(latest.rssi) : NaN;
      const wifist = latest ? Number(latest.wifist) : NaN;
      if (rssiBoxEl) {
        rssiBoxEl.classList.remove('rssi-good', 'rssi-med', 'rssi-poor');
      }
      if (!Number.isFinite(rssi) || rssi === 999 || rssi > 0 || rssi < -130) {
        rssiEl.textContent = 'n/a';
        if (rssiBoxEl) rssiBoxEl.classList.add('rssi-poor');
      } else if (Number.isFinite(wifist) && wifist !== 1 && wifist !== 3) {
        rssiEl.textContent = 'offline';
        if (rssiBoxEl) rssiBoxEl.classList.add('rssi-poor');
      } else {
        rssiEl.textContent = Math.round(rssi) + ' dBm';
        if (rssiBoxEl) {
          if (rssi >= -65) rssiBoxEl.classList.add('rssi-good');
          else if (rssi >= -80) rssiBoxEl.classList.add('rssi-med');
          else rssiBoxEl.classList.add('rssi-poor');
        }
      }
    }
  }
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

    const daemonEl = document.getElementById('daemon');
    if (daemonEl) daemonEl.innerText = status.daemon_running ? 'running' : 'down';
    const portEl = document.getElementById('port');
    if (portEl) portEl.innerText = status.port || 'unknown';
    const linesEl = document.getElementById('lines');
    if (linesEl) linesEl.innerText = status.lines_in || 'unknown';
    const errorEl = document.getElementById('error');
    if (errorEl) errorEl.innerText = status.last_error || 'unknown';

    renderFreshness(health.freshness || {});
    const rawHealthEl = document.getElementById('rawHealth');
    if (rawHealthEl) rawHealthEl.innerText = health.raw || '';
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
    const requestedKeys = [radarTrend.key, ...chartSlotOrder.map((slot) => getTrendByKey(chartSlots[slot]).key)];
    const uniqueKeys = [...new Set(requestedKeys)];
    const results = await Promise.all(
      uniqueKeys.map((key) => fetchJson('/api/ts/' + key + '?minutes=' + trendMinutes, controller).then((data) => [key, data]))
    );
    const trendData = {};
    for (const [key, data] of results) {
      trendData[key] = data;
    }
    const radarLatest = await fetchJson('/api/radar/latest', controller);
    const derivedToggleEl = document.getElementById('radar-use-derived');
    if (derivedToggleEl) {
      useDerivedPresence = !!derivedToggleEl.checked;
    }
    radarNow.state = radarLatest || radarNow.state;
    drawRadarScope(radarNow.state);

    const cacheBust = Date.now();
    {
      const trend = radarTrend;
      const data = trendData[trend.key] || { points: [], stats: null };
      const points = data.points || [];
      const latest = points.length ? points[points.length - 1].v : null;
      const valueEl = document.getElementById('trend-value-' + trend.key);
      const stale = tableIsStale(trend.table) || !points.length || !data.stats;
      if (valueEl) {
        valueEl.textContent = latest === null ? 'n/a' : (formatMetric(latest, trend.decimals) + ' ' + trend.unit);
      }
      renderBadges(trend.key, data.stats || null, trend.decimals, ' ' + trend.unit);

      const imgEl = document.getElementById('trend-img-' + trend.key);
      const card = imgEl ? imgEl.closest('.trend-card') : null;
      if (imgEl) {
        imgEl.classList.toggle('stale', stale);
        imgEl.dataset.trendKey = trend.key;
        setTrendImageSrc(imgEl, trend.key, cacheBust);
      }
      if (card) {
        card.classList.toggle('stale-card', stale);
      }
    }

    for (const slot of chartSlotOrder) {
      const trend = getTrendByKey(chartSlots[slot]);
      const data = trendData[trend.key] || { points: [], stats: null };
      const points = data.points || [];
      const latest = points.length ? points[points.length - 1].v : null;
      const valueEl = document.getElementById('trend-value-slot-' + slot);
      const stale = tableIsStale(trend.table) || !points.length || !data.stats;
      if (valueEl) {
        valueEl.textContent = latest === null ? 'n/a' : (formatMetric(latest, trend.decimals) + ' ' + trend.unit);
      }
      const badgesEl = document.getElementById('trend-badges-slot-' + slot);
      renderBadgesToEl(badgesEl, trend, data.stats || null, trend.decimals, ' ' + trend.unit);

      const titleEl = document.getElementById('trend-title-slot-' + slot);
      if (titleEl) titleEl.textContent = trend.title;

      const imgEl = document.getElementById('trend-img-slot-' + slot);
      const card = imgEl ? imgEl.closest('.trend-card') : null;
      if (imgEl) {
        imgEl.classList.toggle('stale', stale);
        imgEl.alt = trend.title + ' trend';
        imgEl.dataset.trendKey = trend.key;
        setTrendImageSrc(imgEl, trend.key, cacheBust);
      }
      if (card) {
        card.classList.toggle('stale-card', stale);
      }
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
  const hasTrends = !!document.getElementById('trends');
  const hasTables = !!document.getElementById('tables');
  const hasEvents = !!document.getElementById('events-body');
  const hasIntegrity = !!document.getElementById('integrityFps');
  const hasTicker = !!document.getElementById('navTicker');

  if (hasEvents) {
    initEvents();
  }
  if (hasTrends) {
    chartSlots = loadChartSlots();
    await loadDashboardSettings();
    renderChartSlotControls();
    initTrends();
    primeTrendImages();
    bindPresenceToggleUi();
    setTrendMinutes(60);
    await loadRadarCalibrationHistory();
    await refreshDashboardSettingsLight();
  }
  if (hasTables) {
    initTables();
  }

  await pollReady();
  await pollStatus();
  if (hasTables) {
    await pollTables();
  }
  if (hasEvents) {
    await pollEvents();
  }
  if (hasIntegrity) {
    await pollIntegrity();
  }
  if (hasTicker) {
    await refreshTicker();
  }

  setInterval(pollStatus, 1000);
  setInterval(pollReady, 3000);
  if (hasTables) {
    setInterval(pollTables, 3000);
  }
  if (hasTrends) {
    setInterval(pollTrends, 7000);
    setInterval(refreshDashboardSettingsLight, 5000);
  }
  if (hasEvents) {
    setInterval(pollEvents, 4000);
  }
  if (hasIntegrity) {
    setInterval(pollIntegrity, 5000);
  }
  if (hasTicker) {
    setInterval(refreshTicker, 4000);
  }
  setInterval(refreshRelativeTimes, 1000);
  setInterval(refreshLastUpdatedLabel, 1000);
})();
"""


@APP.get("/app.js")
def app_js() -> Response:
    return Response(JS_BUNDLE, media_type="application/javascript")


@APP.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
  return HTMLResponse(render_dashboard_page("/"))


@APP.get("/history", response_class=HTMLResponse)
def history_page() -> HTMLResponse:
  return HTMLResponse(render_history_page())


@APP.get("/events", response_class=HTMLResponse)
def events_page() -> HTMLResponse:
  return HTMLResponse(render_dashboard_page("/events"))


@APP.get("/analytics", response_class=HTMLResponse)
def analytics_page() -> HTMLResponse:
  return HTMLResponse(render_analytics_page())


@APP.get("/calibration", response_class=HTMLResponse)
def calibration_page() -> HTMLResponse:
  return HTMLResponse(render_calibration_page())


@APP.get("/reports", response_class=HTMLResponse)
def reports_page() -> HTMLResponse:
  return HTMLResponse(render_reports_page())


@APP.get("/settings", response_class=HTMLResponse)
def settings_page() -> HTMLResponse:
  return HTMLResponse(render_settings_page())


@APP.get("/field", response_class=HTMLResponse)
def field_page() -> HTMLResponse:
  return HTMLResponse(render_field_page())


@APP.get("/healthz")
def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@APP.get("/health")
def health() -> Dict[str, str]:
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
