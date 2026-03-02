from __future__ import annotations

import datetime
import math
import sqlite3
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .connect import open_db

MAX_RANGE_DAYS = 31


def parse_iso8601_utc(raw: str) -> datetime.datetime:
  text = str(raw or "").strip()
  if text.endswith("Z"):
    text = text[:-1] + "+00:00"
  dt = datetime.datetime.fromisoformat(text)
  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=datetime.timezone.utc)
  return dt.astimezone(datetime.timezone.utc)


def cutoff_iso(minutes: int) -> str:
  dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=int(minutes))
  return dt.isoformat()


def require_time_window(start_ts: str, end_ts: str, max_days: int = MAX_RANGE_DAYS) -> Tuple[str, str]:
  if not str(start_ts or "").strip() or not str(end_ts or "").strip():
    raise ValueError("start_ts and end_ts are required")
  start_raw = str(start_ts).strip()
  end_raw = str(end_ts).strip()
  start_dt = parse_iso8601_utc(start_raw)
  end_dt = parse_iso8601_utc(end_raw)
  if end_dt <= start_dt:
    raise ValueError("end must be after start")
  if (end_dt - start_dt) > datetime.timedelta(days=max(1, int(max_days))):
    raise ValueError(f"range exceeds {int(max_days)} days")
  return start_raw, end_raw


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
  max_days: int = MAX_RANGE_DAYS,
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
      raise ValueError("custom range requires start_local and end_local")
    start = parse_local_datetime_input(start_local)
    end = parse_local_datetime_input(end_local)
  else:
    raise ValueError("invalid preset")

  require_time_window(start.astimezone(datetime.timezone.utc).isoformat(), end.astimezone(datetime.timezone.utc).isoformat(), max_days=max_days)

  start_utc = start.astimezone(datetime.timezone.utc)
  end_utc = end.astimezone(datetime.timezone.utc)
  return {
    "preset": preset_norm,
    "start_local": start.isoformat(),
    "end_local": end.isoformat(),
    "start_utc": start_utc.isoformat(),
    "end_utc": end_utc.isoformat(),
  }


def query_series_points(
  *,
  db_path: Path,
  timeout_secs: float,
  series_map: Dict[str, Dict[str, object]],
  series: str,
  minutes: int,
  downsample: Callable[[List[Dict[str, object]], int], List[Dict[str, object]]],
  on_locked: Optional[Callable[[], None]] = None,
) -> List[Dict[str, object]]:
  cfg = series_map.get(series)
  if not cfg:
    raise KeyError("series not allowed")
  if not db_path.exists():
    return []

  now_utc = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
  start_utc = now_utc - datetime.timedelta(minutes=int(minutes))
  start_ts, end_ts = require_time_window(start_utc.isoformat(), now_utc.isoformat())

  sql = (
    f"SELECT ts_utc, {cfg['column']} "
    f"FROM {cfg['table']} "
    f"WHERE ts_utc >= ? AND ts_utc <= ? AND {cfg['column']} IS NOT NULL "
    f"ORDER BY ts_utc ASC"
  )

  points: List[Dict[str, object]] = []
  try:
    with open_db(db_path, timeout_secs) as conn:
      rows = conn.execute(sql, (start_ts, end_ts)).fetchall()
  except sqlite3.OperationalError as exc:
    msg = str(exc).lower()
    if "no such table" in msg:
      return []
    if "locked" in msg:
      if on_locked is not None:
        on_locked()
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

  return downsample(points, 300)
