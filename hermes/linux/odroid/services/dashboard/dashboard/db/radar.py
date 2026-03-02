from __future__ import annotations

import datetime
import statistics
import sqlite3
from typing import Callable, Dict, List, Optional

from .queries import require_time_window


def fetch_radar_window(conn: sqlite3.Connection, start_ts: str, end_ts: str, limit: int = 2000) -> List[Dict[str, object]]:
  start_ts, end_ts = require_time_window(start_ts, end_ts)
  rows = conn.execute(
    """
    SELECT ts_utc, alive, target, detect_cm, move_cm, stat_cm, move_en, stat_en
    FROM radar
    WHERE ts_utc >= ? AND ts_utc <= ?
    ORDER BY ts_utc DESC
    LIMIT ?
    """,
    (start_ts, end_ts, int(limit)),
  ).fetchall()
  return [dict(row) for row in rows]


def summarize_radar_calibration_window(
  conn: sqlite3.Connection,
  *,
  start_ts_utc: str,
  end_ts_utc: str,
  duration_s: int,
  max_range_cm: int,
  now_utc_iso: Callable[[], str],
) -> Dict[str, object]:
  start_ts_utc, end_ts_utc = require_time_window(start_ts_utc, end_ts_utc)
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
  return {
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
