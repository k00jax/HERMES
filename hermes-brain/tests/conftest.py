"""
Shared fixtures and factories for the home-AI pipeline test suite.

Factories are plain functions, not pytest fixtures, so they can be called
with different arguments inside the same test without fixture scoping
complications.  The one true pytest fixture here is db_path, which needs
tmp_path for tempfile management.
"""
from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from app.pipeline.types import (
    HomeEvent,
    MemoryCandidate,
    SCHEMA_VERSION,
    SOURCE_ENV,
    KIND_TEMPERATURE,
)

# ---------------------------------------------------------------------------
# Fixed reference epoch used by builder tests so bucket math is predictable.
# Bucket indices are derived at import time to avoid hard-coding epoch math.
# ---------------------------------------------------------------------------
REF_TS = "2026-01-01T12:05:30+00:00"

def _ts_to_epoch(ts: str) -> float:
    text = ts.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()

_REF_EPOCH = _ts_to_epoch(REF_TS)
REF_BUCKET_IDX_300 = int(_REF_EPOCH // 300)
REF_BUCKET_IDX_600 = int(_REF_EPOCH // 600)


def make_event(
    *,
    ts_utc: str = REF_TS,
    source: str = SOURCE_ENV,
    kind: str = KIND_TEMPERATURE,
    value: Optional[Dict[str, Any]] = None,
    raw_ref: Optional[str] = "env:1",
    ingested_at: str = "2026-01-01T12:05:31+00:00",
) -> HomeEvent:
    """Minimal HomeEvent factory.  All fields have sensible defaults."""
    return HomeEvent(
        ts_utc=ts_utc,
        source=source,
        kind=kind,
        value=value if value is not None else {"temp_c": 21.0},
        raw_ref=raw_ref,
        ingested_at=ingested_at,
    )


def make_candidate(
    *,
    candidate_id: str = "w300_5785765",
    ts_start: str = "2026-01-01T12:05:00+00:00",
    ts_end: str = "2026-01-01T12:10:00+00:00",
    events: Optional[List[HomeEvent]] = None,
    source_mix: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    salience: Optional[float] = None,
    summary: Optional[str] = None,
    escalate: bool = False,
    provenance: Optional[Dict[str, Any]] = None,
) -> MemoryCandidate:
    """Minimal MemoryCandidate factory."""
    if events is None:
        events = [make_event()]
    return MemoryCandidate(
        candidate_id=candidate_id,
        ts_start=ts_start,
        ts_end=ts_end,
        events=events,
        source_mix=source_mix if source_mix is not None else [SOURCE_ENV],
        tags=tags if tags is not None else [],
        salience=salience,
        summary=summary,
        escalate=escalate,
        provenance=provenance if provenance is not None else {
            "schema_version": SCHEMA_VERSION,
            "pipeline_version": "0.1.0",
            "created_at": "2026-01-01T12:05:31+00:00",
            "window_sec": 300,
            "event_count": 1,
        },
    )


# ---------------------------------------------------------------------------
# Normalizer fixture: a real SQLite file (not :memory:) so normalize() can
# open it via the read-only URI interface.
# ---------------------------------------------------------------------------

def _create_sensor_db(path: Path) -> sqlite3.Connection:
    """
    Create and return a writable connection to a fresh HERMES sensor database
    at the given path.  Schema mirrors what the logger daemon produces.

    Important: tables are created WITHOUT an explicit INTEGER PRIMARY KEY
    column.  SQLite's implicit rowid is used instead.  The normalizer queries
    `SELECT rowid, ...` and accesses row["rowid"].  When a table declares
    `id INTEGER PRIMARY KEY`, SQLite returns the rowid-alias column under the
    name "id" (not "rowid"), which would cause row["rowid"] to fail.  Using
    only implicit rowid avoids this and matches the actual HERMES DB schema.
    """
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS env (
            ts_utc  TEXT NOT NULL,
            temp_c  REAL,
            hum_pct REAL
        );
        CREATE TABLE IF NOT EXISTS air (
            ts_utc   TEXT NOT NULL,
            eco2_ppm REAL,
            tvoc_ppb REAL
        );
        CREATE TABLE IF NOT EXISTS radar (
            ts_utc    TEXT NOT NULL,
            alive     INTEGER,
            target    INTEGER,
            detect_cm INTEGER,
            move_cm   INTEGER,
            stat_cm   INTEGER
        );
        CREATE TABLE IF NOT EXISTS hb (
            ts_utc  TEXT NOT NULL,
            tick_ms INTEGER,
            seq     INTEGER
        );
    """)
    conn.commit()
    return conn


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@pytest.fixture
def db_path(tmp_path: Path):
    """
    Yield (path, conn) where path is the SQLite file path and conn is an
    open writable connection.  Tests insert rows and then call normalize()
    against path.  Connection is closed after the test.
    """
    p = tmp_path / "hermes_test.sqlite3"
    conn = _create_sensor_db(p)
    yield p, conn
    conn.close()
