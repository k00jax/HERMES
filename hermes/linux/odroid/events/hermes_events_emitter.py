#!/usr/bin/env python3
import datetime
import json
import os
import sqlite3
from pathlib import Path
from typing import Dict, Optional

DB_PATH = Path(os.environ.get("HERMES_DB_PATH", "/home/odroid/hermes-data/db/hermes.sqlite3"))
STATE_PATH = Path(os.environ.get("HERMES_EVENTS_STATE_PATH", "/home/odroid/hermes-data/events-emitter/state.json"))
READY_MAX_AGE_SECS = int(os.environ.get("HERMES_READY_MAX_AGE_SECS", "180"))
EVENT_DEDUPE_COOLDOWN_SECS = int(os.environ.get("HERMES_EVENTS_DEDUPE_COOLDOWN_SECS", "120"))

TABLES = ("hb", "env", "air", "light", "mic_noise", "esp_net")


def now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def utc_iso(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def local_iso(dt: datetime.datetime) -> str:
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def parse_ts(raw: str) -> Optional[datetime.datetime]:
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


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


def load_state() -> Dict[str, object]:
    if not STATE_PATH.exists():
        return {"stale": {}, "hb": {"id": None, "uptime_s": None, "boot": None}}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"stale": {}, "hb": {"id": None, "uptime_s": None, "boot": None}}


def save_state(state: Dict[str, object]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True))


def current_table_ages(conn: sqlite3.Connection) -> Dict[str, float]:
    now = now_utc()
    ages: Dict[str, float] = {}
    for table in TABLES:
        row = conn.execute(f"SELECT ts_utc FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
        if not row or row[0] is None:
            ages[table] = -1.0
            continue
        ts = parse_ts(str(row[0]))
        if ts is None:
            ages[table] = -1.0
        else:
            ages[table] = max(0.0, (now - ts).total_seconds())
    return ages


def recent_dedupe_exists(conn: sqlite3.Connection, dedupe_key: str, cooldown_secs: int) -> bool:
    row = conn.execute("SELECT ts_utc FROM events WHERE dedupe_key=? ORDER BY id DESC LIMIT 1", (dedupe_key,)).fetchone()
    if not row or row[0] is None:
        return False
    ts = parse_ts(str(row[0]))
    if ts is None:
        return False
    return (now_utc() - ts).total_seconds() < cooldown_secs


def emit_event(
    conn: sqlite3.Connection,
    *,
    kind: str,
    severity: str,
    source: str,
    message: str,
    data: Optional[Dict[str, object]] = None,
    dedupe_key: Optional[str] = None,
    cooldown_secs: int = EVENT_DEDUPE_COOLDOWN_SECS,
) -> bool:
    if dedupe_key and recent_dedupe_exists(conn, dedupe_key, cooldown_secs):
        return False

    dt = now_utc()
    conn.execute(
        """
        INSERT INTO events (ts_utc, ts_local, kind, severity, source, message, data_json, dedupe_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_iso(dt),
            local_iso(dt),
            kind,
            severity,
            source,
            message,
            json.dumps(data, separators=(",", ":")) if data is not None else None,
            dedupe_key,
        ),
    )
    return True


def coerce_int(v: object) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(v)
    except Exception:
        return None


def run_once() -> int:
    if not DB_PATH.exists():
        return 0

    state = load_state()
    stale_state = state.setdefault("stale", {})
    hb_state = state.setdefault("hb", {"id": None, "uptime_s": None, "boot": None})

    conn = sqlite3.connect(DB_PATH, timeout=2.0)
    conn.execute("PRAGMA busy_timeout=2000;")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")

    try:
        ensure_events_table(conn)

        ages = current_table_ages(conn)
        for table, age in ages.items():
            stale_now = (age < 0.0) or (age > READY_MAX_AGE_SECS)
            stale_prev = bool(stale_state.get(table, False))
            if stale_now and not stale_prev:
                emit_event(
                    conn,
                    kind="stale_detected",
                    severity="warn",
                    source="dashboard",
                    message=f"{table} stale age={int(age) if age >= 0 else -1}s threshold={READY_MAX_AGE_SECS}s",
                    data={"table": table, "age_seconds": age, "threshold_seconds": READY_MAX_AGE_SECS},
                    dedupe_key=f"stale_detected:{table}",
                    cooldown_secs=60,
                )
            elif (not stale_now) and stale_prev:
                emit_event(
                    conn,
                    kind="stale_recovered",
                    severity="info",
                    source="dashboard",
                    message=f"{table} recovered age={int(age)}s",
                    data={"table": table, "age_seconds": age},
                    dedupe_key=f"stale_recovered:{table}",
                    cooldown_secs=60,
                )
            stale_state[table] = stale_now

        hb_row = conn.execute(
            "SELECT id, uptime_s, boot, reset_reason FROM hb ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if hb_row:
            hb_id = coerce_int(hb_row[0])
            uptime_s = coerce_int(hb_row[1])
            boot = coerce_int(hb_row[2])
            reset_reason = str(hb_row[3]) if hb_row[3] is not None else "unknown"

            prev_id = coerce_int(hb_state.get("id"))
            prev_uptime = coerce_int(hb_state.get("uptime_s"))
            prev_boot = coerce_int(hb_state.get("boot"))

            reboot_reasons = []
            if prev_id is not None and hb_id is not None and hb_id != prev_id:
                if prev_boot is not None and boot is not None and boot > prev_boot:
                    reboot_reasons.append("boot_increment")
                if prev_uptime is not None and uptime_s is not None and uptime_s + 5 < prev_uptime:
                    reboot_reasons.append("uptime_reset")

            if reboot_reasons:
                emit_event(
                    conn,
                    kind="reboot_detected",
                    severity="warn",
                    source="nrf",
                    message=f"reboot detected via {'+'.join(reboot_reasons)} reset_reason={reset_reason}",
                    data={
                        "prev_uptime_s": prev_uptime,
                        "uptime_s": uptime_s,
                        "prev_boot": prev_boot,
                        "boot": boot,
                        "reset_reason": reset_reason,
                    },
                    dedupe_key="reboot_detected",
                    cooldown_secs=30,
                )

            hb_state["id"] = hb_id
            hb_state["uptime_s"] = uptime_s
            hb_state["boot"] = boot

        conn.commit()
    finally:
        conn.close()

    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_once())
