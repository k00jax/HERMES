"""
Candidate compressor: generate a compressed summary for a MemoryCandidate.

Calls LocalLLM to produce a short natural-language summary of the candidate's
events.  The summary is written to candidate.summary in place.

If no model is available, summary is left as None.  The pipeline must work
fully with summary=None — compression is an optional enrichment step.

Design rules
------------
- Never raise: log and return on any failure.
- Never overwrite an existing non-None summary.
- Cap event list at MAX_EVENTS_IN_PROMPT to stay within the model's context.
- Do not interpret; only compress observable facts.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from ..llm.local_llm import LocalLLM
from .types import HomeEvent, MemoryCandidate

log = logging.getLogger(__name__)

MAX_EVENTS_IN_PROMPT = 20   # keep prompt well under 2048-token context limit
_COMPRESSION_QUESTION = (
    "In one factual sentence (under 30 words), summarise what happened in this "
    "home sensor window. Report only observable facts. Do not interpret or infer."
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _format_event(event: HomeEvent) -> str:
    ts = event.ts_utc[:19].replace("T", " ")   # "YYYY-MM-DD HH:MM:SS"
    value_str = ", ".join(f"{k}={v}" for k, v in (event.value or {}).items())
    return f"  [{ts}] {event.source}/{event.kind}: {value_str}" if value_str else \
           f"  [{ts}] {event.source}/{event.kind}"


def _build_context(candidate: MemoryCandidate) -> str:
    ts_start = candidate.ts_start[:19].replace("T", " ")
    ts_end   = candidate.ts_end[:19].replace("T", " ")
    tags_str = ", ".join(candidate.tags) if candidate.tags else "none"
    sources  = ", ".join(sorted(set(candidate.source_mix))) if candidate.source_mix else "unknown"

    events = candidate.events or []
    shown  = events[:MAX_EVENTS_IN_PROMPT]
    omitted = len(events) - len(shown)

    lines = [
        f"Window: {ts_start} to {ts_end} UTC",
        f"Sources: {sources}",
        f"Tags: {tags_str}",
        f"Salience: {candidate.salience:.2f}" if candidate.salience is not None else "Salience: unscored",
        "Events:",
    ]
    lines.extend(_format_event(e) for e in shown)
    if omitted:
        lines.append(f"  ... ({omitted} more events not shown)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def compress(candidate: MemoryCandidate, llm: LocalLLM) -> None:
    """
    Generate a compressed summary for a single MemoryCandidate.

    Mutates candidate.summary in place.  Does nothing if:
    - summary is already set (non-None)
    - model file does not exist (offline / no model installed)
    - LLM call fails for any reason

    Parameters
    ----------
    candidate
        A scored MemoryCandidate (salience should already be set).
    llm
        A LocalLLM instance loaded from config.
    """
    if candidate.summary is not None:
        return

    if not llm.model_path.exists():
        log.debug(
            "compressor: model not found at %s — skipping candidate %s",
            llm.model_path, candidate.candidate_id,
        )
        return

    context = _build_context(candidate)

    try:
        raw = llm.generate(question=_COMPRESSION_QUESTION, context=context)
    except Exception as exc:
        log.warning(
            "compressor: LLM call failed for candidate %s: %s",
            candidate.candidate_id, exc,
        )
        return

    summary = raw.strip() if raw else None
    if not summary:
        log.debug("compressor: empty response for candidate %s", candidate.candidate_id)
        return

    candidate.summary = summary
    log.debug(
        "compressor: summarised candidate %s → %r",
        candidate.candidate_id, summary[:80],
    )


def compress_all(candidates: List[MemoryCandidate], llm: LocalLLM) -> int:
    """
    Compress all candidates in place.

    Returns the count of candidates that received a new summary.
    """
    if not candidates:
        return 0

    if not llm.model_path.exists():
        log.debug("compressor: model not found — skipping compression for %d candidates", len(candidates))
        return 0

    count = 0
    for c in candidates:
        before = c.summary
        compress(c, llm)
        if c.summary is not None and before is None:
            count += 1
    log.info("compressor: compressed %d/%d candidates", count, len(candidates))
    return count
