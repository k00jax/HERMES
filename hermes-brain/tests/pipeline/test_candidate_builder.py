"""
Tests for pipeline/candidate_builder.py.

Bucket math is tested against a fixed reference timestamp to keep arithmetic
explicit and avoid clock-dependent flakiness.  See conftest.py for the epoch.

Invariants locked here:
- candidate_id format is exactly "w{window_sec}_{bucket_index}".
- Same inputs + same window_sec → same candidate_ids across reruns.
- Different window_sec → different candidate_ids, no collision.
- Events spanning multiple buckets → one candidate per bucket.
- Presence onset/cleared detection at bucket level.
- CO2, temp, source-mix, omi tags.
- provenance contains schema_version and window_sec.
- Unparseable ts_utc is skipped (not a crash).
"""
from __future__ import annotations

from tests.conftest import make_event, REF_TS, REF_BUCKET_IDX_300, REF_BUCKET_IDX_600

from app.pipeline.candidate_builder import build_candidates
from app.pipeline.types import (
    SCHEMA_VERSION,
    KIND_CO2,
    KIND_PRESENCE,
    KIND_TEMPERATURE,
    SOURCE_AIR,
    SOURCE_ENV,
    SOURCE_OMI,
    SOURCE_RADAR,
)


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------

def test_empty_input_returns_empty():
    assert build_candidates([]) == []


def test_single_event_produces_one_candidate():
    events = [make_event()]
    candidates = build_candidates(events, window_sec=300)
    assert len(candidates) == 1


def test_candidate_has_exactly_the_event():
    e = make_event()
    [c] = build_candidates([e], window_sec=300)
    assert c.events == [e]


# ---------------------------------------------------------------------------
# Candidate ID format
# ---------------------------------------------------------------------------

def test_candidate_id_w300_prefix():
    e = make_event(ts_utc=REF_TS)
    [c] = build_candidates([e], window_sec=300)
    assert c.candidate_id == f"w300_{REF_BUCKET_IDX_300}"


def test_candidate_id_w600_prefix():
    e = make_event(ts_utc=REF_TS)
    [c] = build_candidates([e], window_sec=600)
    assert c.candidate_id == f"w600_{REF_BUCKET_IDX_600}"


def test_candidate_id_format_is_w_windowsec_underscore_index():
    e = make_event(ts_utc=REF_TS)
    for window_sec in (60, 300, 600, 900):
        [c] = build_candidates([e], window_sec=window_sec)
        assert c.candidate_id.startswith(f"w{window_sec}_"), (
            f"expected 'w{window_sec}_...' got {c.candidate_id!r}"
        )
        suffix = c.candidate_id[len(f"w{window_sec}_"):]
        assert suffix.isdigit(), f"suffix should be integer bucket index, got {suffix!r}"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_same_inputs_produce_same_candidate_ids():
    events = [make_event(ts_utc=REF_TS)]
    ids_first  = [c.candidate_id for c in build_candidates(events, window_sec=300)]
    ids_second = [c.candidate_id for c in build_candidates(events, window_sec=300)]
    assert ids_first == ids_second


def test_different_window_sec_produces_different_ids_no_collision():
    e = make_event(ts_utc=REF_TS)
    ids_300 = {c.candidate_id for c in build_candidates([e], window_sec=300)}
    ids_600 = {c.candidate_id for c in build_candidates([e], window_sec=600)}
    assert not ids_300 & ids_600, (
        f"w300 and w600 candidate IDs collided: {ids_300 & ids_600}"
    )


# ---------------------------------------------------------------------------
# Bucket grouping
# ---------------------------------------------------------------------------

def test_events_in_same_bucket_grouped_into_one_candidate():
    # Two timestamps ~30s apart, both inside the same 5-min bucket.
    e1 = make_event(ts_utc="2026-01-01T12:05:10+00:00")
    e2 = make_event(ts_utc="2026-01-01T12:06:40+00:00")
    candidates = build_candidates([e1, e2], window_sec=300)
    assert len(candidates) == 1
    assert len(candidates[0].events) == 2


