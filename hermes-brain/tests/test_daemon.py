"""
Daemon resilience tests.

Verifies that run_cycle() completes without raising and returns a valid status
dict when the sensor database is missing, empty, or contains no recent rows.

The daemon always calls score_all(use_llm=False), so no model is required.
These tests confirm that holds end-to-end through the full cycle.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.daemon import run_cycle
from app.pipeline.context_store import ContextStore
from app.escalation.cloud_client import EscalationClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path: Path) -> ContextStore:
    store_dir = tmp_path / "context"
    store_dir.mkdir()
    return ContextStore(store_dir=store_dir, salience_threshold=0.0)


def _make_client(tmp_path: Path) -> EscalationClient:
    queue_dir = tmp_path / "escalation"
    queue_dir.mkdir()
    return EscalationClient(endpoint="", queue_dir=queue_dir)


def _run(tmp_path: Path, db_path: Path, *, dry_run: bool = False) -> dict:
    return run_cycle(
        db_path=db_path,
        window_min=5,
        salience_threshold=0.0,
        escalation_threshold=0.7,
        allowlist_str="ts_utc,source,kind,tags,salience,summary",
        destination="test",
        store=_make_store(tmp_path),
        client=_make_client(tmp_path),
        omi_queue_path=tmp_path / "omi_queue.jsonl",
        prev_radar_target=None,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Tests: missing database
# ---------------------------------------------------------------------------

def test_cycle_missing_db_returns_valid_status(tmp_path: Path) -> None:
    """run_cycle must not raise when the sensor DB file does not exist."""
    db_path = tmp_path / "nonexistent.sqlite3"
    assert not db_path.exists()

    status = _run(tmp_path, db_path)

    assert isinstance(status, dict)
    assert status["error"] is None
    assert status["events_read"] == 0
    assert status["candidates_built"] == 0
    assert status["candidates_stored"] == 0
    assert status["packets_queued"] == 0
    assert status["duration_ms"] >= 0


def test_cycle_missing_db_dry_run(tmp_path: Path) -> None:
    """dry-run mode must also complete cleanly with a missing DB."""
    status = _run(tmp_path, tmp_path / "no_db.sqlite3", dry_run=True)
    assert status["error"] is None
    assert status["events_read"] == 0


# ---------------------------------------------------------------------------
# Tests: empty database (tables exist, no rows)
# ---------------------------------------------------------------------------

@pytest.fixture
def empty_db(tmp_path: Path) -> Path:
    p = tmp_path / "empty.sqlite3"
    conn = sqlite3.connect(str(p))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS env   (ts_utc TEXT NOT NULL, temp_c REAL, hum_pct REAL);
        CREATE TABLE IF NOT EXISTS air   (ts_utc TEXT NOT NULL, eco2_ppm REAL, tvoc_ppb REAL);
        CREATE TABLE IF NOT EXISTS radar (ts_utc TEXT NOT NULL, alive INTEGER, target INTEGER,
                                          detect_cm INTEGER, move_cm INTEGER, stat_cm INTEGER);
        CREATE TABLE IF NOT EXISTS hb    (ts_utc TEXT NOT NULL, tick_ms INTEGER, seq INTEGER);
    """)
    conn.commit()
    conn.close()
    return p


def test_cycle_empty_db_returns_valid_status(tmp_path: Path, empty_db: Path) -> None:
    """run_cycle must not raise when tables exist but contain no rows."""
    status = _run(tmp_path, empty_db)

    assert isinstance(status, dict)
    assert status["error"] is None
    assert status["events_read"] == 0
    assert status["candidates_built"] == 0
    assert status["candidates_stored"] == 0
    assert status["duration_ms"] >= 0


def test_cycle_empty_db_no_candidates_stored(tmp_path: Path, empty_db: Path) -> None:
    """With no events, no candidate files should be written."""
    store_dir = tmp_path / "context"
    store_dir.mkdir()
    store = ContextStore(store_dir=store_dir, salience_threshold=0.0)
    client = _make_client(tmp_path)

    run_cycle(
        db_path=empty_db,
        window_min=5,
        salience_threshold=0.0,
        escalation_threshold=0.7,
        allowlist_str="ts_utc,source,kind,tags,salience,summary",
        destination="test",
        store=store,
        client=client,
        omi_queue_path=tmp_path / "omi_queue.jsonl",
        prev_radar_target=None,
        dry_run=False,
    )

    jsonl_files = list(store_dir.glob("candidates_*.jsonl"))
    assert jsonl_files == [], "no candidate files expected when DB is empty"


# ---------------------------------------------------------------------------
# Tests: no model loaded (use_llm=False — always the case in the daemon)
# ---------------------------------------------------------------------------

def test_cycle_no_llm_completes(tmp_path: Path, empty_db: Path) -> None:
    """
    The daemon always calls score_all(use_llm=False).  Confirm that a full
    cycle with an empty DB and no model configured produces no error.
    """
    status = _run(tmp_path, empty_db)
    assert status["error"] is None


# ---------------------------------------------------------------------------
# Tests: Omi queue absent or empty
# ---------------------------------------------------------------------------

def test_cycle_missing_omi_queue_file(tmp_path: Path, empty_db: Path) -> None:
    """Daemon must handle a missing Omi queue file gracefully."""
    omi_path = tmp_path / "omi_queue.jsonl"
    assert not omi_path.exists()

    status = _run(tmp_path, empty_db)
    assert status["error"] is None
    assert status["omi_events"] == 0


def test_cycle_empty_omi_queue_file(tmp_path: Path, empty_db: Path) -> None:
    """An empty Omi queue file should be drained without error."""
    omi_path = tmp_path / "omi_queue.jsonl"
    omi_path.write_text("", encoding="utf-8")

    status = _run(tmp_path, empty_db)
    assert status["error"] is None
    assert status["omi_events"] == 0


def test_cycle_malformed_omi_queue_line(tmp_path: Path, empty_db: Path) -> None:
    """Malformed lines in the Omi queue must not crash the cycle."""
    omi_path = tmp_path / "omi_queue.jsonl"
    omi_path.write_text("not-valid-json\n{also bad\n", encoding="utf-8")

    status = _run(tmp_path, empty_db)
    assert status["error"] is None
    assert status["omi_events"] == 0


# ---------------------------------------------------------------------------
# Tests: status dict shape
# ---------------------------------------------------------------------------

def test_cycle_status_has_required_keys(tmp_path: Path, empty_db: Path) -> None:
    """Status dict must contain all keys the dashboard expects."""
    required = {
        "ts_run", "events_read", "omi_events", "candidates_built",
        "candidates_stored", "packets_queued", "packets_delivered",
        "error", "duration_ms",
    }
    status = _run(tmp_path, empty_db)
    assert required.issubset(status.keys())
