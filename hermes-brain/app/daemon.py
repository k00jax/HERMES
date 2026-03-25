"""
HERMES home-AI pipeline daemon.

Runs the pipeline loop on a configurable interval.  Each cycle:

    1. Read sensor events from SQLite (normalizer).
    2. Merge any pending Omi events from the shared Omi queue.
    3. Group events into time-bucket candidates (candidate_builder).
    4. Score each candidate (salience_scorer).
    5. Apply privacy filter and route escalation packets (privacy_router).
    6. Store qualifying candidates locally (context_store).
    7. Deliver escalation packets (cloud_client).

The daemon writes a status file (JSON) after every cycle so the dashboard
context router can surface live pipeline health without the router needing
to import daemon internals.

Usage
-----
    python -m app.daemon            # reads config from env / config.yaml
    python -m app.daemon --once     # single run then exit (useful for cron)
    python -m app.daemon --dry-run  # run pipeline but do not write or send

Environment / config keys handled here
---------------------------------------
See config.py for the full list.  Daemon-specific keys:
    HERMES_PIPELINE_INTERVAL_S      (default 60)
    HERMES_PIPELINE_WINDOW_MIN      (default 5)
    HERMES_SALIENCE_THRESHOLD       (default 0.0 — store everything scored)
    HERMES_ESCALATION_THRESHOLD     (default 0.7)
    HERMES_ESCALATION_ENDPOINT      (default "" — offline mode)
    HERMES_PRIVACY_ALLOWLIST        (comma-separated field names)
    HERMES_DB_PATH                  (default ~/hermes-data/db/hermes.sqlite3)
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import signal
import sys
import time
from pathlib import Path
from typing import List, Optional

# Pipeline modules
from .pipeline.normalizer        import normalize
from .pipeline.candidate_builder import build_candidates
from .pipeline.salience_scorer   import score_all
from .pipeline.compressor        import compress_all
from .pipeline.privacy_router    import route
from .pipeline.context_store     import ContextStore
from .pipeline.types             import HomeEvent, MemoryCandidate
from .escalation.cloud_client    import EscalationClient
from .llm.local_llm              import LocalLLM
from .config                     import load_config

log = logging.getLogger(__name__)

_SHUTDOWN = False


def _handle_sigterm(signum, frame):
    global _SHUTDOWN
    log.info("daemon: received signal %d — shutting down after current cycle", signum)
    _SHUTDOWN = True


# ---------------------------------------------------------------------------
# Status file — written after every cycle for dashboard consumption
# ---------------------------------------------------------------------------

def _write_status(status_path: Path, status: dict) -> None:
    tmp = status_path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        tmp.replace(status_path)
    except Exception as exc:
        log.warning("daemon: could not write status file: %s", exc)


# ---------------------------------------------------------------------------
# Omi queue: events injected via HTTP (context router writes here)
# ---------------------------------------------------------------------------

def _drain_omi_queue(omi_queue_path: Path) -> List[HomeEvent]:
    """
    Read and clear the Omi event queue file.

    The context router appends HomeEvent JSON lines to this file when
    /context/ingest is called.  The daemon drains it each cycle.
    If the file does not exist, returns empty list.
    """
    if not omi_queue_path.exists():
        return []

    events: List[HomeEvent] = []
    tmp = omi_queue_path.with_suffix(".drain")
    try:
        omi_queue_path.rename(tmp)
    except Exception as exc:
        log.warning("daemon: could not rename omi queue: %s", exc)
        return []

    try:
        for line in tmp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                from .pipeline.types import HomeEvent as HE
                events.append(HE(
                    ts_utc=d["ts_utc"],
                    source=d["source"],
                    kind=d["kind"],
                    value=d.get("value", {}),
                    raw_ref=d.get("raw_ref"),
                    ingested_at=d.get("ingested_at", ""),
                ))
            except Exception as exc:
                log.warning("daemon: malformed omi queue line: %s", exc)
    except Exception as exc:
        log.error("daemon: could not read omi queue drain file: %s", exc)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass

    if events:
        log.info("daemon: drained %d omi events from queue", len(events))
    return events


# ---------------------------------------------------------------------------
# Single pipeline cycle
# ---------------------------------------------------------------------------

def run_cycle(
    *,
    db_path: Path,
    window_min: int,
    salience_threshold: float,
    escalation_threshold: float,
    allowlist_str: str,
    destination: str,
    store: ContextStore,
    client: EscalationClient,
    omi_queue_path: Path,
    prev_radar_target: Optional[bool],
    dry_run: bool,
    llm: Optional[LocalLLM] = None,
) -> dict:
    """
    Execute one pipeline cycle.  Returns a status dict.
    """
    cycle_start = time.monotonic()
    ts_run = datetime.datetime.now(datetime.timezone.utc).isoformat()

    status: dict = {
        "ts_run":            ts_run,
        "events_read":       0,
        "omi_events":        0,
        "candidates_built":  0,
        "candidates_stored": 0,
        "packets_queued":    0,
        "packets_delivered": 0,
        "error":             None,
        "duration_ms":       0,
    }

    try:
        # 1. Normalise sensor events from SQLite.
        sensor_events = normalize(db_path, window_minutes=window_min)
        status["events_read"] = len(sensor_events)

        # 2. Drain Omi queue and merge (Omi always appended AFTER sensor events
        #    so they don't pollute the chronological sort; builder handles mixed).
        omi_events = _drain_omi_queue(omi_queue_path)
        status["omi_events"] = len(omi_events)
        all_events = sensor_events + omi_events
        # Re-sort after merge so bucket assignment is correct.
        all_events.sort(key=lambda e: e.ts_utc)

        # 3. Build candidates.
        candidates = build_candidates(
            all_events,
            window_sec=window_min * 60,
            prev_radar_target=prev_radar_target,
        )
        status["candidates_built"] = len(candidates)

        # 4. Score.
        score_all(candidates, use_llm=False)

        if llm is not None:
            compress_all(candidates, llm)

        # Privacy route — produces escalation packets.
        packets = route(
            candidates,
            escalation_threshold=escalation_threshold,
            allowlist_str=allowlist_str,
            destination=destination,
        )
        status["packets_queued"] = len(packets)

        if not dry_run:
            # 7. Store.
            written = store.append_all(
                [c for c in candidates if (c.salience or 0.0) >= salience_threshold]
            )
            status["candidates_stored"] = written

            # 8. Deliver.
            delivered = client.send_all(packets)
            status["packets_delivered"] = delivered
        else:
            log.info("daemon: dry-run — skipping store and deliver")

    except Exception as exc:
        log.exception("daemon: cycle error: %s", exc)
        status["error"] = str(exc)

    status["duration_ms"] = int((time.monotonic() - cycle_start) * 1000)
    log.info(
        "daemon: cycle done events=%d candidates=%d stored=%d escalated=%d duration=%dms",
        status["events_read"], status["candidates_built"],
        status["candidates_stored"], status["packets_delivered"],
        status["duration_ms"],
    )
    return status


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="HERMES home-AI pipeline daemon")
    parser.add_argument("--once",    action="store_true", help="Run one cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Run pipeline but skip store and deliver")
    args = parser.parse_args(argv)

    cfg = load_config()

    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT,  _handle_sigterm)

    log.info("daemon: starting (interval=%ds window=%dmin)",
             cfg.pipeline_interval_s, cfg.pipeline_window_min)

    # Resolve paths.
    db_path        = cfg.hermes_db_path
    store_dir      = cfg.data_dir / "context"
    queue_dir      = cfg.data_dir / "escalation"
    omi_queue_path = cfg.data_dir / "omi_queue.jsonl"
    status_path    = cfg.data_dir / "pipeline_status.json"

    store = ContextStore(
        store_dir=store_dir,
        salience_threshold=cfg.salience_threshold,
    )
    client = EscalationClient(
        endpoint=cfg.escalation_endpoint,
        queue_dir=queue_dir,
    )

    # Attempt to flush any queued packets from previous run.
    if client.enabled:
        flushed = client.flush_queue()
        if flushed:
            log.info("daemon: flushed %d queued packets on startup", flushed)

    llm: Optional[LocalLLM] = None
    if cfg.compression_enabled:
        llm = LocalLLM(model_path=cfg.model_path, llama_bin=cfg.llama_bin)
        if llm.model_path.exists():
            log.info("daemon: compression enabled — model at %s", cfg.model_path)
        else:
            log.warning(
                "daemon: HERMES_COMPRESSION_ENABLED=true but model not found at %s "
                "— compression will be skipped each cycle",
                cfg.model_path,
            )

    prev_radar_target: Optional[bool] = None

    while not _SHUTDOWN:
        status = run_cycle(
            db_path=db_path,
            window_min=cfg.pipeline_window_min,
            salience_threshold=cfg.salience_threshold,
            escalation_threshold=cfg.escalation_threshold,
            allowlist_str=cfg.privacy_allowlist,
            destination=cfg.escalation_destination,
            store=store,
            client=client,
            omi_queue_path=omi_queue_path,
            prev_radar_target=prev_radar_target,
            dry_run=args.dry_run,
            llm=llm,
        )
        _write_status(status_path, status)

        if args.once or _SHUTDOWN:
            break

        time.sleep(cfg.pipeline_interval_s)

    log.info("daemon: stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