def test_events_in_different_buckets_produce_multiple_candidates():
    # 12:05 → bucket 14:05–14:10; 12:15 → bucket 14:15–14:20.
    e1 = make_event(ts_utc="2026-01-01T12:05:30+00:00")
    e2 = make_event(ts_utc="2026-01-01T12:16:00+00:00")
    candidates = build_candidates([e1, e2], window_sec=300)
    assert len(candidates) == 2


def test_candidates_ordered_by_ts_start():
    e1 = make_event(ts_utc="2026-01-01T12:05:30+00:00")
    e2 = make_event(ts_utc="2026-01-01T12:16:00+00:00")
    candidates = build_candidates([e1, e2], window_sec=300)
    assert candidates[0].ts_start < candidates[1].ts_start


def test_bucket_boundaries_are_epoch_aligned():
    e = make_event(ts_utc=REF_TS)          # 12:05:30 → bucket 12:05–12:10
    [c] = build_candidates([e], window_sec=300)
    # start and end must be on 5-minute boundaries
    import datetime
    start = datetime.datetime.fromisoformat(c.ts_start)
    end   = datetime.datetime.fromisoformat(c.ts_end)
    assert int(start.timestamp()) % 300 == 0
    assert int(end.timestamp()) % 300 == 0
    assert (end - start).total_seconds() == 300


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

def test_provenance_contains_schema_version():
    [c] = build_candidates([make_event()], window_sec=300)
    assert c.provenance["schema_version"] == SCHEMA_VERSION


def test_provenance_contains_window_sec():
    [c] = build_candidates([make_event()], window_sec=300)
    assert c.provenance["window_sec"] == 300


def test_provenance_contains_event_count():
    events = [make_event(), make_event()]
    [c] = build_candidates(events, window_sec=300)
    assert c.provenance["event_count"] == 2


# ---------------------------------------------------------------------------
# Tags — presence
# ---------------------------------------------------------------------------

def _presence_event(ts: str, target: int) -> object:
    return make_event(
        ts_utc=ts,
        source=SOURCE_RADAR,
        kind=KIND_PRESENCE,
        value={"alive": 1, "target": target, "detect_cm": 120.0, "move_cm": 0.0, "stat_cm": 0.0},
        raw_ref="radar:1",
    )


def test_presence_onset_tag():
    e = _presence_event(REF_TS, target=1)
    [c] = build_candidates([e], window_sec=300, prev_radar_target=False)
    assert "presence_onset" in c.tags


def test_presence_cleared_tag():
    e = _presence_event(REF_TS, target=0)
    [c] = build_candidates([e], window_sec=300, prev_radar_target=True)
    assert "presence_cleared" in c.tags


def test_no_presence_tag_when_prev_state_unknown():
    e = _presence_event(REF_TS, target=1)
    [c] = build_candidates([e], window_sec=300, prev_radar_target=None)
    assert "presence_onset" not in c.tags
    assert "presence_cleared" not in c.tags


def test_no_presence_tag_when_state_unchanged():
    e = _presence_event(REF_TS, target=1)
    [c] = build_candidates([e], window_sec=300, prev_radar_target=True)
    assert "presence_onset" not in c.tags
    assert "presence_cleared" not in c.tags


# ---------------------------------------------------------------------------
# Tags — CO2
# ---------------------------------------------------------------------------

def _co2_event(ts: str, ppm: float) -> object:
    return make_event(
        ts_utc=ts,
        source=SOURCE_AIR,
        kind=KIND_CO2,
        value={"eco2_ppm": ppm},
        raw_ref="air:1",
    )


def test_co2_elevated_tag_fires_above_threshold():
    e = _co2_event(REF_TS, 1100.0)
    [c] = build_candidates([e], window_sec=300)
    assert "co2_elevated" in c.tags


def test_co2_elevated_tag_does_not_fire_at_normal_level():
    e = _co2_event(REF_TS, 500.0)
    [c] = build_candidates([e], window_sec=300)
    assert "co2_elevated" not in c.tags


def test_co2_spike_tag_fires_when_delta_exceeds_threshold():
    # Two CO2 readings 400 ppm apart in the same bucket.
    e1 = _co2_event("2026-01-01T12:05:10+00:00", 500.0)
    e2 = _co2_event("2026-01-01T12:06:00+00:00", 900.0)
    [c] = build_candidates([e1, e2], window_sec=300)
    assert "co2_spike" in c.tags


