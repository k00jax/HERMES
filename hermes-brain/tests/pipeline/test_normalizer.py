"""
Tests for pipeline/normalizer.py.

All tests use a real SQLite file (via the db_path fixture from conftest.py).
The normalizer opens the DB in read-only URI mode, so we write test rows
through the writable fixture connection before calling normalize().

Key invariants locked here:
- normalize() returns [] for a missing DB path (no exception).
- normalize() returns [] for a DB with no matching tables.
- normalize() returns [] for a DB with tables but no rows in the window.
- raw_ref format is exactly "{table}:{rowid}".
- env rows produce two events per row (temperature + humidity).
- air rows produce two events per row (co2 + voc).
- radar rows produce one event per row with all present fields.
- heartbeats are excluded by default; included with include_heartbeats=True.
- The returned list is sorted by ts_utc ascending.
"""
from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from app.pipeline.normalizer import normalize
from app.pipeline.types import (
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


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Missing / empty DB
# ---------------------------------------------------------------------------

def test_missing_db_returns_empty(tmp_path):
    path = tmp_path / "does_not_exist.sqlite3"
    result = normalize(path, window_minutes=5)
    assert result == []


def test_empty_tables_returns_empty(db_path):
    path, _conn = db_path
    result = normalize(path, window_minutes=5)
    assert result == []


def test_db_with_no_tables_returns_empty(tmp_path):
    """A valid SQLite file with no sensor tables — normalizer must not crash."""
    import sqlite3
    p = tmp_path / "empty.sqlite3"
    conn = sqlite3.connect(str(p))
    conn.close()
    result = normalize(p, window_minutes=5)
    assert result == []


# ---------------------------------------------------------------------------
# env table
# ---------------------------------------------------------------------------

def test_env_row_produces_two_events(db_path):
    path, conn = db_path
    ts = _now()
    conn.execute(
        "INSERT INTO env (ts_utc, temp_c, hum_pct) VALUES (?, ?, ?)",
        (ts, 22.5, 48.3),
    )
    conn.commit()

    events = normalize(path, window_minutes=10)
    env_events = [e for e in events if e.source == SOURCE_ENV]
    assert len(env_events) == 2

    kinds = {e.kind for e in env_events}
    assert kinds == {KIND_TEMPERATURE, KIND_HUMIDITY}


def test_env_temperature_event_fields(db_path):
    path, conn = db_path
    ts = _now()
    conn.execute(
        "INSERT INTO env (ts_utc, temp_c, hum_pct) VALUES (?, ?, ?)",
        (ts, 22.5, 48.3),
    )
    conn.commit()

    events = normalize(path, window_minutes=10)
    temp_events = [e for e in events if e.kind == KIND_TEMPERATURE]
    assert len(temp_events) == 1
    e = temp_events[0]

    assert e.ts_utc == ts
    assert e.source == SOURCE_ENV
    assert e.value == {"temp_c": 22.5}
    assert e.raw_ref is not None
    assert e.raw_ref.startswith("env:")
    assert e.ingested_at != ""


def test_env_raw_ref_format(db_path):
    path, conn = db_path
    ts = _now()
    conn.execute(
        "INSERT INTO env (ts_utc, temp_c, hum_pct) VALUES (?, ?, ?)",
        (ts, 20.0, 45.0),
    )
    conn.commit()

    events = normalize(path, window_minutes=10)
    env_events = [e for e in events if e.source == SOURCE_ENV]
    for e in env_events:
        parts = e.raw_ref.split(":")
        assert len(parts) == 2, f"expected 'table:rowid', got {e.raw_ref!r}"
        assert parts[0] == "env"
        assert parts[1].isdigit()


def test_env_null_temp_skips_temperature_event(db_path):
    path, conn = db_path
    ts = _now()
    conn.execute(
        "INSERT INTO env (ts_utc, temp_c, hum_pct) VALUES (?, NULL, ?)",
        (ts, 48.0),
    )
    conn.commit()

    events = normalize(path, window_minutes=10)
    assert not any(e.kind == KIND_TEMPERATURE for e in events)
    assert any(e.kind == KIND_HUMIDITY for e in events)


def test_env_null_hum_skips_humidity_event(db_path):
    path, conn = db_path
    ts = _now()
    conn.execute(
        "INSERT INTO env (ts_utc, temp_c, hum_pct) VALUES (?, ?, NULL)",
        (ts, 22.0),
    )
    conn.commit()

    events = normalize(path, window_minutes=10)
    assert any(e.kind == KIND_TEMPERATURE for e in events)
    assert not any(e.kind == KIND_HUMIDITY for e in events)


# ---------------------------------------------------------------------------
# air table
# ---------------------------------------------------------------------------

def test_air_row_produces_two_events(db_path):
    path, conn = db_path
    ts = _now()
    conn.execute(
        "INSERT INTO air (ts_utc, eco2_ppm, tvoc_ppb) VALUES (?, ?, ?)",
        (ts, 850.0, 12.0),
    )
    conn.commit()

    events = normalize(path, window_minutes=10)
    air_events = [e for e in events if e.source == SOURCE_AIR]
    assert len(air_events) == 2
    kinds = {e.kind for e in air_events}
    assert kinds == {KIND_CO2, KIND_VOC}


def test_air_co2_raw_ref_format(db_path):
    path, conn = db_path
    ts = _now()
    conn.execute(
        "INSERT INTO air (ts_utc, eco2_ppm, tvoc_ppb) VALUES (?, ?, ?)",
        (ts, 400.0, 5.0),
    )
    conn.commit()

    events = normalize(path, window_minutes=10)
    co2_events = [e for e in events if e.kind == KIND_CO2]
    assert len(co2_events) == 1
    assert co2_events[0].raw_ref.startswith("air:")
    assert co2_events[0].value == {"eco2_ppm": 400.0}


# ---------------------------------------------------------------------------
# radar table
# ---------------------------------------------------------------------------

def test_radar_row_produces_one_presence_event(db_path):
    path, conn = db_path
    ts = _now()
    conn.execute(
        "INSERT INTO radar (ts_utc, alive, target, detect_cm, move_cm, stat_cm) VALUES (?, ?, ?, ?, ?, ?)",
        (ts, 1, 1, 120, 80, 0),
    )
    conn.commit()

    events = normalize(path, window_minutes=10)
    radar_events = [e for e in events if e.source == SOURCE_RADAR]
    assert len(radar_events) == 1
    e = radar_events[0]
    assert e.kind == KIND_PRESENCE
    assert e.raw_ref.startswith("radar:")
    assert e.value["target"] == 1.0
    assert e.value["detect_cm"] == 120.0


def test_radar_preserves_all_non_null_fields(db_path):
    path, conn = db_path
    ts = _now()
    conn.execute(
        "INSERT INTO radar (ts_utc, alive, target, detect_cm, move_cm, stat_cm) VALUES (?, 1, 0, 200, 0, 180)",
        (ts,),
    )
    conn.commit()

    events = normalize(path, window_minutes=10)
    radar_events = [e for e in events if e.source == SOURCE_RADAR]
    v = radar_events[0].value
    for field in ("alive", "target", "detect_cm", "move_cm", "stat_cm"):
        assert field in v, f"field {field!r} missing from radar event value"


# ---------------------------------------------------------------------------
# heartbeats
# ---------------------------------------------------------------------------

def test_heartbeats_excluded_by_default(db_path):
    path, conn = db_path
    ts = _now()
    conn.execute(
        "INSERT INTO hb (ts_utc, tick_ms, seq) VALUES (?, ?, ?)",
        (ts, 1000, 42),
    )
    conn.commit()

    events = normalize(path, window_minutes=10)
    assert not any(e.source == SOURCE_HB for e in events)


def test_heartbeats_included_when_requested(db_path):
    path, conn = db_path
    ts = _now()
    conn.execute(
        "INSERT INTO hb (ts_utc, tick_ms, seq) VALUES (?, ?, ?)",
        (ts, 1000, 42),
    )
    conn.commit()

    events = normalize(path, window_minutes=10, include_heartbeats=True)
    hb_events = [e for e in events if e.source == SOURCE_HB]
    assert len(hb_events) == 1
    assert hb_events[0].kind == KIND_HEARTBEAT
    assert hb_events[0].value["tick_ms"] == 1000
    assert hb_events[0].value["seq"] == 42


# ---------------------------------------------------------------------------
# Sort order
# ---------------------------------------------------------------------------

def test_events_sorted_by_ts_utc(db_path):
    path, conn = db_path
    now = datetime.datetime.now(datetime.timezone.utc)
    # Insert in reverse order to verify the sort.
    t1 = (now - datetime.timedelta(seconds=30)).isoformat()
    t2 = (now - datetime.timedelta(seconds=20)).isoformat()
    t3 = (now - datetime.timedelta(seconds=10)).isoformat()

    conn.execute("INSERT INTO env (ts_utc, temp_c, hum_pct) VALUES (?, ?, ?)", (t3, 22.0, 50.0))
    conn.execute("INSERT INTO env (ts_utc, temp_c, hum_pct) VALUES (?, ?, ?)", (t1, 21.0, 49.0))
    conn.execute("INSERT INTO env (ts_utc, temp_c, hum_pct) VALUES (?, ?, ?)", (t2, 21.5, 49.5))
    conn.commit()

    events = normalize(path, window_minutes=10)
    ts_values = [e.ts_utc for e in events]
    assert ts_values == sorted(ts_values), "events are not sorted by ts_utc"


# ---------------------------------------------------------------------------
# Window cutoff
# ---------------------------------------------------------------------------

def test_old_rows_excluded_by_cutoff(db_path):
    path, conn = db_path
    old_ts = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)
    ).isoformat()
    conn.execute(
        "INSERT INTO env (ts_utc, temp_c, hum_pct) VALUES (?, ?, ?)",
        (old_ts, 20.0, 45.0),
    )
    conn.commit()

    events = normalize(path, window_minutes=5)
    assert events == []
