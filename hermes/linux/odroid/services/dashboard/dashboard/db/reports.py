from __future__ import annotations

import sqlite3
from typing import Dict, List


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


def list_reports(conn: sqlite3.Connection, limit: int = 100) -> List[Dict[str, object]]:
  ensure_reports_table(conn)
  rows = conn.execute(
    """
    SELECT id, ts_utc, ts_local, range_start_utc, range_end_utc, preset, options_json, file_path, status, notes
    FROM reports
    ORDER BY id DESC
    LIMIT ?
    """,
    (int(limit),),
  ).fetchall()
  return [dict(row) for row in rows]
