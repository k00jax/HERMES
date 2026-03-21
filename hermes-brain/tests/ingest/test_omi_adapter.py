"""
Tests for ingest/omi_adapter.py.

Invariants locked here:
- source is always SOURCE_OMI, never a sensor source.
- raw_ref is always None (no SQLite origin).
- _omi_batch_id is a UUID4 string present on every event.
- All events from the same parse_omi_payload() call share the same batch_id.
- _omi_item_index is 0-based within the batch.
- Single-blob calls have item_index=0.
- _omi_payload_hash is a 64-char hex SHA-256 of the original blob.
- Identical blobs produce identical hashes (content fingerprint).
- Different blobs produce different hashes.
- Hash is computed from the original blob, before HERMES fields are added.
- _omi_ts_was_missing=True when ts_utc is absent.
- _omi_ts_was_missing=False when ts_utc is present.
- Unknown blob fields are stored in _omi_raw (not silently discarded).
- text field is preserved as a string.
- Invalid payload (not a dict) → empty list.
- items is not a list → empty list.
- blob is not a dict → skipped, others still processed.
"""
from __future__ import annotations
import hashlib
import json
import re

from app.ingest.omi_adapter import parse_omi_payload
from app.pipeline.types import SOURCE_OMI

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _single(blob: dict):
    """Parse a single blob and return (events, event_if_any)."""
    events = parse_omi_payload(blob)
    return events, (events[0] if events else None)


# ---------------------------------------------------------------------------
# Source and raw_ref contract
# ---------------------------------------------------------------------------

def test_single_blob_source_is_omi():
    _, e = _single({"kind": "memory", "text": "hello"})
    assert e is not None
    assert e.source == SOURCE_OMI


def test_single_blob_raw_ref_is_none():
    _, e = _single({"kind": "memory", "text": "hello"})
    assert e.raw_ref is None


def test_batch_events_source_is_omi():
    events = parse_omi_payload({"items": [
        {"text": "a"}, {"text": "b"}
    ]})
    assert all(e.source == SOURCE_OMI for e in events)


def test_batch_events_raw_ref_is_none():
    events = parse_omi_payload({"items": [{"text": "a"}]})
    assert all(e.raw_ref is None for e in events)


# ---------------------------------------------------------------------------
# batch_id
# ---------------------------------------------------------------------------

def test_single_blob_has_batch_id():
    _, e = _single({"text": "hello"})
    assert "_omi_batch_id" in e.value
    assert _UUID_RE.match(e.value["_omi_batch_id"]), "batch_id not UUID4"


def test_batch_items_share_batch_id():
    events = parse_omi_payload({"items": [
        {"text": "item 0"},
        {"text": "item 1"},
        {"text": "item 2"},
    ]})
    assert len(events) == 3
    batch_ids = {e.value["_omi_batch_id"] for e in events}
    assert len(batch_ids) == 1, f"expected one shared batch_id, got {batch_ids}"


def test_different_calls_produce_different_batch_ids():
    blob = {"text": "hello"}
    e1 = parse_omi_payload(blob)[0]
    e2 = parse_omi_payload(blob)[0]
    assert e1.value["_omi_batch_id"] != e2.value["_omi_batch_id"]


# ---------------------------------------------------------------------------
# item_index
# ---------------------------------------------------------------------------

def test_single_blob_item_index_is_zero():
    _, e = _single({"text": "hello"})
    assert e.value["_omi_item_index"] == 0


def test_batch_item_indices_are_sequential():
    events = parse_omi_payload({"items": [
        {"text": "first"},
        {"text": "second"},
        {"text": "third"},
    ]})
    assert len(events) == 3
    indices = [e.value["_omi_item_index"] for e in events]
    assert indices == [0, 1, 2]


# ---------------------------------------------------------------------------
# payload_hash
# ---------------------------------------------------------------------------

def test_payload_hash_is_64_char_hex():
    _, e = _single({"text": "hello", "kind": "memory"})
    h = e.value["_omi_payload_hash"]
    assert isinstance(h, str)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_identical_blobs_produce_identical_hashes():
    blob = {"text": "same content", "kind": "memory", "ts_utc": "2026-01-01T00:00:00+00:00"}
    e1 = parse_omi_payload(blob)[0]
    e2 = parse_omi_payload(blob)[0]
    assert e1.value["_omi_payload_hash"] == e2.value["_omi_payload_hash"]


def test_different_blobs_produce_different_hashes():
    e1 = parse_omi_payload({"text": "content A"})[0]
    e2 = parse_omi_payload({"text": "content B"})[0]
    assert e1.value["_omi_payload_hash"] != e2.value["_omi_payload_hash"]


