"""
Tests for the candidate compressor (hermes-brain/app/pipeline/compressor.py).

LocalLLM is stubbed out in all tests — no llama.cpp binary or model file is
required.  Tests cover:
- compress() sets candidate.summary from LLM output
- compress() skips candidates that already have a summary
- compress() skips when model file does not exist
- compress() handles LLM exceptions gracefully (no raise)
- compress_all() returns correct count and mutates all candidates
- Event formatting helpers produce expected output
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.pipeline.compressor import compress, compress_all, _format_event, _build_context
from app.pipeline.types import HomeEvent, MemoryCandidate
from app.llm.local_llm import LocalLLM

# Re-use shared factories from conftest
from tests.conftest import make_event, make_candidate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_llm(model_exists: bool = True, response: str = "Presence detected with elevated CO2.") -> LocalLLM:
    """Return a LocalLLM whose subprocess call is fully mocked."""
    llm = MagicMock(spec=LocalLLM)
    llm.model_path = MagicMock()
    llm.model_path.exists.return_value = model_exists
    llm.generate.return_value = response
    return llm


# ---------------------------------------------------------------------------
# compress() — basic behaviour
# ---------------------------------------------------------------------------

def test_compress_sets_summary(tmp_path: Path) -> None:
    """compress() must populate candidate.summary from LLM output."""
    candidate = make_candidate(tags=["presence_onset"], salience=0.6)
    llm = _stub_llm(response="Motion detected; CO2 slightly elevated.")

    compress(candidate, llm)

    assert candidate.summary == "Motion detected; CO2 slightly elevated."
    llm.generate.assert_called_once()


def test_compress_skips_existing_summary() -> None:
    """compress() must not overwrite a summary that is already set."""
    candidate = make_candidate(summary="already set")
    llm = _stub_llm()

    compress(candidate, llm)

    assert candidate.summary == "already set"
    llm.generate.assert_not_called()


def test_compress_skips_when_model_missing() -> None:
    """compress() must leave summary as None when the model file does not exist."""
    candidate = make_candidate()
    llm = _stub_llm(model_exists=False)

    compress(candidate, llm)

    assert candidate.summary is None
    llm.generate.assert_not_called()


def test_compress_handles_llm_exception() -> None:
    """compress() must not raise when LLM.generate() throws."""
    candidate = make_candidate()
    llm = _stub_llm()
    llm.generate.side_effect = RuntimeError("subprocess exploded")

    compress(candidate, llm)   # must not raise

    assert candidate.summary is None


def test_compress_handles_empty_llm_response() -> None:
    """compress() must leave summary as None when LLM returns empty string."""
    candidate = make_candidate()
    llm = _stub_llm(response="   ")

    compress(candidate, llm)

    assert candidate.summary is None


def test_compress_strips_whitespace_from_summary() -> None:
    """compress() must strip surrounding whitespace from the LLM response."""
    candidate = make_candidate()
    llm = _stub_llm(response="  Quiet period, no anomalies.  \n")

    compress(candidate, llm)

    assert candidate.summary == "Quiet period, no anomalies."


# ---------------------------------------------------------------------------
# compress_all()
# ---------------------------------------------------------------------------

def test_compress_all_returns_count() -> None:
    """compress_all() must return the number of candidates that got a new summary."""
    candidates = [make_candidate(candidate_id=f"w300_{i}") for i in range(4)]
    llm = _stub_llm(response="Summary text.")

    count = compress_all(candidates, llm)

    assert count == 4
    assert all(c.summary == "Summary text." for c in candidates)


def test_compress_all_skips_already_summarised() -> None:
    """compress_all() must not count or overwrite pre-existing summaries."""
    c1 = make_candidate(candidate_id="w300_1", summary="done")
    c2 = make_candidate(candidate_id="w300_2")
    llm = _stub_llm(response="New summary.")

    count = compress_all([c1, c2], llm)

    assert count == 1          # only c2 got a new summary
    assert c1.summary == "done"
    assert c2.summary == "New summary."


def test_compress_all_empty_list() -> None:
    """compress_all() on an empty list must return 0 without calling LLM."""
    llm = _stub_llm()
    count = compress_all([], llm)
    assert count == 0
    llm.generate.assert_not_called()


def test_compress_all_model_missing_returns_zero() -> None:
    """compress_all() must return 0 when the model file is absent."""
    candidates = [make_candidate(candidate_id=f"w300_{i}") for i in range(3)]
    llm = _stub_llm(model_exists=False)

    count = compress_all(candidates, llm)

    assert count == 0
    assert all(c.summary is None for c in candidates)


# ---------------------------------------------------------------------------
# _format_event() and _build_context()
# ---------------------------------------------------------------------------

def test_format_event_includes_source_kind_and_values() -> None:
    event = make_event(
        ts_utc="2026-01-01T12:05:30+00:00",
        source="env",
        kind="temperature",
        value={"temp_c": 22.5, "hum_pct": 55.0},
    )
    line = _format_event(event)
    assert "env/temperature" in line
    assert "temp_c=22.5" in line
    assert "2026-01-01 12:05:30" in line


def test_format_event_no_value_fields() -> None:
    event = make_event(value={})
    line = _format_event(event)
    assert "env/temperature" in line
    # No colon-separated values, but should not crash
    assert isinstance(line, str)


def test_build_context_includes_tags_and_window() -> None:
    candidate = make_candidate(
        ts_start="2026-01-01T12:00:00+00:00",
        ts_end="2026-01-01T12:05:00+00:00",
        tags=["presence_onset", "co2_elevated"],
        salience=0.7,
    )
    ctx = _build_context(candidate)
    assert "presence_onset" in ctx
    assert "co2_elevated" in ctx
    assert "2026-01-01 12:00:00" in ctx
    assert "2026-01-01 12:05:00" in ctx
    assert "0.70" in ctx


def test_build_context_caps_events() -> None:
    """_build_context() must cap to MAX_EVENTS_IN_PROMPT and note omitted count."""
    from app.pipeline.compressor import MAX_EVENTS_IN_PROMPT
    events = [make_event(ts_utc=f"2026-01-01T12:{i:02d}:00+00:00") for i in range(MAX_EVENTS_IN_PROMPT + 5)]
    candidate = make_candidate(events=events)
    ctx = _build_context(candidate)
    assert "more events not shown" in ctx


def test_build_context_no_events() -> None:
    candidate = make_candidate(events=[])
    ctx = _build_context(candidate)
    assert "Window:" in ctx
    assert isinstance(ctx, str)
