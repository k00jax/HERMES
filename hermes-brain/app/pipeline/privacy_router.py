"""
Privacy router: apply a field allowlist before any data leaves HERMES.

Design
------
Every EscalationPacket records both which fields were permitted (allowed_fields)
and which were stripped (stripped_fields).  The stripped list is stored locally
only — it must not be transmitted to the cloud endpoint.

Allowlist format (config string, comma-separated)
-------------------------------------------------
    "ts_start,ts_end,source_mix,tags,salience,summary"

Allowlist governs which *top-level keys* of the payload dict are transmitted.
It does NOT govern the events list (which is never transmitted — only the
summary and tags derived from it are).

The payload sent in an EscalationPacket is:
    {
      "ts_start":   ...,
      "ts_end":     ...,
      "source_mix": [...],
      "tags":       [...],
      "salience":   ...,
      "summary":    ...,   # only if compressor has run
    }

Raw sensor values (temp_c, eco2_ppm, detect_cm, etc.) are NOT in the default
allowlist.  They stay on-device.  If you want to escalate raw values, add the
relevant field name to the allowlist explicitly.

Omi-sourced events are subject to the same filter as everything else.  The
"omi_present" tag tells the downstream reasoner that Omi data contributed,
but the Omi event payloads are not transmitted unless raw fields are in the
allowlist.
"""
from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any, Dict, List, Optional

from .types import EscalationPacket, MemoryCandidate

log = logging.getLogger(__name__)

# Fields that are always allowed regardless of allowlist config.
# These are structural (not sensor data) and needed for routing.
_ALWAYS_ALLOWED = {"ts_start", "ts_end", "source_mix", "tags", "salience"}

# Default allowlist (can be overridden via config).
DEFAULT_ALLOWLIST = "ts_start,ts_end,source_mix,tags,salience,summary"


def _parse_allowlist(allowlist_str: str) -> set[str]:
    parts = {f.strip() for f in allowlist_str.split(",") if f.strip()}
    return parts | _ALWAYS_ALLOWED


def _build_full_payload(candidate: MemoryCandidate) -> Dict[str, Any]:
    """
    Assemble the full unfiltered payload from a candidate.
    This is what we would send if there were no privacy filter.
    """
    return {
        "ts_start":   candidate.ts_start,
        "ts_end":     candidate.ts_end,
        "source_mix": candidate.source_mix,
        "tags":       candidate.tags,
        "salience":   candidate.salience,
        "summary":    candidate.summary,
        "candidate_id": candidate.candidate_id,
    }


def build_escalation_packet(
    candidate: MemoryCandidate,
    allowlist_str: str = DEFAULT_ALLOWLIST,
    destination: str = "default",
) -> EscalationPacket:
    """
    Apply the privacy allowlist to a MemoryCandidate and produce an
    EscalationPacket.

    Parameters
    ----------
    candidate
        A MemoryCandidate with escalate=True and a valid salience score.
    allowlist_str
        Comma-separated field names permitted to leave HERMES.
    destination
        Configured endpoint name (not URL).  Used for routing.

    Returns
    -------
    EscalationPacket
        Ready to hand to cloud_client.  The stripped_fields list is
        present for local audit only.
    """
    allowed_set = _parse_allowlist(allowlist_str)
    full_payload = _build_full_payload(candidate)

    filtered: Dict[str, Any] = {}
    allowed_fields: List[str] = []
    stripped_fields: List[str] = []

    for field, value in full_payload.items():
        if field in allowed_set:
            filtered[field] = value
            allowed_fields.append(field)
        else:
            stripped_fields.append(field)
            log.debug("privacy_router: stripped field=%s from candidate=%s", field, candidate.candidate_id)

    if stripped_fields:
        log.info(
            "privacy_router: candidate=%s stripped=%s",
            candidate.candidate_id, stripped_fields,
        )

    return EscalationPacket(
        packet_id=str(uuid.uuid4()),
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        candidate_id=candidate.candidate_id,
        summary=candidate.summary,
        tags=candidate.tags,
        salience=candidate.salience if candidate.salience is not None else 0.0,
        source_mix=candidate.source_mix,
        payload=filtered,
        allowed_fields=sorted(allowed_fields),
        stripped_fields=sorted(stripped_fields),
        destination=destination,
    )


def route(
    candidates: List[MemoryCandidate],
    escalation_threshold: float,
    allowlist_str: str = DEFAULT_ALLOWLIST,
    destination: str = "default",
) -> List[EscalationPacket]:
    """
    Filter candidates that meet the escalation threshold, apply the privacy
    filter, and return EscalationPackets.

    Also sets candidate.escalate = True on candidates that pass.

    Parameters
    ----------
    candidates
        Scored MemoryCandidate list.
    escalation_threshold
        Minimum salience to escalate.
    allowlist_str
        Privacy allowlist (comma-separated field names).
    destination
        Endpoint name for routing.

    Returns
    -------
    List[EscalationPacket]
        One packet per candidate that passed the threshold.
        Empty list if no candidates qualify.
    """
    packets: List[EscalationPacket] = []
    for c in candidates:
        if c.salience is None:
            continue
        if c.salience < escalation_threshold:
            continue
        c.escalate = True
        packet = build_escalation_packet(c, allowlist_str=allowlist_str, destination=destination)
        packets.append(packet)
        log.info(
            "privacy_router: queued candidate=%s salience=%.3f dest=%s",
            c.candidate_id, c.salience, destination,
        )
    return packets
