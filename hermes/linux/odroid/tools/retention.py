#!/usr/bin/env python3
"""
HERMES SQLite time-series retention and downsampling.

Install (Odroid, paths match other hermes units):
  sudo ln -sf /home/odroid/hermes-src/hermes/linux/odroid/systemd/hermes-retention.service \\
    /etc/systemd/system/hermes-retention.service
  sudo ln -sf /home/odroid/hermes-src/hermes/linux/odroid/systemd/hermes-retention.timer \\
    /etc/systemd/system/hermes-retention.timer
  sudo systemctl daemon-reload
  sudo systemctl enable --now hermes-retention.timer

Adjust WorkingDirectory / symlinks if your checkout lives elsewhere.

Policy (UTC, by row age relative to run time):
  - Last 24h: full resolution (unchanged)
  - 1d–30d old: 1 row per minute (AVG numerics; last non-numeric by ts_utc)
  - 30d–365d old: 1 row per hour
  - Older than 365d: 1 row per day
  Representative ts_utc is min(ts_utc) per bucket. Original rows in each processed
  window are deleted after aggregated rows are inserted.

First run on a very large DB: use --dry-run, then --table <name> on a copy if possible.
VACUUM can take a long time and needs exclusive access; use --no-vacuum to skip.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DB = Path.home() / "hermes-data" / "db" / "hermes.sqlite3"
LOG_DIR = Path.home() / "hermes-data" / "retention"
LOG_FILE = LOG_DIR / "retention.log"

TIMESERIES_TABLES: tuple[str, ...] = (
    "radar",
    "env",
    "air",
    "light",
    "mic_noise",
    "hb",
    "metrics",
    "state_events",
    "parse_fail",
    "esp_net",
    "raw_lines",
)

PROTECTED_TABLES: frozenset[str] = frozenset(
    {
        "events",
        "event_state",
        "acks",
        "oled_status",
        "settings",
        "reports",
        "radar_calibration",
        "vision_cam",
        "vision_snapshots",
        "sqlite_sequence",
    }
)

# Extra GROUP BY columns (besides time bucket) so distinct series are not merged.
TABLE_DISCRIMINATORS: dict[str, tuple[str, ...]] = {
    "metrics": ("source", "kind", "key"),
    "state_events": ("event_type",),
    "raw_lines": ("source",),
    "parse_fail": ("source",),
    "env": ("source",),
    "air": ("source",),
    "light": ("source",),
    "mic_noise": ("source",),
    "hb": ("source",),
}

DELETE_BATCH = 10_000
CHUNK_DAYS_COLD = 30
CHUNK_DAYS_COOL = 7
CHUNK_DAYS_WARM = 1

STAGING_NAME = "hermes_ret_staging"


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    col_type: str
    pk: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str) -> datetime | None:
    try:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _setup_logging(verbose: bool) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s")
    fmt.converter = time.gmtime  # type: ignore[assignment]

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(root.level)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=120.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=120000;")
    return conn


def _quote_ident(name: str) -> str:
    if not name.isidentifier():
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return '"' + name.replace('"', '""') + '"'


def table_columns(conn: sqlite3.Connection, table: str) -> list[ColumnInfo]:
    rows = conn.execute(f"PRAGMA table_info({_quote_ident(table)})").fetchall()
    return [
        ColumnInfo(name=str(r[1]), col_type=(r[2] or "").upper(), pk=int(r[5] or 0))
        for r in rows
    ]


def _is_numeric_type(col: ColumnInfo) -> bool:
    t = col.col_type
    return any(x in t for x in ("INT", "REAL", "FLOAT", "DOUBLE", "NUMERIC", "DEC"))


def _is_integer_type(col: ColumnInfo) -> bool:
    return "INT" in col.col_type


def time_column(cols: Sequence[ColumnInfo]) -> str:
    names = {c.name for c in cols}
    if "ts_utc" in names:
        return "ts_utc"
    for candidate in ("timestamp", "created_at", "ts"):
        if candidate in names:
            return candidate
    raise ValueError("no ts_utc/timestamp/created_at/ts column")


def bucket_expr_minute(col: str) -> str:
    c = _quote_ident(col)
    return f"(substr({c}, 1, 10) || 'T' || substr({c}, 12, 5))"


def bucket_expr_hour(col: str) -> str:
    c = _quote_ident(col)
    return f"(substr({c}, 1, 10) || 'T' || substr({c}, 12, 2))"


def bucket_expr_day(col: str) -> str:
    c = _quote_ident(col)
    return f"substr({c}, 1, 10)"


def discriminators_for_table(table: str, cols: Sequence[ColumnInfo]) -> list[str]:
    names = {c.name for c in cols}
    extra = TABLE_DISCRIMINATORS.get(table, ())
    out: list[str] = []
    for d in extra:
        if d in names:
            out.append(d)
    if not out and "source" in names:
        out.append("source")
    return out


def _col_list_sql(cols: Iterable[str]) -> str:
    return ", ".join(_quote_ident(c) for c in cols)


def _bucket_x(bucket_sql: str, ts_col: str) -> str:
    tsq = _quote_ident(ts_col)
    return bucket_sql.replace(tsq, f"x.{tsq}")


def build_aggregate_select(
    table: str,
    cols: Sequence[ColumnInfo],
    ts_col: str,
    bucket_sql: str,
    discriminators: Sequence[str],
) -> tuple[str, list[str]]:
    """SQL for INSERT INTO staging (...) SELECT ... with params (t_lo, t_hi)."""
    t = _quote_ident(table)
    tsq = _quote_ident(ts_col)
    bucket_x = _bucket_x(bucket_sql, ts_col)

    non_id_cols = [c for c in cols if c.name.lower() != "id"]
    insert_names = [c.name for c in non_id_cols]

    group_parts = [bucket_sql] + [_quote_ident(d) for d in discriminators]
    group_by = ", ".join(group_parts)

    inner_from = (
        f"SELECT *, {bucket_sql} AS bk FROM {t} "
        f"WHERE {tsq} >= ? AND {tsq} < ?"
    )

    select_parts: list[str] = []
    disc_set = set(discriminators)

    for col in non_id_cols:
        name = col.name
        cq = _quote_ident(name)
        if name == ts_col:
            select_parts.append(f"MIN(r.{cq}) AS {cq}")
        elif name in disc_set:
            select_parts.append(f"MIN(r.{cq}) AS {cq}")
        elif name == "ts_local":
            bucket_match = " AND ".join(
                [f"{bucket_x} = r.bk"]
                + [f"x.{_quote_ident(d)} = r.{_quote_ident(d)}" for d in discriminators]
            )
            select_parts.append(
                f"(SELECT x.{_quote_ident('ts_local')} FROM {t} x WHERE {bucket_match} "
                f"ORDER BY x.{tsq} ASC, x.{_quote_ident('id')} ASC LIMIT 1) AS {cq}"
            )
        elif _is_numeric_type(col):
            if _is_integer_type(col):
                select_parts.append(f"CAST(ROUND(AVG(r.{cq})) AS INTEGER) AS {cq}")
            else:
                select_parts.append(f"AVG(r.{cq}) AS {cq}")
        else:
            bucket_match = " AND ".join(
                [f"{bucket_x} = r.bk"]
                + [f"x.{_quote_ident(d)} = r.{_quote_ident(d)}" for d in discriminators]
            )
            select_parts.append(
                f"(SELECT x.{cq} FROM {t} x WHERE {bucket_match} "
                f"ORDER BY x.{tsq} DESC, x.{_quote_ident('id')} DESC LIMIT 1) AS {cq}"
            )

    sql = (
        f"SELECT {', '.join(select_parts)} FROM ({inner_from}) r "
        f"GROUP BY {group_by}"
    )
    return sql, insert_names


def _has_rowid(conn: sqlite3.Connection, table: str) -> bool:
    r = conn.execute(
        f"SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not r or not r[0]:
        return True
    s = r[0].upper()
    return "WITHOUT ROWID" not in s


def _iter_time_chunks(lo: datetime, hi: datetime, days: int) -> Iterator[tuple[datetime, datetime]]:
    cur = lo
    step = timedelta(days=days)
    while cur < hi:
        nxt = min(cur + step, hi)
        yield cur, nxt
        cur = nxt


def _drop_staging_if_exists(conn: sqlite3.Connection) -> None:
    conn.execute(f"DROP TABLE IF EXISTS {_quote_ident(STAGING_NAME)}")


def _create_staging(conn: sqlite3.Connection, table: str, insert_cols: Sequence[str]) -> None:
    _drop_staging_if_exists(conn)
    cols = _col_list_sql(insert_cols)
    src = _quote_ident(table)
    conn.execute(
        f"CREATE TEMPORARY TABLE {_quote_ident(STAGING_NAME)} AS "
        f"SELECT {cols} FROM {src} WHERE 0"
    )


def count_window_rows(
    conn: sqlite3.Connection, table: str, ts_col: str, t_lo: str, t_hi: str
) -> int:
    t = _quote_ident(table)
    tsq = _quote_ident(ts_col)
    row = conn.execute(
        f"SELECT COUNT(*) FROM {t} WHERE {tsq} >= ? AND {tsq} < ?",
        (t_lo, t_hi),
    ).fetchone()
    return int(row[0]) if row else 0


def count_buckets(
    conn: sqlite3.Connection,
    table: str,
    ts_col: str,
    bucket_sql: str,
    discriminators: Sequence[str],
    t_lo: str,
    t_hi: str,
) -> int:
    t = _quote_ident(table)
    tsq = _quote_ident(ts_col)
    group = ", ".join([bucket_sql] + [_quote_ident(d) for d in discriminators])
    row = conn.execute(
        f"SELECT COUNT(*) FROM (SELECT 1 FROM {t} WHERE {tsq} >= ? AND {tsq} < ? "
        f"GROUP BY {group})",
        (t_lo, t_hi),
    ).fetchone()
    return int(row[0]) if row else 0


def process_time_window(
    conn: sqlite3.Connection,
    table: str,
    cols: list[ColumnInfo],
    ts_col: str,
    bucket_sql: str,
    discriminators: list[str],
    t_lo: str,
    t_hi: str,
    *,
    dry_run: bool,
    log: logging.Logger,
    label: str,
) -> tuple[int, int]:
    """
    Returns (rows_before, rows_after) for this window.
    rows_after == rows_before if dry_run or nothing to do.
    """
    t = _quote_ident(table)
    tsq = _quote_ident(ts_col)
    n_before = count_window_rows(conn, table, ts_col, t_lo, t_hi)
    if n_before <= 1:
        log.debug("%s skip window [%s, %s): rows=%s", label, t_lo, t_hi, n_before)
        return n_before, n_before

    n_buckets = count_buckets(conn, table, ts_col, bucket_sql, discriminators, t_lo, t_hi)
    if n_buckets >= n_before:
        log.info(
            "%s window [%s, %s): already at or below bucket count (rows=%s buckets=%s), skip",
            label,
            t_lo,
            t_hi,
            n_before,
            n_buckets,
        )
        return n_before, n_before

    agg_sql, insert_cols = build_aggregate_select(
        table, cols, ts_col, bucket_sql, discriminators
    )

    if dry_run:
        log.info(
            "%s DRY-RUN [%s, %s): rows=%s -> ~%s bucket rows (delta ~%s)",
            label,
            t_lo,
            t_hi,
            n_before,
            n_buckets,
            n_before - n_buckets,
        )
        return n_before, n_buckets

    _create_staging(conn, table, insert_cols)
    col_list = _col_list_sql(insert_cols)
    st = _quote_ident(STAGING_NAME)
    try:
        conn.execute(f"INSERT INTO {st} ({col_list}) {agg_sql}", (t_lo, t_hi))
        staged = conn.execute(f"SELECT COUNT(*) FROM {st}").fetchone()
        n_staged = int(staged[0]) if staged else 0
        if n_staged == 0:
            log.warning("%s staging empty despite rows=%s; skip window", label, n_before)
            return n_before, n_before

        log.info(
            "%s [%s, %s): aggregating rows=%s -> %s", label, t_lo, t_hi, n_before, n_staged
        )

        use_rowid = _has_rowid(conn, table)
        conn.execute("BEGIN IMMEDIATE")
        try:
            deleted_total = 0
            while True:
                if use_rowid:
                    cur = conn.execute(
                        f"DELETE FROM {t} WHERE rowid IN ("
                        f"SELECT rowid FROM {t} WHERE {tsq} >= ? AND {tsq} < ? LIMIT ?)",
                        (t_lo, t_hi, DELETE_BATCH),
                    )
                else:
                    cur = conn.execute(
                        f"DELETE FROM {t} WHERE id IN ("
                        f"SELECT id FROM {t} WHERE {tsq} >= ? AND {tsq} < ? LIMIT ?)",
                        (t_lo, t_hi, DELETE_BATCH),
                    )
                n = cur.rowcount if cur.rowcount is not None else 0
                deleted_total += n
                if n == 0:
                    break
                if deleted_total % (DELETE_BATCH * 10) == 0:
                    log.info("%s deleted %s rows...", label, deleted_total)

            conn.execute(
                f"INSERT INTO {t} ({col_list}) SELECT {col_list} FROM {st}"
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        n_after = count_window_rows(conn, table, ts_col, t_lo, t_hi)
        log.info(
            "%s done [%s, %s): deleted=%s inserted=%s rows_now=%s",
            label,
            t_lo,
            t_hi,
            deleted_total,
            n_staged,
            n_after,
        )
        return n_before, n_after
    finally:
        _drop_staging_if_exists(conn)


def process_table(
    conn: sqlite3.Connection,
    table: str,
    *,
    dry_run: bool,
    log: logging.Logger,
) -> None:
    if table in PROTECTED_TABLES:
        log.warning("refusing to process protected table %s", table)
        return

    cols = table_columns(conn, table)
    if not cols:
        log.warning("table %s missing or empty schema; skip", table)
        return

    try:
        ts_col = time_column(cols)
    except ValueError as e:
        log.warning("table %s: %s; skip", table, e)
        return

    discriminators = discriminators_for_table(table, cols)
    now = _utc_now()
    boundary_24h = now - timedelta(hours=24)
    boundary_30d = now - timedelta(days=30)
    boundary_365d = now - timedelta(days=365)

    lo_24 = _iso_z(boundary_24h)
    lo_30 = _iso_z(boundary_30d)
    lo_365 = _iso_z(boundary_365d)

    total_before = conn.execute(
        f"SELECT COUNT(*) FROM {_quote_ident(table)}"
    ).fetchone()
    n_table_start = int(total_before[0]) if total_before else 0
    log.info(
        "table %s: total_rows=%s ts_col=%s discriminators=%s",
        table,
        n_table_start,
        ts_col,
        discriminators,
    )

    if n_table_start == 0:
        log.info("table %s: empty; skip", table)
        return

    min_row = conn.execute(
        f"SELECT MIN({_quote_ident(ts_col)}) FROM {_quote_ident(table)}"
    ).fetchone()
    cold_floor = datetime(1970, 1, 1, tzinfo=timezone.utc)
    if min_row and min_row[0] is not None:
        parsed = _parse_ts(str(min_row[0]))
        if parsed is not None:
            cold_floor = parsed

    # Cold: older than 365d -> daily
    for chunk_lo, chunk_hi in _iter_time_chunks(cold_floor, boundary_365d, CHUNK_DAYS_COLD):
        process_time_window(
            conn,
            table,
            cols,
            ts_col,
            bucket_expr_day(ts_col),
            discriminators,
            _iso_z(chunk_lo),
            _iso_z(chunk_hi),
            dry_run=dry_run,
            log=log,
            label=f"{table}/cold",
        )

    # Cool: 30d .. 365d -> hourly
    for chunk_lo, chunk_hi in _iter_time_chunks(boundary_365d, boundary_30d, CHUNK_DAYS_COOL):
        process_time_window(
            conn,
            table,
            cols,
            ts_col,
            bucket_expr_hour(ts_col),
            discriminators,
            _iso_z(chunk_lo),
            _iso_z(chunk_hi),
            dry_run=dry_run,
            log=log,
            label=f"{table}/cool",
        )

    # Warm: 24h .. 30d -> per minute
    for chunk_lo, chunk_hi in _iter_time_chunks(boundary_30d, boundary_24h, CHUNK_DAYS_WARM):
        process_time_window(
            conn,
            table,
            cols,
            ts_col,
            bucket_expr_minute(ts_col),
            discriminators,
            _iso_z(chunk_lo),
            _iso_z(chunk_hi),
            dry_run=dry_run,
            log=log,
            label=f"{table}/warm",
        )

    total_after = conn.execute(
        f"SELECT COUNT(*) FROM {_quote_ident(table)}"
    ).fetchone()
    n_table_end = int(total_after[0]) if total_after else 0
    log.info("table %s: total_rows after=%s (delta %s)", table, n_table_end, n_table_end - n_table_start)


def resolve_db_path() -> Path:
    env = os.environ.get("HERMES_DB")
    if env:
        return Path(env).expanduser()
    return DEFAULT_DB.expanduser()


def main() -> int:
    p = argparse.ArgumentParser(description="HERMES DB retention / downsampling")
    p.add_argument("--dry-run", action="store_true", help="report only, no DB writes")
    p.add_argument("--table", help="single time-series table name")
    p.add_argument("--no-vacuum", action="store_true", help="skip VACUUM at end")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    _setup_logging(args.verbose)
    log = logging.getLogger("retention")

    db_path = resolve_db_path()
    if not db_path.is_file():
        log.error("database not found: %s", db_path)
        return 1

    tables: Sequence[str]
    if args.table:
        if args.table in PROTECTED_TABLES:
            log.error("refusing --table %s (protected)", args.table)
            return 1
        if args.table not in TIMESERIES_TABLES:
            log.error("unknown or non-timeseries table: %s", args.table)
            return 1
        tables = (args.table,)
    else:
        tables = TIMESERIES_TABLES

    log.info(
        "start db=%s dry_run=%s tables=%s",
        db_path,
        args.dry_run,
        list(tables),
    )

    conn = _connect(db_path)
    try:
        for tbl in tables:
            try:
                process_table(conn, tbl, dry_run=args.dry_run, log=log)
            except sqlite3.Error as e:
                log.exception("table %s failed: %s", tbl, e)
                if not args.dry_run:
                    log.error("stopping after failure (previous tables committed)")
                    return 1
        if not args.dry_run and not args.no_vacuum:
            log.info("running VACUUM (can take a long time; stop logger if you see lock errors)")
            conn.execute("VACUUM")
            log.info("VACUUM complete")
    finally:
        conn.close()

    log.info("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
