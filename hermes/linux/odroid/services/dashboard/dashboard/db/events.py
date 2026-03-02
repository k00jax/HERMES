from __future__ import annotations

import sqlite3
from typing import Dict, List

from .queries import require_time_window


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


def fetch_events_window(conn: sqlite3.Connection, start_ts: str, end_ts: str, limit: int = 2000) -> List[Dict[str, object]]:
  start_ts, end_ts = require_time_window(start_ts, end_ts)
  rows = conn.execute(
    """
    SELECT id, ts_utc, ts_local, kind, severity, source, message, data_json, dedupe_key
    FROM events
    WHERE ts_utc >= ? AND ts_utc <= ?
    ORDER BY id DESC
    LIMIT ?
    """,
    (start_ts, end_ts, int(limit)),
  ).fetchall()
  return [dict(row) for row in rows]
