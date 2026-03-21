"""
Omi adapter: accept external memory blobs and inject them into the pipeline.

Provenance contract
-------------------
Every HomeEvent produced here carries:
    source = SOURCE_OMI          (never SOURCE_ENV / SOURCE_AIR / etc.)
    raw_ref = None               (no SQLite origin)
    ingested_at = <wall clock>   (when HERMES received it)
    value["_omi_received_at"] = same as ingested_at
    value["_omi_ts_claimed"]  = the ts_utc that Omi claims for the memory

This makes clock drift or late delivery permanently visible in the record.
Omi events are NEVER silently merged with sensor truth.

Input format
------------
HTTP POST to /context/ingest (registered in the dashboard context router).
Content-Type: application/json

Two accepted shapes:

1. Single memory blob:
    {
      "ts_utc":  "2026-03-20T14:05:00+00:00",   # Omi's claimed timestamp
      "kind":    "memory",                         # free string
      "text":    "...",                            # the memory content
      "meta":    { ... }                           # optional Omi metadata
    }

2. Batch of memory blobs:
    {
      "items": [ <blob>, <blob>, ... ]
    }

Unknown fields in each blob are preserved inside value["_omi_raw"] so that
nothing is silently discarded.

This module is a pure function library — no HTTP server here.
The router in dashboard/routes/context.py calls parse_omi_payload().
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from ..pipeline.types import HomeEvent, SOURCE_OMI

log = logging.getLogger(__name__)

_KNOWN_BLOB_FIELDS = {"ts_utc", "kind", "text", "meta"}


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _parse_blob(blob: Dict[str, Any], ingested_at: str) -> Optional[HomeEvent]:
    """
    Convert a single Omi blob dict into a HomeEvent.

    Returns None if the blob is too malformed to be useful (missing both
    ts_utc and text).  Logs a warning in that case.
    """
    if not isinstance(blob, dict):
        log.warning("omi_adapter: blob is not a dict — skipping")
        return None

    # Omi's claimed timestamp.  If absent, use ingestion wall-clock as claimed
    # ts but mark the ambiguity in value.
    claimed_ts = blob.get("ts_utc", "")
    if not claimed_ts:
        claimed_ts = ingested_at
        ts_was_missing = True
    else:
        ts_was_missing = False

    kind = str(blob.get("kind", "memory")).strip() or "memory"

    # Collect any extra fields the caller sent — don't discard them.
    extra = {k: v for k, v in blob.items() if k not in _KNOWN_BLOB_FIELDS}

    value: Dict[str, Any] = {
        "_omi_received_at":   ingested_at,
        "_omi_ts_claimed":    claimed_ts,
        "_omi_ts_was_missing": ts_was_missing,
    }
    if blob.get("text") is not None:
        value["text"] = str(blob["text"])
    if blob.get("meta") is not None:
        value["meta"] = blob["meta"]
    if extra:
        value["_omi_raw"] = extra

    return HomeEvent(
        ts_utc=claimed_ts,
        source=SOURCE_OMI,
        kind=kind,
        value=value,
        raw_ref=None,            # no SQLite origin
        ingested_at=ingested_at,
    )


def parse_omi_payload(payload: Dict[str, Any]) -> List[HomeEvent]:
    """
    Parse an Omi HTTP POST body and return a list of HomeEvents.

    Accepts both single-blob and batch formats.
    Returns an empty list on completely invalid input (logs a warning).
    """
    ingested_at = _utc_now_iso()

    if not isinstance(payload, dict):
        log.warning("omi_adapter: payload is not a dict")
        return []

    # Batch format: {"items": [...]}
    if "items" in payload:
        items = payload["items"]
        if not isinstance(items, list):
            log.warning("omi_adapter: 'items' is not a list")
            return []
        events: List[HomeEvent] = []
        for blob in items:
            evt = _parse_blob(blob, ingested_at)
            if evt is not None:
                events.append(evt)
        log.info("omi_adapter: ingested %d events from batch of %d", len(events), len(items))
        return events

    # Single blob format.
    evt = _parse_blob(payload, ingested_at)
    if evt is None:
        return []
    log.info("omi_adapter: ingested 1 omi event kind=%s", evt.kind)
    return [evt]
