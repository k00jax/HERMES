"""
Normalizer: SQLite sensor tables → list[HomeEvent].

Responsibilities
----------------
- Open the HERMES SQLite database read-only.
- Query the last `window_minutes` of data from each sensor table.
- Convert each row into a HomeEvent, preserving the raw field values
  and recording a raw_ref pointer back to the source row.
- Return a flat, chronologically-sorted list of HomeEvents.
- Never write to the database.
- Survive gracefully when tables are empty, missing, or the DB file does
  not exist yet (first boot scenario).

What this module does NOT do
-----------------------------
- Interpret values (no "high CO2" flags here — that is the scorer's job).
- Deduplicate rows across pipeline runs (that is the context_store's job).
- Filter by salience (that is the scorer's job).

Raw-ref format
--------------
Every sensor-derived HomeEvent carries:

    raw_ref = "{table}:{rowid}"

e.g.  "env:4291",  "radar:88201"

rowid is SQLite's built-in integer row identifier.  It is stable for the
lifetime of the row and is available on every table regardless of whether an
explicit INTEGER PRIMARY KEY column was declared.  We select it as "rowid"
in each query.  This gives a reversible, no-copy pointer to the exact source
record.
"""
from __future__ import annotations

import datetime
import logging
import sqlite3
from pathlib import Path
from typing import List, Optional

from .types import (
    HomeEvent,
    KIND_CO2,
    KIND_HEARTBEAT,
    KIND_HUMIDITY,
    KIND_PRESENCE,
    KIND_TEMPERATURE,
    KIND_VOC,
    SOURCE_AIR,
    SOURCE_ENV,
    SOURCE_HB,
    SOURCE_RADAR,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _cutoff_iso(window_minutes: int) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=window_minutes)
    return dt.isoformat()


def _open_db_readonly(db_path: Path, timeout: float = 5.0) -> sqlite3.Connection:
    """
    Open the HERMES SQLite database in read-only URI mode.

    WAL mode is already set by the logger daemon, so concurrent reads from
    this process are safe without any additional PRAGMA.
    """
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=timeout)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Per-table fetch functions
# Each returns a list[HomeEvent] and never raises on missing/empty tables.
# ---------------------------------------------------------------------------

def _fetch_env(conn: sqlite3.Connection, cutoff: str, ingested_at: str) -> List[HomeEvent]:
    if not _table_exists(conn, "env"):
        return []
    rows = conn.execute(
        "SELECT rowid, ts_utc, temp_c, hum_pct FROM env WHERE ts_utc >= ? ORDER BY ts_utc ASC",
        (cutoff,),
    ).fetchall()
    events: List[HomeEvent] = []
    for row in rows:
        ts = str(row["ts_utc"])
        raw_ref = f"env:{row['rowid']}"
        if row["temp_c"] is not None:
            events.append(HomeEvent(
                ts_utc=ts,
                source=SOURCE_ENV,
                kind=KIND_TEMPERATURE,
                value={"temp_c": float(row["temp_c"])},
                raw_ref=raw_ref,
                ingested_at=ingested_at,
            ))
        if row["hum_pct"] is not None:
            events.append(HomeEvent(
                ts_utc=ts,
                source=SOURCE_ENV,
                kind=KIND_HUMIDITY,
                value={"hum_pct": float(row["hum_pct"])},
                raw_ref=raw_ref,
                ingested_at=ingested_at,
            ))
    return events


def _fetch_air(conn: sqlite3.Connection, cutoff: str, ingested_at: str) -> List[HomeEvent]:
    if not _table_exists(conn, "air"):
        return []
    rows = conn.execute(
        "SELECT rowid, ts_utc, eco2_ppm, tvoc_ppb FROM air WHERE ts_utc >= ? ORDER BY ts_utc ASC",
        (cutoff,),
    ).fetchall()
    events: List[HomeEvent] = []
    for row in rows:
        ts = str(row["ts_utc"])
        raw_ref = f"air:{row['rowid']}"
        if row["eco2_ppm"] is not None:
            events.append(HomeEvent(
                ts_utc=ts,
                source=SOURCE_AIR,
                kind=KIND_CO2,
                value={"eco2_ppm": float(row["eco2_ppm"])},
                raw_ref=raw_ref,
                ingested_at=ingested_at,
            ))
        if row["tvoc_ppb"] is not None:
            events.append(HomeEvent(
                ts_utc=ts,
                source=SOURCE_AIR,
                kind=KIND_VOC,
                value={"tvoc_ppb": float(row["tvoc_ppb"])},
                raw_ref=raw_ref,
                ingested_at=ingested_at,
            ))
    return events


