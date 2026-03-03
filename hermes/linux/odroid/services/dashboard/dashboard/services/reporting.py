from __future__ import annotations

import base64
import datetime
import json
import time
from pathlib import Path
from typing import Dict

from fastapi import HTTPException

from legacy_app import (
  DB_PATH,
  DB_TIMEOUT_SECS,
  REPORTS_DIR,
  RSSI_NOT_CONNECTED,
  SETTINGS_DEFAULTS,
  STATIC_DIR,
  VALID_CHIME_KEYS,
  api_health,
  ensure_analytics_indexes,
  ensure_events_table,
  estimate_radar_sample_seconds,
  get_settings_payload,
  radar_present_sql_predicate,
  render_shell_page,
)

from ..db.connect import open_db
from ..db.queries import parse_iso8601_utc, resolve_range_utc_from_request
from ..db.reports import get_report, insert_report, list_reports as db_list_reports, ensure_reports_table, update_report_output


def report_response(ok: bool, **extra: object) -> Dict[str, object]:
  out = {"ok": bool(ok)}
  out.update(extra)
  return out


def now_utc_iso() -> str:
  return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def now_local_iso() -> str:
  return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


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
  sections = []

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
  <div class=\"report-brand\"><img src=\"__REPORT_LOGO_SRC__\" alt=\"HERMES logo\" /></div>
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


def resolve_range_or_http(*, preset: str, start_local: str | None, end_local: str | None) -> Dict[str, str]:
  try:
    return resolve_range_utc_from_request(
      preset=preset,
      start_local=start_local,
      end_local=end_local,
      max_days=31,
    )
  except ValueError as exc:
    detail = str(exc)
    if detail == "invalid preset":
      raise HTTPException(status_code=400, detail="invalid preset")
    if detail == "custom range requires start_local and end_local":
      raise HTTPException(status_code=400, detail="custom range requires start_local and end_local")
    if detail.startswith("range exceeds"):
      raise HTTPException(status_code=400, detail=detail)
    if detail == "end must be after start":
      raise HTTPException(status_code=400, detail="end must be after start")
    raise HTTPException(status_code=400, detail=f"invalid custom datetime: {detail}")


def api_reports_generate(payload: Dict[str, object]) -> Dict[str, object]:
  preset = str(payload.get("preset") or "24h")
  start_local = payload.get("start_local")
  end_local = payload.get("end_local")
  include = payload.get("include") if isinstance(payload.get("include"), dict) else {}
  include_presence = bool(include.get("presence", True))
  include_air = bool(include.get("air", True))
  include_events = bool(include.get("events", True))
  include_rssi = bool(include.get("rssi", False))

  range_info = resolve_range_or_http(
    preset=preset,
    start_local=str(start_local) if start_local is not None else None,
    end_local=str(end_local) if end_local is not None else None,
  )

  REPORTS_DIR.mkdir(parents=True, exist_ok=True)
  with open_db(DB_PATH, DB_TIMEOUT_SECS) as conn:
    ensure_reports_table(conn)
    ensure_analytics_indexes(conn)
    ensure_events_table(conn)
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
    report_id = insert_report(
      conn,
      ts_utc=now_utc_iso(),
      ts_local=now_local_iso(),
      range_start_utc=range_info["start_utc"],
      range_end_utc=range_info["end_utc"],
      preset=range_info["preset"],
      options_json=options_json,
      file_path="",
      status="running",
      notes="",
    )
    conn.commit()

    settings_payload = get_settings_payload(conn, SETTINGS_DEFAULTS, VALID_CHIME_KEYS)
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
    update_report_output(conn, report_id, file_path=str(out_path), status="done", notes="")
    conn.commit()

  return {
    "report_id": report_id,
    "status": "done",
    "download_url": f"/api/reports/{report_id}/download",
  }


def api_reports_list(limit: int) -> Dict[str, object]:
  with open_db(DB_PATH, DB_TIMEOUT_SECS) as conn:
    rows = db_list_reports(conn, int(limit))
  out = []
  for row in rows:
    item = dict(row)
    item["download_url"] = f"/api/reports/{item['id']}/download"
    out.append(item)
  return {"rows": out}


def resolve_report_download_path(report_id: int) -> Path:
  with open_db(DB_PATH, DB_TIMEOUT_SECS) as conn:
    row = get_report(conn, int(report_id))
  if not row:
    raise HTTPException(status_code=404, detail="report not found")
  if str(row.get("status") or "") != "done":
    raise HTTPException(status_code=409, detail="report not ready")
  file_path = Path(str(row.get("file_path") or ""))
  if not file_path.exists() or not file_path.is_file():
    raise HTTPException(status_code=404, detail="report file missing")
  return file_path