def test_hash_matches_sha256_of_original_blob():
    """Verify the hash algorithm and canonical form exactly."""
    blob = {"kind": "memory", "text": "verify me"}
    e = parse_omi_payload(blob)[0]
    # The adapter sorts keys and uses ensure_ascii=False before hashing.
    canonical = json.dumps(blob, sort_keys=True, ensure_ascii=False)
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert e.value["_omi_payload_hash"] == expected


def test_hash_is_unaffected_by_hermes_added_fields():
    """The hash is computed from the original blob, not the enriched value."""
    blob = {"text": "original"}
    e = parse_omi_payload(blob)[0]
    # Reconstruct what the hash should be: just the original blob fields.
    canonical = json.dumps(blob, sort_keys=True, ensure_ascii=False)
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert e.value["_omi_payload_hash"] == expected


def test_dict_insertion_order_does_not_affect_hash():
    blob_a = {"text": "hello", "kind": "memory"}
    blob_b = {"kind": "memory", "text": "hello"}   # same keys, different order
    e_a = parse_omi_payload(blob_a)[0]
    e_b = parse_omi_payload(blob_b)[0]
    assert e_a.value["_omi_payload_hash"] == e_b.value["_omi_payload_hash"]


# ---------------------------------------------------------------------------
# Timestamp handling
# ---------------------------------------------------------------------------

def test_ts_was_missing_true_when_no_ts_utc():
    _, e = _single({"text": "no timestamp"})
    assert e.value["_omi_ts_was_missing"] is True


def test_ts_was_missing_false_when_ts_utc_present():
    _, e = _single({"ts_utc": "2026-01-01T00:00:00+00:00", "text": "has timestamp"})
    assert e.value["_omi_ts_was_missing"] is False


def test_ts_utc_used_when_present():
    claimed = "2026-01-01T00:00:00+00:00"
    _, e = _single({"ts_utc": claimed, "text": "hello"})
    assert e.ts_utc == claimed
    assert e.value["_omi_ts_claimed"] == claimed


def test_ingestion_time_used_as_claimed_when_ts_missing():
    _, e = _single({"text": "no ts"})
    # When ts_utc is absent, claimed ts falls back to ingested_at.
    assert e.value["_omi_ts_claimed"] == e.value["_omi_received_at"]


# ---------------------------------------------------------------------------
# Content preservation
# ---------------------------------------------------------------------------

def test_text_field_preserved():
    _, e = _single({"text": "remember this", "kind": "note"})
    assert e.value["text"] == "remember this"


def test_kind_field_used():
    _, e = _single({"text": "hello", "kind": "reminder"})
    assert e.kind == "reminder"


def test_kind_defaults_to_memory():
    _, e = _single({"text": "no kind field"})
    assert e.kind == "memory"


def test_unknown_fields_stored_in_omi_raw():
    _, e = _single({"text": "hello", "extra_field": "extra_value", "another": 42})
    assert "_omi_raw" in e.value
    assert e.value["_omi_raw"]["extra_field"] == "extra_value"
    assert e.value["_omi_raw"]["another"] == 42


def test_known_fields_not_duplicated_in_omi_raw():
    _, e = _single({"text": "hello", "kind": "memory", "ts_utc": "2026-01-01T00:00:00+00:00"})
    raw = e.value.get("_omi_raw", {})
    assert "text" not in raw
    assert "kind" not in raw
    assert "ts_utc" not in raw


def test_meta_field_preserved():
    meta = {"confidence": 0.9, "device": "wearable"}
    _, e = _single({"text": "hello", "meta": meta})
    assert e.value["meta"] == meta


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_invalid_payload_not_dict_returns_empty():
    assert parse_omi_payload("not a dict") == []  # type: ignore[arg-type]
    assert parse_omi_payload(None) == []            # type: ignore[arg-type]
    assert parse_omi_payload(42) == []              # type: ignore[arg-type]


def test_items_not_a_list_returns_empty():
    assert parse_omi_payload({"items": "not a list"}) == []


def test_non_dict_blob_in_batch_is_skipped():
    events = parse_omi_payload({"items": [
        {"text": "good blob"},
        "not a dict",
        {"text": "also good"},
    ]})
    # Two good blobs should be parsed; the string blob is skipped.
    assert len(events) == 2
    assert all(e.source == SOURCE_OMI for e in events)


def test_empty_batch_returns_empty_list():
    events = parse_omi_payload({"items": []})
    assert events == []
