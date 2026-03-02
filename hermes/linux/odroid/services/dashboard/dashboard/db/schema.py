from __future__ import annotations

import sqlite3


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
      message TEXT,
      data_json TEXT,
      dedupe_key TEXT
    )
    """
  )


def ensure_settings_table(conn: sqlite3.Connection) -> None:
  conn.execute(
    """
    CREATE TABLE IF NOT EXISTS settings (
      key TEXT PRIMARY KEY,
      value_json TEXT NOT NULL,
      updated_ts_utc TEXT NOT NULL
    )
    """
  )


def ensure_reports_table(conn: sqlite3.Connection) -> None:
  conn.execute(
    """
    CREATE TABLE IF NOT EXISTS reports (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_ts_utc TEXT NOT NULL,
      start_ts_utc TEXT,
      end_ts_utc TEXT,
      file_path TEXT,
      status TEXT,
      notes TEXT
    )
    """
  )