def _fetch_radar(conn: sqlite3.Connection, cutoff: str, ingested_at: str) -> List[HomeEvent]:
    if not _table_exists(conn, "radar"):
        return []
    rows = conn.execute(
        """
        SELECT rowid, ts_utc, alive, target, detect_cm, move_cm, stat_cm
        FROM radar
        WHERE ts_utc >= ?
        ORDER BY ts_utc ASC
        """,
        (cutoff,),
    ).fetchall()
    events: List[HomeEvent] = []
    for row in rows:
        ts = str(row["ts_utc"])
        raw_ref = f"radar:{row['rowid']}"
        # Preserve all fields; let scorer/builder decide what matters.
        value: dict = {}
        for col in ("alive", "target", "detect_cm", "move_cm", "stat_cm"):
            v = row[col]
            if v is not None:
                try:
                    value[col] = float(v)
                except (TypeError, ValueError):
                    value[col] = v
        if value:
            events.append(HomeEvent(
                ts_utc=ts,
                source=SOURCE_RADAR,
                kind=KIND_PRESENCE,
                value=value,
                raw_ref=raw_ref,
                ingested_at=ingested_at,
            ))
    return events


def _fetch_hb(conn: sqlite3.Connection, cutoff: str, ingested_at: str) -> List[HomeEvent]:
    if not _table_exists(conn, "hb"):
        return []
    rows = conn.execute(
        "SELECT rowid, ts_utc, tick_ms, seq FROM hb WHERE ts_utc >= ? ORDER BY ts_utc ASC",
        (cutoff,),
    ).fetchall()
    events: List[HomeEvent] = []
    for row in rows:
        ts = str(row["ts_utc"])
        raw_ref = f"hb:{row['rowid']}"
        value: dict = {}
        for col in ("tick_ms", "seq"):
            v = row[col]
            if v is not None:
                try:
                    value[col] = int(v)
                except (TypeError, ValueError):
                    value[col] = v
        if value:
            events.append(HomeEvent(
                ts_utc=ts,
                source=SOURCE_HB,
                kind=KIND_HEARTBEAT,
                value=value,
                raw_ref=raw_ref,
                ingested_at=ingested_at,
            ))
    return events


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def normalize(
    db_path: Path,
    window_minutes: int = 5,
    include_heartbeats: bool = False,
) -> List[HomeEvent]:
    """
    Read the last `window_minutes` of sensor data from SQLite and return a
    chronologically-sorted list of HomeEvents.

    Parameters
    ----------
    db_path
        Path to the HERMES SQLite database file.
    window_minutes
        How far back to query.  Should match the pipeline's configured window.
    include_heartbeats
        Heartbeats are high-frequency (1 Hz) and carry no home-state signal on
        their own.  Excluded by default to keep candidate bundles lean.  Pass
        True to include them (useful for diagnosing serial health).

    Returns
    -------
    List[HomeEvent]
        Flat list sorted by ts_utc ascending.  May be empty if the database
        does not exist, tables are empty, or no rows fall within the window.
    """
    if not db_path.exists():
        log.warning("normalizer: db not found at %s — returning empty", db_path)
        return []

    cutoff = _cutoff_iso(window_minutes)
    ingested_at = _utc_now_iso()

    try:
        conn = _open_db_readonly(db_path)
    except Exception as exc:
        log.error("normalizer: could not open db at %s: %s", db_path, exc)
        return []

    try:
        events: List[HomeEvent] = []
        events.extend(_fetch_env(conn, cutoff, ingested_at))
        events.extend(_fetch_air(conn, cutoff, ingested_at))
        events.extend(_fetch_radar(conn, cutoff, ingested_at))
        if include_heartbeats:
            events.extend(_fetch_hb(conn, cutoff, ingested_at))

        # Sort chronologically by the sensor's own timestamp.
        events.sort(key=lambda e: e.ts_utc)

        log.debug(
            "normalizer: window=%dm cutoff=%s events=%d",
            window_minutes, cutoff, len(events),
        )
        return events

    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "locked" in msg:
            log.warning("normalizer: db locked — skipping this run")
        else:
            log.error("normalizer: db error: %s", exc)
        return []
    except Exception as exc:
        log.error("normalizer: unexpected error: %s", exc)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
