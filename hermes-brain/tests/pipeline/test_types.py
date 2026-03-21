"""
Canary tests for types.py.

These tests exist to catch accidental schema mutations.  If any of these
fail it means a field was renamed, removed, or the SCHEMA_VERSION was
changed without a deliberate decision.

Do not add behaviour tests here — those belong in the module-specific files.
"""
from app.pipeline.types import (
    SCHEMA_VERSION,
    SOURCE_ENV,
    SOURCE_AIR,
    SOURCE_RADAR,
    SOURCE_HB,
    SOURCE_OMI,
    SOURCE_SYSTEM,
    KIND_TEMPERATURE,
    KIND_HUMIDITY,
    KIND_CO2,
    KIND_VOC,
    KIND_PRESENCE,
    KIND_HEARTBEAT,
    HomeEvent,
    MemoryCandidate,
    EscalationPacket,
)


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

def test_schema_version_is_string():
    assert isinstance(SCHEMA_VERSION, str)


def test_schema_version_value():
    # Changing this value requires a deliberate versioning decision.
    assert SCHEMA_VERSION == "1"


# ---------------------------------------------------------------------------
# Source constants
# ---------------------------------------------------------------------------

def test_source_constants_distinct():
    sources = {SOURCE_ENV, SOURCE_AIR, SOURCE_RADAR, SOURCE_HB, SOURCE_OMI, SOURCE_SYSTEM}
    assert len(sources) == 6


def test_omi_source_is_separate_from_sensor_sources():
    sensor_sources = {SOURCE_ENV, SOURCE_AIR, SOURCE_RADAR, SOURCE_HB}
    assert SOURCE_OMI not in sensor_sources


# ---------------------------------------------------------------------------
# HomeEvent — field names (canary)
# ---------------------------------------------------------------------------

def test_home_event_fields():
    e = HomeEvent(
        ts_utc="2026-01-01T00:00:00+00:00",
        source=SOURCE_ENV,
        kind=KIND_TEMPERATURE,
        value={"temp_c": 20.0},
        raw_ref="env:1",
        ingested_at="2026-01-01T00:00:01+00:00",
    )
    assert e.ts_utc == "2026-01-01T00:00:00+00:00"
    assert e.source == SOURCE_ENV
    assert e.kind == KIND_TEMPERATURE
    assert e.value == {"temp_c": 20.0}
    assert e.raw_ref == "env:1"
    assert e.ingested_at == "2026-01-01T00:00:01+00:00"


def test_home_event_raw_ref_can_be_none_for_external():
    e = HomeEvent(
        ts_utc="2026-01-01T00:00:00+00:00",
        source=SOURCE_OMI,
        kind="memory",
        value={"text": "hello"},
        raw_ref=None,
        ingested_at="2026-01-01T00:00:01+00:00",
    )
    assert e.raw_ref is None


# ---------------------------------------------------------------------------
# MemoryCandidate — field names (canary)
# ---------------------------------------------------------------------------

def test_memory_candidate_fields():
    from tests.conftest import make_candidate
    c = make_candidate(salience=0.5)
    # Verify every field the schema defines is accessible.
    assert hasattr(c, "candidate_id")
    assert hasattr(c, "ts_start")
    assert hasattr(c, "ts_end")
    assert hasattr(c, "events")
    assert hasattr(c, "source_mix")
    assert hasattr(c, "tags")
    assert hasattr(c, "salience")
    assert hasattr(c, "summary")
    assert hasattr(c, "escalate")
    assert hasattr(c, "provenance")


def test_memory_candidate_salience_starts_none():
    from tests.conftest import make_candidate
    c = make_candidate()
    assert c.salience is None


def test_memory_candidate_escalate_starts_false():
    from tests.conftest import make_candidate
    c = make_candidate()
    assert c.escalate is False


# ---------------------------------------------------------------------------
# EscalationPacket — field names (canary)
# ---------------------------------------------------------------------------

def test_escalation_packet_fields():
    p = EscalationPacket(
        packet_id="test-uuid",
        created_at="2026-01-01T00:00:00+00:00",
        candidate_id="w300_1",
        summary=None,
        tags=["co2_elevated"],
        salience=0.8,
        source_mix=["air"],
        payload={"ts_start": "2026-01-01T00:00:00+00:00"},
        allowed_fields=["ts_start"],
        stripped_fields=["candidate_id"],
        destination="default",
    )
    assert hasattr(p, "packet_id")
    assert hasattr(p, "created_at")
    assert hasattr(p, "candidate_id")
    assert hasattr(p, "summary")
    assert hasattr(p, "tags")
    assert hasattr(p, "salience")
    assert hasattr(p, "source_mix")
    assert hasattr(p, "payload")
    assert hasattr(p, "allowed_fields")
    assert hasattr(p, "stripped_fields")
    assert hasattr(p, "destination")
