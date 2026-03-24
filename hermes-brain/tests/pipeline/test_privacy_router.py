"""
Tests for pipeline/privacy_router.py.

Invariants locked here:
- _ALWAYS_ALLOWED fields (ts_start, ts_end, source_mix, tags, salience)
  are present in the packet payload regardless of allowlist config.
- candidate_id is NOT in the default allowlist — it must appear in
  stripped_fields, not in the payload.
- allowed_fields and stripped_fields are sorted.
- stripped_fields is stored on the packet but nothing in this module
  adds it to the payload (the field is audit-only).
- route() sets candidate.escalate=True on candidates that pass the threshold.
- route() skips candidates below the threshold.
- route() skips candidates with salience=None.
- packet_id is a UUID-format string, unique per call.
- build_escalation_packet() uses candidate.salience even if 0.0.
"""
from __future__ import annotations
import re

from tests.conftest import make_candidate

from app.pipeline.privacy_router import (
    DEFAULT_ALLOWLIST,
    _ALWAYS_ALLOWED,
    build_escalation_packet,
    route,
)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


# ---------------------------------------------------------------------------
# _ALWAYS_ALLOWED
# ---------------------------------------------------------------------------

def test_always_allowed_set_content():
    assert _ALWAYS_ALLOWED == {"ts_start", "ts_end", "source_mix", "tags", "salience"}


def test_always_allowed_fields_present_even_with_empty_allowlist():
    c = make_candidate(salience=0.5)
    packet = build_escalation_packet(c, allowlist_str="")
    for field in _ALWAYS_ALLOWED:
        assert field in packet.payload, f"always-allowed field {field!r} missing from payload"


def test_always_allowed_fields_in_allowed_fields_list():
    c = make_candidate(salience=0.5)
    packet = build_escalation_packet(c, allowlist_str="")
    for field in _ALWAYS_ALLOWED:
        assert field in packet.allowed_fields


# ---------------------------------------------------------------------------
# candidate_id stripping
# ---------------------------------------------------------------------------

def test_candidate_id_not_in_default_payload():
    c = make_candidate(salience=0.5)
    packet = build_escalation_packet(c, allowlist_str=DEFAULT_ALLOWLIST)
    assert "candidate_id" not in packet.payload


def test_candidate_id_in_stripped_fields():
    c = make_candidate(salience=0.5)
    packet = build_escalation_packet(c, allowlist_str=DEFAULT_ALLOWLIST)
    assert "candidate_id" in packet.stripped_fields


# ---------------------------------------------------------------------------
# Allowlist filtering
# ---------------------------------------------------------------------------

def test_default_allowlist_includes_summary_key():
    c = make_candidate(salience=0.5, summary="a brief summary")
    packet = build_escalation_packet(c, allowlist_str=DEFAULT_ALLOWLIST)
    assert "summary" in packet.payload
    assert packet.payload["summary"] == "a brief summary"


def test_summary_stripped_when_not_in_allowlist():
    c = make_candidate(salience=0.5, summary="secret")
    packet = build_escalation_packet(c, allowlist_str="ts_start,ts_end")
    assert "summary" not in packet.payload
    assert "summary" in packet.stripped_fields


def test_stripped_fields_not_in_payload():
    c = make_candidate(salience=0.5)
    packet = build_escalation_packet(c, allowlist_str=DEFAULT_ALLOWLIST)
    for f in packet.stripped_fields:
        assert f not in packet.payload, f"stripped field {f!r} leaked into payload"


def test_allowed_fields_sorted():
    c = make_candidate(salience=0.5)
    packet = build_escalation_packet(c, allowlist_str=DEFAULT_ALLOWLIST)
    assert packet.allowed_fields == sorted(packet.allowed_fields)


def test_stripped_fields_sorted():
    c = make_candidate(salience=0.5)
    packet = build_escalation_packet(c, allowlist_str=DEFAULT_ALLOWLIST)
    assert packet.stripped_fields == sorted(packet.stripped_fields)


# ---------------------------------------------------------------------------
# Packet metadata
# ---------------------------------------------------------------------------

def test_packet_id_is_uuid4_format():
    c = make_candidate(salience=0.5)
    packet = build_escalation_packet(c)
    assert _UUID_RE.match(packet.packet_id), f"packet_id not UUID4: {packet.packet_id!r}"


def test_packet_id_is_unique_per_call():
    c = make_candidate(salience=0.5)
    p1 = build_escalation_packet(c)
    p2 = build_escalation_packet(c)
    assert p1.packet_id != p2.packet_id


def test_packet_candidate_id_matches_candidate():
    c = make_candidate(candidate_id="w300_9999", salience=0.8)
    packet = build_escalation_packet(c)
    assert packet.candidate_id == "w300_9999"


def test_packet_destination_passed_through():
    c = make_candidate(salience=0.8)
    packet = build_escalation_packet(c, destination="cloud_v1")
    assert packet.destination == "cloud_v1"


def test_packet_salience_is_candidate_salience():
    c = make_candidate(salience=0.75)
    packet = build_escalation_packet(c)
    assert packet.salience == 0.75


def test_packet_salience_defaults_to_zero_when_none():
    c = make_candidate(salience=None)
    packet = build_escalation_packet(c)
    assert packet.salience == 0.0


def test_packet_tags_copied_from_candidate():
    tags = ["presence_onset", "co2_elevated"]
    c = make_candidate(tags=tags, salience=0.8)
    packet = build_escalation_packet(c)
    assert packet.tags == tags


def test_packet_source_mix_copied_from_candidate():
    from app.pipeline.types import SOURCE_ENV, SOURCE_AIR
    c = make_candidate(source_mix=[SOURCE_ENV, SOURCE_AIR], salience=0.5)
    packet = build_escalation_packet(c)
    assert packet.source_mix == [SOURCE_ENV, SOURCE_AIR]


# ---------------------------------------------------------------------------
# route()
# ---------------------------------------------------------------------------

def test_route_passes_candidates_above_threshold():
    c = make_candidate(salience=0.8)
    packets = route([c], escalation_threshold=0.7)
    assert len(packets) == 1


def test_route_skips_candidates_below_threshold():
    c = make_candidate(salience=0.3)
    packets = route([c], escalation_threshold=0.7)
    assert packets == []


def test_route_skips_candidates_at_threshold_boundary():
    # salience exactly equal to threshold should pass (>=, not >).
    c = make_candidate(salience=0.7)
    packets = route([c], escalation_threshold=0.7)
    assert len(packets) == 1


def test_route_skips_candidates_with_none_salience():
    c = make_candidate(salience=None)
    packets = route([c], escalation_threshold=0.0)
    assert packets == []


def test_route_sets_escalate_true_on_passing_candidates():
    c = make_candidate(salience=0.9)
    assert c.escalate is False
    route([c], escalation_threshold=0.7)
    assert c.escalate is True


def test_route_does_not_set_escalate_on_failing_candidates():
    c = make_candidate(salience=0.1)
    route([c], escalation_threshold=0.7)
    assert c.escalate is False


def test_route_returns_one_packet_per_passing_candidate():
    passing = [make_candidate(salience=0.9), make_candidate(salience=0.8)]
    failing = [make_candidate(salience=0.1)]
    packets = route(passing + failing, escalation_threshold=0.7)
    assert len(packets) == 2


def test_route_empty_list():
    assert route([], escalation_threshold=0.5) == []
