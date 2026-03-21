"""
Cloud escalation client: deliver EscalationPackets to a downstream endpoint.

v1 behaviour
------------
- If no endpoint is configured (empty string or None), log the packet and
  return immediately.  This is the default.  HERMES works fully offline.
- If an endpoint is configured, attempt an HTTP POST.
- Failed deliveries are queued to a local JSONL file and retried on the next
  call to flush_queue().
- The client is intentionally synchronous in v1.  The daemon calls it after
  storing candidates locally, so a slow or failing network call only adds
  latency to one pipeline cycle — it does not block sensor ingestion.

Queue format
------------
Same JSONL pattern used everywhere in HERMES:
    data/escalation/queue_YYYY-MM-DD.jsonl

Queued items older than QUEUE_MAX_DAYS are discarded without delivery.
"""
from __future__ import annotations

import datetime
import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from ..pipeline.types import EscalationPacket

log = logging.getLogger(__name__)

QUEUE_MAX_DAYS = 3
HTTP_TIMEOUT_S = 10
USER_AGENT = "HERMES-Escalation/0.1"


# ---------------------------------------------------------------------------
# Serialisation (stripped_fields must NOT appear in the transmitted body)
# ---------------------------------------------------------------------------

def _packet_to_wire(packet: EscalationPacket) -> Dict[str, Any]:
    """
    Build the dict that is actually transmitted to the cloud endpoint.

    stripped_fields is excluded — it is a local audit field only.
    """
    return {
        "packet_id":      packet.packet_id,
        "created_at":     packet.created_at,
        "candidate_id":   packet.candidate_id,
        "summary":        packet.summary,
        "tags":           packet.tags,
        "salience":       packet.salience,
        "source_mix":     packet.source_mix,
        "payload":        packet.payload,
        "allowed_fields": packet.allowed_fields,
        "destination":    packet.destination,
    }


def _packet_to_queue_record(packet: EscalationPacket) -> Dict[str, Any]:
    """Full record for local queue (includes stripped_fields for audit)."""
    wire = _packet_to_wire(packet)
    wire["stripped_fields"] = packet.stripped_fields   # local only
    wire["_queued_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return wire


# ---------------------------------------------------------------------------
# HTTP delivery
# ---------------------------------------------------------------------------

def _post_packet(endpoint: str, packet: EscalationPacket) -> bool:
    """
    POST a single EscalationPacket to the configured endpoint.

    Returns True on success (2xx response), False on any failure.
    Does NOT raise.
    """
    body = json.dumps(_packet_to_wire(packet), ensure_ascii=False).encode("utf-8")
    req = Request(
        url=endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            status = resp.status
            if 200 <= status < 300:
                log.info(
                    "cloud_client: delivered packet=%s to %s status=%d",
                    packet.packet_id, endpoint, status,
                )
                return True
            log.warning(
                "cloud_client: unexpected status=%d for packet=%s",
                status, packet.packet_id,
            )
            return False
    except HTTPError as exc:
        log.warning("cloud_client: HTTP %d for packet=%s: %s", exc.code, packet.packet_id, exc)
    except URLError as exc:
        log.warning("cloud_client: network error for packet=%s: %s", packet.packet_id, exc)
    except Exception as exc:
        log.error("cloud_client: unexpected error for packet=%s: %s", packet.packet_id, exc)
    return False


# ---------------------------------------------------------------------------
# Local queue
# ---------------------------------------------------------------------------

class EscalationClient:
    """
    Deliver EscalationPackets to a downstream endpoint.

    Parameters
    ----------
    endpoint
        HTTP/HTTPS URL.  Empty string or None means log-only mode (offline).
    queue_dir
        Directory for the local delivery queue.
    """

    def __init__(self, endpoint: str, queue_dir: Path) -> None:
        self.endpoint = endpoint.strip() if endpoint else ""
        self.queue_dir = queue_dir
        self.queue_dir.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return bool(self.endpoint)

    def _queue_path(self) -> Path:
        day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        return self.queue_dir / f"queue_{day}.jsonl"

    def _enqueue(self, packet: EscalationPacket) -> None:
        record = _packet_to_queue_record(packet)
        line = json.dumps(record, ensure_ascii=False)
        try:
            with self._queue_path().open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            log.debug("cloud_client: queued packet=%s", packet.packet_id)
        except Exception as exc:
            log.error("cloud_client: queue write failed for packet=%s: %s", packet.packet_id, exc)

    def _purge_old_queue_files(self) -> None:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=QUEUE_MAX_DAYS)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        for path in sorted(self.queue_dir.glob("queue_*.jsonl")):
            stem = path.stem  # "queue_YYYY-MM-DD"
            parts = stem.split("_", 1)
            if len(parts) == 2 and parts[1] < cutoff_str:
                try:
                    path.unlink()
                    log.info("cloud_client: purged old queue file %s", path.name)
                except Exception as exc:
                    log.warning("cloud_client: could not purge %s: %s", path, exc)

    def send(self, packet: EscalationPacket) -> bool:
        """
        Attempt to deliver a packet.

        If no endpoint is configured: log the packet summary and return True
        (treated as handled — nothing is queued in offline mode by design).

        If delivery fails: enqueue the packet and return False.

        Returns True if the packet was delivered or accepted (offline mode),
        False if it was queued due to delivery failure.
        """
        if not self.enabled:
            log.info(
                "cloud_client: [offline] packet=%s salience=%.3f tags=%s summary=%r",
                packet.packet_id, packet.salience, packet.tags, packet.summary,
            )
            return True

        success = _post_packet(self.endpoint, packet)
        if not success:
            self._enqueue(packet)
        return success

    def send_all(self, packets: List[EscalationPacket]) -> int:
        """
        Send all packets.  Returns count of successfully delivered packets.
        """
        delivered = 0
        for p in packets:
            if self.send(p):
                delivered += 1
        return delivered

    def flush_queue(self) -> int:
        """
        Attempt to deliver all queued packets.

        Removes successfully delivered records from the queue files.
        Returns count of successfully delivered packets.

        Call this at daemon startup or periodically to drain the retry queue.
        Skips files whose date would be past the purge cutoff.
        """
        if not self.enabled:
            return 0

        self._purge_old_queue_files()
        delivered = 0

        for path in sorted(self.queue_dir.glob("queue_*.jsonl")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue

            remaining: List[str] = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Reconstruct a minimal EscalationPacket for retransmission.
                try:
                    packet = EscalationPacket(
                        packet_id=record["packet_id"],
                        created_at=record["created_at"],
                        candidate_id=record["candidate_id"],
                        summary=record.get("summary"),
                        tags=record.get("tags", []),
                        salience=float(record.get("salience", 0.0)),
                        source_mix=record.get("source_mix", []),
                        payload=record.get("payload", {}),
                        allowed_fields=record.get("allowed_fields", []),
                        stripped_fields=record.get("stripped_fields", []),
                        destination=record.get("destination", ""),
                    )
                except Exception as exc:
                    log.warning("cloud_client: malformed queue record: %s", exc)
                    continue

                if _post_packet(self.endpoint, packet):
                    delivered += 1
                else:
                    remaining.append(line)

            # Rewrite the file with only undelivered records.
            try:
                if remaining:
                    path.write_text("\n".join(remaining) + "\n", encoding="utf-8")
                elif path.exists():
                    path.unlink()
            except Exception as exc:
                log.error("cloud_client: could not rewrite queue file %s: %s", path, exc)

        return delivered
