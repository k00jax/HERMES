"""
Core data types for the HERMES home-AI pipeline.

These three types are the backbone of the branch.  They must remain stable
while everything else evolves around them.

Design rules encoded here:
- raw_ref is NEVER optional on sensor-sourced events (only on synthesised ones).
- value holds the raw payload dict — no interpretation, no field removal.
- MemoryCandidate carries a salience score, not a semantic conclusion.
- EscalationPacket records which fields were allowed through the privacy filter
  so the audit trail is part of the artifact, not a separate log.
- All timestamps are ISO 8601 UTC strings throughout.  No float epochs in
  the type layer (they live only inside time-arithmetic helpers).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Source constants — exhaustive list of values that HomeEvent.source may hold.
# Omi is explicitly separate from all sensor sources.
# ---------------------------------------------------------------------------
SOURCE_ENV     = "env"       # SHT31 temperature / humidity
SOURCE_AIR     = "air"       # SGP30 CO2 / VOC
SOURCE_RADAR   = "radar"     # LD2410B mmWave presence
SOURCE_HB      = "hb"        # nRF heartbeat
SOURCE_OMI     = "omi"       # External Omi memory blob — never sensor truth
SOURCE_SYSTEM  = "system"    # Pipeline-internal synthetic events

# Closed set of kind values for sensor-derived events.
# Omi events use free-form kind strings set by the adapter.
KIND_TEMPERATURE = "temperature"
KIND_HUMIDITY    = "humidity"
KIND_CO2         = "co2"
KIND_VOC         = "voc"
KIND_PRESENCE    = "presence"
KIND_HEARTBEAT   = "heartbeat"


@dataclass
class HomeEvent:
    """
    A single normalised observation from one sensor or one external source.

    Fields
    ------
    ts_utc      ISO 8601 UTC string — the sensor's own timestamp (from ts_utc
                column), not the pipeline's processing time.
    source      One of the SOURCE_* constants.  Omi events always carry
                SOURCE_OMI so they can never be confused with sensor truth.
    kind        Semantic label (KIND_* constant for sensor events; free string
                for Omi events).
    value       Raw payload dict.  Contains exactly the fields that the sensor
                produced, with no transformation, no rounding, no deletion.
                The pipeline must never modify this dict after construction.
    raw_ref     Stable pointer back to the SQLite source row in the form
                "{table}:{rowid}" (e.g. "env:4291").  None only for events
                that have no SQLite origin (Omi, synthetic system events).
    ingested_at ISO 8601 UTC string recording when the pipeline first saw this
                event.  Distinct from ts_utc so that Omi clock drift or late
                delivery is always visible.
    """
    ts_utc:      str
    source:      str
    kind:        str
    value:       Dict[str, Any]
    raw_ref:     Optional[str]          # "{table}:{rowid}" or None
    ingested_at: str                    # pipeline wall-clock, ISO 8601 UTC


@dataclass
class MemoryCandidate:
    """
    A time-windowed bundle of HomeEvents that may be worth remembering.

    A candidate is produced by the candidate_builder for every non-empty
    time bucket.  The salience_scorer then assigns a score.  High-salience
    candidates are routed to the privacy layer and optionally escalated.

    Fields
    ------
    candidate_id  Deterministic string: "{bucket_index}_{first_source_hash}".
                  NOT a random UUID — deterministic IDs mean re-running the
                  pipeline over the same window produces the same candidate_id,
                  allowing the context_store to deduplicate without a separate
                  dedup pass.
    ts_start      ISO 8601 UTC — inclusive window start (aligned bucket edge).
    ts_end        ISO 8601 UTC — exclusive window end (aligned bucket edge).
    events        All HomeEvents that fell inside this time bucket.  Order is
                  chronological by ts_utc.  Never empty when stored.
    source_mix    Sorted list of unique source values across all events.
                  Downstream can quickly see "radar + env + omi" without
                  iterating events.
    tags          Rule-assigned labels (e.g. "presence_onset", "co2_spike",
                  "omi_input").  Multiple tags may be applied; the scorer never
                  collapses them into a single conclusion.
    salience      Float in [0.0, 1.0].  Sum of rule contributions, clamped.
                  Absent (None) until salience_scorer has run.
    summary       Optional free-text produced by the compressor (local LLM or
                  rule-based).  None until a compressor runs.  Storing None is
                  valid — a candidate without a summary is still useful.
    escalate      True if salience >= ESCALATION_THRESHOLD and privacy router
                  has approved.  Set by privacy_router, not scorer.
    provenance    Arbitrary metadata dict for downstream trust assessment.
                  Must include at minimum:
                    pipeline_version, created_at (ISO UTC), window_sec.
    """
    candidate_id: str
    ts_start:     str
    ts_end:       str
    events:       List[HomeEvent]
    source_mix:   List[str]
    tags:         List[str]
    salience:     Optional[float]       # None until scored
    summary:      Optional[str]         # None until compressor runs
    escalate:     bool
    provenance:   Dict[str, Any]


@dataclass
class EscalationPacket:
    """
    A privacy-filtered bundle ready to send to a downstream reasoner.

    Constructed by privacy_router from a MemoryCandidate that has
    escalate=True.  The packet records exactly which fields were permitted
    to leave HERMES so the decision is auditable at the receiving end.

    Fields
    ------
    packet_id       UUID4 string.  Random (not deterministic) — each
                    escalation attempt produces a new packet even for the same
                    candidate, so retransmission is distinguishable.
    created_at      ISO 8601 UTC — when the packet was constructed.
    candidate_id    Back-reference to the MemoryCandidate that spawned this.
    summary         The candidate's summary (may be None if compressor has not
                    run yet — the packet is still valid, just less rich).
    tags            Copied from the candidate unchanged.
    salience        Copied from the candidate.
    source_mix      Copied from the candidate.
    payload         The privacy-filtered event payload.  Contains only fields
                    that appear in the configured PRIVACY_ALLOWLIST.  Raw
                    sensor readings (e.g. exact distance in cm) are excluded
                    unless the allowlist explicitly includes them.
    allowed_fields  Sorted list of field names that were permitted through the
                    privacy filter.  Stored inside the packet so the downstream
                    model knows the provenance of what it received.
    stripped_fields Sorted list of field names that were present but removed.
                    Stored for local audit only — this list must NOT be sent
                    to the cloud endpoint.
    destination     Configured endpoint name (not the full URL) for routing.
    """
    packet_id:      str
    created_at:     str
    candidate_id:   str
    summary:        Optional[str]
    tags:           List[str]
    salience:       float
    source_mix:     List[str]
    payload:        Dict[str, Any]
    allowed_fields: List[str]
    stripped_fields: List[str]          # local audit only
    destination:    str
