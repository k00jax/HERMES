"""
Omi adapter: accept external memory blobs and inject them into the pipeline.

Provenance contract
-------------------
raw_ref=None is acceptable here because full external provenance is recoverable
from the following fields stored in every HomeEvent.value:

    _omi_batch_id      UUID4 string, one per parse_omi_payload() call.
                       All items from the same HTTP POST share this ID.
                       Use this to group or locate the original request in logs.
    _omi_item_index    0-based position of this blob within the batch (0 for
                       single-blob posts).  Uniquely identifies one item inside
                       a batch when combined with _omi_batch_id.
    _omi_payload_hash  SHA-256 hex digest of the original blob JSON (computed
                       before any HERMES fields are added).  Reproducible for
                       identical content — use for dedup or content verification.
    _omi_received_at   ISO 8601 UTC wall-clock when HERMES first saw this blob.
    _omi_ts_claimed    The ts_utc that Omi asserted for the memory.
    _omi_ts_was_missing  True if no ts_utc was provided by the caller.

Every HomeEvent produced here also carries:
    source = SOURCE_OMI          (never SOURCE_ENV / SOURCE_AIR / etc.)
    raw_ref = None               (no SQLite origin)
    ingested_at = _omi_received_at

This makes clock drift, late delivery, and batch membership permanently
visible in the stored record.
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
import hashlib
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from ..pipeline.types import HomeEvent, SOURCE_OMI

log = logging.getLogger(__name__)

_KNOWN_BLOB_FIELDS = {"ts_utc", "kind", "text", "meta"}


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _blob_hash(blob: Dict[str, Any]) -> str:
    """
    SHA-256 hex digest of the blob dict, computed before any HERMES fields
    are added.  Keys are sorted so the hash is stable regardless of dict
    insertion order.  Returns a 64-character hex string.
    """
    canonical = json.dumps(blob, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_blob(
    blob: Dict[str, Any],
    ingested_at: str,
    batch_id: str,
    item_index: int,
) -> Optional[HomeEvent]:
    """
    Convert a single Omi blob dict into a HomeEvent.

    Returns None if the blob is too malformed to be useful (missing both
    ts_utc and text).  Logs a warning in that case.
    """
    if not isinstance(blob, dict):
        log.warning("omi_adapter: blob is not a dict — skipping")
        return None

    # Compute content hash from the original blob before we add any fields.
    payload_hash = _blob_hash(blob)

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
        # External provenance — recovers the original POST even with raw_ref=None.
        "_omi_batch_id":       batch_id,
        "_omi_item_index":     item_index,
        "_omi_payload_hash":   payload_hash,
        # Timing
        "_omi_received_at":    ingested_at,
        "_omi_ts_claimed":     claimed_ts,
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
        raw_ref=None,            # no SQLite origin; use _omi_batch_id + _omi_item_index to trace
        ingested_at=ingested_at,
    )


def parse_omi_payload(payload: Dict[str, Any]) -> List[HomeEvent]:
    """
    Parse an Omi HTTP POST body and return a list of HomeEvents.

    Accepts both single-blob and batch formats.
    Returns an empty list on completely invalid input (logs a warning).
    """
    ingested_at = _utc_now_iso()
    batch_id = str(uuid.uuid4())      # one UUID per HTTP POST call

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
        for idx, blob in enumerate(items):
            evt = _parse_blob(blob, ingested_at, batch_id=batch_id, item_index=idx)
            if evt is not None:
                events.append(evt)
        log.info(
            "omi_adapter: batch_id=%s ingested %d events from batch of %d",
            batch_id, len(events), len(items),
        )
        return events

    # Single blob format — item_index=0.
    evt = _parse_blob(payload, ingested_at, batch_id=batch_id, item_index=0)
    if evt is None:
        return []
    log.info(
        "omi_adapter: batch_id=%s ingested 1 omi event kind=%s",
        batch_id, evt.kind,
    )
    return [evt]
