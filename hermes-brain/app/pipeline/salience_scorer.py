"""
Salience scorer: assigns a float score to each MemoryCandidate.

Design
------
Rule-based only in v1.  No LLM involved.  Each rule contributes a weight;
scores are summed and clamped to [0.0, 1.0].

Rules are intentionally generous — they flag *potentially* interesting
candidates, not confirmed events.  The downstream reasoning layer decides
what to do with them.  Thresholds here are NOT anomaly thresholds; they are
salience thresholds.

A candidate that scores 0.0 (no rules fired) is still stored locally.
Only candidates above ESCALATION_THRESHOLD (a separate config value) are
sent upstream — and only after the privacy router approves.

LLM hook (future, not v1)
--------------------------
If a local model is available and the candidate's score is in the "ambiguous"
band (SALIENCE_AMBIGUOUS_LOW < score < SALIENCE_AMBIGUOUS_HIGH), the scorer
can optionally invoke the LLM to refine the score.  The hook is present but
does nothing in v1.  This avoids re-architecting when the model arrives.

Rule weights (summed, then clamped to 1.0)
------------------------------------------
Tag "presence_onset"        +0.40   Transition from empty to occupied is
                                    one of the most useful home events.
Tag "presence_cleared"      +0.30   Transition to empty — less urgent than
                                    onset but still a significant state change.
Tag "co2_elevated"          +0.25   Sustained elevation indicates occupancy
                                    or ventilation issue.
Tag "co2_spike"             +0.30   Fast spike within a single window is more
                                    surprising than sustained elevation.
Tag "temp_drift"            +0.15   Slow drift is less surprising; flag it
                                    but give it low weight.
Tag "multi_source"          +0.10   Multiple sensor types agreeing is a mild
                                    corroboration bonus.
Tag "omi_present"           +0.20   External memory item is always worth a look,
                                    but we don't trust it more than sensors.
Any tag present at all      +0.05   Baseline: a tagged candidate beats a blank one.
"""
from __future__ import annotations

import logging
from typing import Optional

from .types import MemoryCandidate

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rule weight table
# ---------------------------------------------------------------------------
_TAG_WEIGHTS: dict[str, float] = {
    "presence_onset":    0.40,
    "presence_cleared":  0.30,
    "co2_elevated":      0.25,
    "co2_spike":         0.30,
    "temp_drift":        0.15,
    "multi_source":      0.10,
    "omi_present":       0.20,
}

# Baseline bonus for any non-empty tag list.
_ANY_TAG_BONUS: float = 0.05

# Ambiguous band — reserved for optional LLM refinement (not used in v1).
_SALIENCE_AMBIGUOUS_LOW:  float = 0.25
_SALIENCE_AMBIGUOUS_HIGH: float = 0.50


def _rule_score(candidate: MemoryCandidate) -> float:
    """
    Sum rule weights for each tag present on the candidate.
    Apply the any-tag baseline bonus if at least one tag fired.
    Clamp result to [0.0, 1.0].
    """
    total = 0.0
    if candidate.tags:
        total += _ANY_TAG_BONUS
        for tag in candidate.tags:
            total += _TAG_WEIGHTS.get(tag, 0.0)
    return min(1.0, total)


def _llm_refine(candidate: MemoryCandidate, rule_score: float) -> float:
    """
    Stub for optional LLM-based score refinement.

    In v1 this is a no-op.  Replace the body of this function when a local
    model is available.  The function must return a float in [0.0, 1.0].
    It must not raise; log and return rule_score on any failure.
    """
    # v1: return rule score unchanged.
    return rule_score


def score(
    candidate: MemoryCandidate,
    use_llm: bool = False,
) -> MemoryCandidate:
    """
    Assign a salience score to a MemoryCandidate and return it.

    The candidate is mutated in place (salience field updated) and also
    returned for convenience.  The provenance dict is extended with scoring
    metadata.

    Parameters
    ----------
    candidate
        A MemoryCandidate produced by candidate_builder (salience=None).
    use_llm
        If True and the rule score falls in the ambiguous band, attempt LLM
        refinement.  Defaults to False (pure rule-based).  In v1 this has no
        effect regardless.

    Returns
    -------
    MemoryCandidate
        The same object with salience set and provenance updated.
    """
    rule_s = _rule_score(candidate)

    final_s = rule_s
    llm_used = False

    if use_llm and _SALIENCE_AMBIGUOUS_LOW <= rule_s <= _SALIENCE_AMBIGUOUS_HIGH:
        try:
            refined = _llm_refine(candidate, rule_s)
            final_s = float(refined)
            llm_used = True
        except Exception as exc:
            log.warning("salience_scorer: llm_refine failed (%s) — using rule score", exc)

    candidate.salience = round(final_s, 4)
    candidate.provenance["scoring"] = {
        "rule_score": round(rule_s, 4),
        "llm_used":   llm_used,
        "final":      candidate.salience,
        "tags_fired": [t for t in candidate.tags if t in _TAG_WEIGHTS],
    }

    log.debug(
        "salience_scorer: candidate=%s tags=%s score=%.3f",
        candidate.candidate_id, candidate.tags, candidate.salience,
    )
    return candidate


def score_all(
    candidates: list[MemoryCandidate],
    use_llm: bool = False,
) -> list[MemoryCandidate]:
    """Score every candidate in the list.  Returns the same list."""
    for c in candidates:
        score(c, use_llm=use_llm)
    return candidates
