from __future__ import annotations

import sqlite3
from typing import Dict, List

from .queries import require_time_window


def fetch_env_window(conn: sqlite3.Connection, start_ts: str, end_ts: str, limit: int = 2000) -> List[Dict[str, object]]:
  start_ts, end_ts = require_time_window(start_ts, end_ts)
  rows = conn.execute(
    """
    SELECT ts_utc, temp_c, hum_pct
    FROM env
    WHERE ts_utc >= ? AND ts_utc <= ?
    ORDER BY ts_utc DESC
    LIMIT ?
    """,
    (start_ts, end_ts, int(limit)),
  ).fetchall()
  return [dict(row) for row in rows]
