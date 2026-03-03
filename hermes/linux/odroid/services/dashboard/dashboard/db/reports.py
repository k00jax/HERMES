from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional


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


def insert_report(
  conn: sqlite3.Connection,
  *,
  ts_utc: str,
  ts_local: str,
  range_start_utc: str,
  range_end_utc: str,
  preset: str,
  options_json: str,
  file_path: str,
  status: str,
  notes: str,
) -> int:
  ensure_reports_table(conn)
  cur = conn.execute(
    """
    INSERT INTO reports (
      ts_utc, ts_local, range_start_utc, range_end_utc, preset, options_json, file_path, status, notes
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
    (
      ts_utc,
      ts_local,
      range_start_utc,
      range_end_utc,
      preset,
      options_json,
      file_path,
      status,
      notes,
    ),
  )
  return int(cur.lastrowid or 0)


def update_report_output(conn: sqlite3.Connection, report_id: int, *, file_path: str, status: str, notes: str) -> None:
  ensure_reports_table(conn)
  conn.execute(
    "UPDATE reports SET file_path=?, status=?, notes=? WHERE id=?",
    (file_path, status, notes, int(report_id)),
  )


def get_report(conn: sqlite3.Connection, report_id: int) -> Optional[Dict[str, object]]:
  ensure_reports_table(conn)
  row = conn.execute(
    "SELECT id, file_path, status FROM reports WHERE id=?",
    (int(report_id),),
  ).fetchone()
  if not row:
    return None
  return dict(row)
