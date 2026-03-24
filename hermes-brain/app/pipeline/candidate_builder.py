"""
Candidate builder: list[HomeEvent] → list[MemoryCandidate].

Responsibilities
----------------
- Group HomeEvents into fixed-size, epoch-aligned UTC time buckets.
- For each non-empty bucket, produce a MemoryCandidate.
- Detect simple notable transitions within each bucket and record them as
  tags on the candidate.  No conclusions — only flags.
- Assign deterministic candidate_ids so that re-running the pipeline over
  the same window produces the same IDs (enabling dedup in context_store).
- Leave salience=None and summary=None.  Those are set by later stages.

What this module does NOT do
-----------------------------
- Score salience (that is salience_scorer's job).
- Interpret patterns across multiple buckets (future retrieval layer).
- Remove or summarise any event data.

Bucket alignment
----------------
Buckets are aligned to the UTC epoch in multiples of window_sec:

    bucket_index = int(ts_unix // window_sec)
    bucket_start = bucket_index * window_sec          (inclusive)
    bucket_end   = (bucket_index + 1) * window_sec    (exclusive)

A 5-minute (300 s) bucket at 14:07:23 UTC belongs to bucket 14:05–14:10.
All events that fall in the same bucket_index go into the same candidate.

Candidate ID
------------
    "w{window_sec}_{bucket_index}"

e.g. "w300_5913518" for a 5-minute bucket.

Deterministic — re-running the pipeline over the same input with the same
window_sec always produces the same candidate_id.  The window_sec prefix
prevents collisions if the configured window size changes between daemon
restarts (e.g. w300_5913518 and w600_5913518 are different time ranges and
would both be valid, distinct candidates).

Tags emitted by this module
---------------------------
"presence_onset"    first radar row in bucket has target != 0 after a
                    preceding window with target == 0 (transition into presence)
"presence_cleared"  first radar row has target == 0 after target != 0
"co2_elevated"      any co2 reading > CO2_ELEVATED_PPM (threshold, not spike)
"co2_spike"         max - min CO2 within bucket > CO2_SPIKE_DELTA
"temp_drift"        max - min temp within bucket > TEMP_DRIFT_C
"multi_source"      bucket contains events from 3+ distinct sources
"omi_present"       at least one omi-sourced event is in this bucket
"""
from __future__ import annotations

import datetime
import logging
from typing import Dict, List, Optional, Tuple

from .types import SCHEMA_VERSION

