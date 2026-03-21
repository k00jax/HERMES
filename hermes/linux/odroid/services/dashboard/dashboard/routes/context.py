"""
Context router: /context/* endpoints for the home-AI pipeline.

These endpoints expose pipeline status and candidate data produced by the
hermes-brain daemon.  Communication between the dashboard (this process) and
the daemon (separate process) is purely file-based:

    ~/hermes-data/pipeline_status.json   — written by daemon after each cycle
    ~/hermes-data/context/               — JSONL candidate store written by daemon
    ~/hermes-data/omi_queue.jsonl        — written here, drained by daemon

No imports from hermes-brain.  No shared in-process state.  This keeps the
dashboard stable regardless of whether the daemon is running.

Endpoints
---------
GET  /context/status       Pipeline health: last run, event counts, errors.
GET  /context/candidates   Recent memory candidates (query params: limit, min_salience, tag).
POST /context/ingest       Accept an Omi memory blob and queue it for the daemon.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["context"])

# ---------------------------------------------------------------------------
# Path resolution — mirrors daemon path logic exactly.
# Override with HERMES_DATA_DIR env var if needed.
# ---------------------------------------------------------------------------

def _data_dir() -> Path:
    return Path(os.environ.get("HERMES_DATA_DIR", os.path.expanduser("~/hermes-data")))


def _status_path() -> Path:
    return _data_dir() / "pipeline_status.json"


def _context_dir() -> Path:
    return _data_dir() / "context"


def _omi_queue_path() -> Path:
    return _data_dir() / "omi_queue.jsonl"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_status() -> Optional[Dict[str, Any]]:
    path = _status_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_candidates(
    limit: int,
    min_salience: float,
    tag_filter: Optional[str],
) -> List[Dict[str, Any]]:
    context_dir = _context_dir()
    if not context_dir.exists():
        return []

    results: List[Dict[str, Any]] = []
    paths = sorted(context_dir.glob("candidates_*.jsonl"), reverse=True)

    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            sal = obj.get("salience")
            if sal is not None and float(sal) < min_salience:
                continue
            if tag_filter and tag_filter not in obj.get("tags", []):
                continue
            # Strip the events list for the API response — candidates can be
            # large.  Expose event count and source_mix instead.
            condensed = {
                "candidate_id": obj.get("candidate_id"),
                "ts_start":     obj.get("ts_start"),
                "ts_end":       obj.get("ts_end"),
                "salience":     obj.get("salience"),
                "tags":         obj.get("tags", []),
                "source_mix":   obj.get("source_mix", []),
                "summary":      obj.get("summary"),
                "escalate":     obj.get("escalate", False),
                "event_count":  len(obj.get("events", [])),
                "provenance":   obj.get("provenance", {}),
            }
            results.append(condensed)
            if len(results) >= limit:
                return results
    return results


def _append_omi_queue(event_dicts: List[Dict[str, Any]]) -> int:
    """
    Append HomeEvent dicts to the Omi queue file.
    Returns count of events written.
    """
    path = _omi_queue_path()
    try:
        with path.open("a", encoding="utf-8") as fh:
            for d in event_dicts:
                fh.write(json.dumps(d, ensure_ascii=False) + "\n")
        return len(event_dicts)
    except Exception as exc:
        raise RuntimeError(f"omi queue write failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/context/status")
async def context_status() -> JSONResponse:
    """
    Return the last pipeline cycle status written by the daemon.

    If the daemon has never run, returns a 'not_started' status rather than
    a 404, so callers can distinguish "daemon not running" from "route missing".
    """
    status = _read_status()
    if status is None:
        return JSONResponse({
            "daemon_status": "not_started",
            "note": "pipeline_status.json not found — daemon may not have run yet",
            "candidate_count": 0,
        })

    # Enrich with live candidate count.
    context_dir = _context_dir()
    total = 0
    if context_dir.exists():
        for p in context_dir.glob("candidates_*.jsonl"):
            try:
                total += sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())
            except Exception:
                pass

    return JSONResponse({
        "daemon_status": "ok" if status.get("error") is None else "error",
        "last_run":      status.get("ts_run"),
        "events_read":   status.get("events_read", 0),
        "omi_events":    status.get("omi_events", 0),
        "candidates_built":  status.get("candidates_built", 0),
        "candidates_stored": status.get("candidates_stored", 0),
        "packets_queued":    status.get("packets_queued", 0),
        "packets_delivered": status.get("packets_delivered", 0),
        "duration_ms":   status.get("duration_ms", 0),
        "error":         status.get("error"),
        "candidate_count_total": total,
    })


@router.get("/context/candidates")
async def context_candidates(
    limit:        int   = Query(default=20,  ge=1, le=200),
    min_salience: float = Query(default=0.0, ge=0.0, le=1.0),
    tag:          Optional[str] = Query(default=None),
) -> JSONResponse:
    """
    Return recent memory candidates, newest first.

    Events within each candidate are NOT included in the response to keep
    payloads small.  event_count and source_mix are included instead.

    Query params
    ------------
    limit         Max candidates to return (1–200, default 20).
    min_salience  Only return candidates with salience >= this value.
    tag           If set, only return candidates carrying this tag.
    """
    candidates = _read_candidates(limit=limit, min_salience=min_salience, tag_filter=tag)
    return JSONResponse({
        "count": len(candidates),
        "candidates": candidates,
    })


@router.post("/context/ingest")
async def context_ingest(request: Request) -> JSONResponse:
    """
    Accept an Omi memory blob (or batch) and queue it for the daemon.

    The payload is validated minimally here and passed to the Omi adapter
    for parsing.  The resulting HomeEvent dicts are appended to the Omi queue
    file, which the daemon drains on its next cycle.

    Accepted formats — see hermes-brain/app/ingest/omi_adapter.py docstring.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="request body must be valid JSON")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be a JSON object")

    # Import the adapter at call time so the dashboard process does not depend
    # on hermes-brain being importable at startup.  If hermes-brain is not on
    # the Python path, return a clear error.
    try:
        # Try relative import first (when running as part of a combined package).
        import importlib
        omi_mod = importlib.import_module("app.ingest.omi_adapter")
        parse_fn = omi_mod.parse_omi_payload
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="omi_adapter not available — hermes-brain not on Python path",
        )

    events = parse_fn(payload)
    if not events:
        return JSONResponse({"queued": 0, "note": "no valid events parsed from payload"})

    # Serialise events to dicts for queue file.
    event_dicts = [
        {
            "ts_utc":      e.ts_utc,
            "source":      e.source,
            "kind":        e.kind,
            "value":       e.value,
            "raw_ref":     e.raw_ref,
            "ingested_at": e.ingested_at,
        }
        for e in events
    ]

    try:
        written = _append_omi_queue(event_dicts)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return JSONResponse({"queued": written})
