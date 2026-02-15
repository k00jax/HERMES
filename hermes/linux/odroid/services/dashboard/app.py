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

from fastapi import FastAPI, HTTPException, Query
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
    allow_methods=["GET"],
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
  </style>
</head>
<body>
  <h1>HERMES Dashboard</h1>
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
  { key: 'air_eco2', title: 'ECO2', unit: 'ppm', decimals: 0 },
  { key: 'env_temp', title: 'Temp', unit: '°C', decimals: 1 },
  { key: 'env_hum', title: 'Humidity', unit: '%', decimals: 1 },
  { key: 'esp_rssi', title: 'RSSI', unit: 'dBm', decimals: 0 },
];
const tableState = {};
const tableRowLimit = {};
let statusController = null;
let tableController = null;
let trendController = null;
let lastUpdatedMs = 0;
let trendMinutes = 60;

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
  if (!stats) {
    el.innerHTML = '<span class="badge">no data</span>';
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
  titleWrap.appendChild(title);
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

  rowsSelect.onchange = () => {
    const parsed = Number.parseInt(rowsSelect.value, 10);
    tableRowLimit[tableName] = [20, 50, 200].includes(parsed) ? parsed : 20;
    refreshOneTable(tableName);
  };

  header.appendChild(titleWrap);
  actions.appendChild(rowsLabel);
  actions.appendChild(rowsSelect);
  actions.appendChild(copyBtn);
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
    noRows,
    table,
    headRow,
    tbody,
    copyBtn,
    keys: [],
    rowEls: new Map(),
    rowData: new Map(),
    displayKeys: [],
    maxId: null,
  };
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

function applyTableRows(tableName, rows, force = false) {
  const state = tableState[tableName];
  const label = (tableLabels[tableName] || tableName);
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

function fetchOneTable(tableName, controller = null) {
  const limit = tableRowLimit[tableName] || 20;
  return fetchJson('/api/latest/' + tableName + '?limit=' + limit, controller || new AbortController());
}

async function refreshOneTable(tableName) {
  if (document.visibilityState === 'hidden') {
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
    const results = await Promise.all(
      tables.map((t) => fetchOneTable(t, controller).then((data) => [t, data]))
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
      valueEl.textContent = latest === null ? 'n/a' : (formatMetric(latest, trend.decimals) + ' ' + trend.unit);
      renderBadges(trend.key, data.stats || null, trend.decimals, ' ' + trend.unit);

      const imgEl = document.getElementById('trend-img-' + trend.key);
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

(async () => {
  initTrends();
  setTrendMinutes(60);
  initTables();
  await pollStatus();
  await pollTables();
  setInterval(pollStatus, 1000);
  setInterval(pollTables, 3000);
  setInterval(pollTrends, 7000);
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