from .types import (
    HomeEvent,
    KIND_CO2,
    KIND_PRESENCE,
    KIND_TEMPERATURE,
    MemoryCandidate,
    SOURCE_OMI,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds — intentionally loose.  These flag *candidates*, not alerts.
# The salience scorer applies weights; the builder just names the pattern.
# ---------------------------------------------------------------------------
CO2_ELEVATED_PPM: float = 1000.0   # SGP30 baseline ~400–450 ppm in clean air
CO2_SPIKE_DELTA: float  = 300.0    # delta within a single bucket
TEMP_DRIFT_C: float     = 1.5      # within a single 5-min bucket

PIPELINE_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _bucket_boundaries(bucket_index: int, window_sec: int) -> Tuple[str, str]:
    """Return (ts_start_iso, ts_end_iso) for a bucket given its index."""
    start_epoch = bucket_index * window_sec
    end_epoch   = (bucket_index + 1) * window_sec
    start_dt = datetime.datetime.fromtimestamp(start_epoch, tz=datetime.timezone.utc)
    end_dt   = datetime.datetime.fromtimestamp(end_epoch,   tz=datetime.timezone.utc)
    return start_dt.isoformat(), end_dt.isoformat()


def _ts_to_epoch(ts_utc: str) -> float:
    """Parse an ISO 8601 UTC string to a Unix epoch float."""
    text = ts_utc.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Tag detection helpers — each operates on the events in one bucket only
# ---------------------------------------------------------------------------

def _tag_presence(
    events: List[HomeEvent],
    prev_bucket_had_target: Optional[bool],
) -> List[str]:
    """Detect presence onset / cleared transitions."""
    radar_events = [e for e in events if e.kind == KIND_PRESENCE]
    if not radar_events:
        return []

    tags: List[str] = []
    # Use the first radar row in this bucket as the "current state".
    first = radar_events[0]
    current_target = bool(first.value.get("target", 0))

    if prev_bucket_had_target is not None:
        if current_target and not prev_bucket_had_target:
            tags.append("presence_onset")
        elif not current_target and prev_bucket_had_target:
            tags.append("presence_cleared")

    return tags


def _tag_co2(events: List[HomeEvent]) -> List[str]:
    co2_vals = [
        e.value["eco2_ppm"]
        for e in events
        if e.kind == KIND_CO2 and "eco2_ppm" in e.value
    ]
    if not co2_vals:
        return []
    tags: List[str] = []
    if max(co2_vals) > CO2_ELEVATED_PPM:
        tags.append("co2_elevated")
    if (max(co2_vals) - min(co2_vals)) > CO2_SPIKE_DELTA:
        tags.append("co2_spike")
    return tags


def _tag_temp(events: List[HomeEvent]) -> List[str]:
    temp_vals = [
        e.value["temp_c"]
        for e in events
        if e.kind == KIND_TEMPERATURE and "temp_c" in e.value
    ]
    if not temp_vals:
        return []
    if (max(temp_vals) - min(temp_vals)) > TEMP_DRIFT_C:
        return ["temp_drift"]
    return []


def _tag_source_mix(source_mix: List[str]) -> List[str]:
    tags: List[str] = []
    if len(source_mix) >= 3:
        tags.append("multi_source")
    if SOURCE_OMI in source_mix:
        tags.append("omi_present")
    return tags


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def build_candidates(
    events: List[HomeEvent],
    window_sec: int = 300,
    prev_radar_target: Optional[bool] = None,
) -> List[MemoryCandidate]:
    """
    Group HomeEvents into epoch-aligned time buckets and produce one
    MemoryCandidate per non-empty bucket.

    Parameters
    ----------
    events
        Flat chronologically-sorted list from normalizer (or mixed with Omi
        events injected by omi_adapter).
    window_sec
        Bucket width in seconds.  Default 300 (5 minutes).
    prev_radar_target
        The last known radar target state from the previous pipeline run.
        Used to detect presence transitions at bucket boundaries.
        None means unknown (transition tags are suppressed).

    Returns
    -------
    List[MemoryCandidate]
        One candidate per non-empty bucket, in ascending ts_start order.
        Salience is None.  Summary is None.  Escalate is False.
    """
    if not events:
        return []

    # --- Group events into buckets ---
    buckets: Dict[int, List[HomeEvent]] = {}
    for event in events:
        try:
            epoch = _ts_to_epoch(event.ts_utc)
        except Exception:
            log.warning("candidate_builder: unparseable ts_utc %r — skipping", event.ts_utc)
            continue
        bucket_index = int(epoch // window_sec)
        buckets.setdefault(bucket_index, []).append(event)

    created_at = _utc_now_iso()
    candidates: List[MemoryCandidate] = []

    # Track radar state across buckets within this call so that
    # presence_onset/cleared can span bucket boundaries in a single run.
    current_prev_target = prev_radar_target

    for bucket_index in sorted(buckets.keys()):
        bucket_events = buckets[bucket_index]
        ts_start, ts_end = _bucket_boundaries(bucket_index, window_sec)

        source_mix = sorted({e.source for e in bucket_events})

        tags: List[str] = []
        tags.extend(_tag_presence(bucket_events, current_prev_target))
        tags.extend(_tag_co2(bucket_events))
        tags.extend(_tag_temp(bucket_events))
        tags.extend(_tag_source_mix(source_mix))

        # Update running radar state for next bucket.
        radar_rows = [e for e in bucket_events if e.kind == KIND_PRESENCE]
        if radar_rows:
            current_prev_target = bool(radar_rows[-1].value.get("target", 0))

        candidate = MemoryCandidate(
            candidate_id=f"w{window_sec}_{bucket_index}",
            ts_start=ts_start,
            ts_end=ts_end,
            events=bucket_events,
            source_mix=source_mix,
            tags=tags,
            salience=None,
            summary=None,
            escalate=False,
            provenance={
                "schema_version":   SCHEMA_VERSION,
                "pipeline_version": PIPELINE_VERSION,
                "created_at":       created_at,
                "window_sec":       window_sec,
                "event_count":      len(bucket_events),
            },
        )
        candidates.append(candidate)

    log.debug(
        "candidate_builder: %d events → %d candidates (window=%ds)",
        len(events), len(candidates), window_sec,
    )
    return candidates
