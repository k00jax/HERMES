from __future__ import annotations

import sqlite3
from pathlib import Path

def open_db(db_path: Path, timeout_secs: float) -> sqlite3.Connection:
  conn = sqlite3.connect(db_path, timeout=float(timeout_secs))
  conn.execute("PRAGMA journal_mode=WAL;")
  conn.execute("PRAGMA synchronous=NORMAL;")
  conn.row_factory = sqlite3.Row
  return conn