def test_co2_spike_tag_does_not_fire_for_small_delta():
    e1 = _co2_event("2026-01-01T12:05:10+00:00", 400.0)
    e2 = _co2_event("2026-01-01T12:06:00+00:00", 450.0)
    [c] = build_candidates([e1, e2], window_sec=300)
    assert "co2_spike" not in c.tags


# ---------------------------------------------------------------------------
# Tags — temperature drift
# ---------------------------------------------------------------------------

def _temp_event(ts: str, temp: float) -> object:
    return make_event(ts_utc=ts, value={"temp_c": temp})


def test_temp_drift_tag_fires_when_delta_exceeds_threshold():
    e1 = _temp_event("2026-01-01T12:05:10+00:00", 20.0)
    e2 = _temp_event("2026-01-01T12:06:00+00:00", 22.0)   # delta = 2.0 > 1.5
    [c] = build_candidates([e1, e2], window_sec=300)
    assert "temp_drift" in c.tags


def test_temp_drift_tag_does_not_fire_for_small_delta():
    e1 = _temp_event("2026-01-01T12:05:10+00:00", 21.0)
    e2 = _temp_event("2026-01-01T12:06:00+00:00", 21.5)   # delta = 0.5 < 1.5
    [c] = build_candidates([e1, e2], window_sec=300)
    assert "temp_drift" not in c.tags


# ---------------------------------------------------------------------------
# Tags — source mix
# ---------------------------------------------------------------------------

def test_multi_source_tag_fires_with_three_or_more_sources():
    e1 = make_event(ts_utc=REF_TS, source=SOURCE_ENV,   kind=KIND_TEMPERATURE, value={"temp_c": 21.0}, raw_ref="env:1")
    e2 = make_event(ts_utc=REF_TS, source=SOURCE_AIR,   kind=KIND_CO2,         value={"eco2_ppm": 400.0}, raw_ref="air:1")
    e3 = make_event(ts_utc=REF_TS, source=SOURCE_RADAR, kind=KIND_PRESENCE,    value={"target": 1.0}, raw_ref="radar:1")
    [c] = build_candidates([e1, e2, e3], window_sec=300)
    assert "multi_source" in c.tags


def test_multi_source_tag_does_not_fire_with_two_sources():
    e1 = make_event(ts_utc=REF_TS, source=SOURCE_ENV, kind=KIND_TEMPERATURE, value={"temp_c": 21.0}, raw_ref="env:1")
    e2 = make_event(ts_utc=REF_TS, source=SOURCE_AIR, kind=KIND_CO2, value={"eco2_ppm": 400.0}, raw_ref="air:1")
    [c] = build_candidates([e1, e2], window_sec=300)
    assert "multi_source" not in c.tags


# ---------------------------------------------------------------------------
# Tags — omi_present
# ---------------------------------------------------------------------------

def test_omi_present_tag_when_omi_event_in_bucket():
    omi = make_event(ts_utc=REF_TS, source=SOURCE_OMI, kind="memory", value={"text": "hello"}, raw_ref=None)
    [c] = build_candidates([omi], window_sec=300)
    assert "omi_present" in c.tags


def test_omi_present_tag_absent_without_omi_event():
    e = make_event()
    [c] = build_candidates([e], window_sec=300)
    assert "omi_present" not in c.tags


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

def test_unparseable_ts_utc_is_skipped_not_crash():
    good = make_event(ts_utc=REF_TS)
    bad  = make_event(ts_utc="not-a-timestamp")
    # Should produce exactly one candidate for the good event.
    candidates = build_candidates([good, bad], window_sec=300)
    assert len(candidates) == 1


def test_salience_is_none_after_build():
    [c] = build_candidates([make_event()], window_sec=300)
    assert c.salience is None


def test_summary_is_none_after_build():
    [c] = build_candidates([make_event()], window_sec=300)
    assert c.summary is None


def test_escalate_is_false_after_build():
    [c] = build_candidates([make_event()], window_sec=300)
    assert c.escalate is False
