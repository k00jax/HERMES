"""
Tests for pipeline/salience_scorer.py.

Invariants locked here:
- No tags → salience 0.0.
- Each tag produces the documented weight contribution.
- Any-tag baseline bonus (0.05) fires whenever tags is non-empty.
- Sum of all known tags exceeds 1.0 but is clamped to 1.0.
- use_llm=False has no effect on the result (v1 stub).
- provenance["scoring"] is populated by score().
- score_all() mutates all candidates in the list.
- Tags not in _TAG_WEIGHTS contribute 0 weight (only baseline).
"""
from __future__ import annotations

from tests.conftest import make_candidate

from app.pipeline.salience_scorer import score, score_all


# Documented weights from salience_scorer.py — explicit here so tests catch
# if the source weights drift without a matching test update.
_WEIGHTS = {
    "presence_onset":   0.40,
    "presence_cleared": 0.30,
    "co2_elevated":     0.25,
    "co2_spike":        0.30,
    "temp_drift":       0.15,
    "multi_source":     0.10,
    "omi_present":      0.20,
}
_ANY_TAG_BONUS = 0.05


def _score_of(tags: list) -> float:
    c = make_candidate(tags=tags)
    score(c)
    return c.salience


# ---------------------------------------------------------------------------
# Zero case
# ---------------------------------------------------------------------------

def test_no_tags_gives_zero_salience():
    assert _score_of([]) == 0.0


# ---------------------------------------------------------------------------
# Single-tag weights
# ---------------------------------------------------------------------------

def test_presence_onset_weight():
    expected = round(_ANY_TAG_BONUS + _WEIGHTS["presence_onset"], 4)
    assert _score_of(["presence_onset"]) == expected


def test_presence_cleared_weight():
    expected = round(_ANY_TAG_BONUS + _WEIGHTS["presence_cleared"], 4)
    assert _score_of(["presence_cleared"]) == expected


def test_co2_elevated_weight():
    expected = round(_ANY_TAG_BONUS + _WEIGHTS["co2_elevated"], 4)
    assert _score_of(["co2_elevated"]) == expected


def test_co2_spike_weight():
    expected = round(_ANY_TAG_BONUS + _WEIGHTS["co2_spike"], 4)
    assert _score_of(["co2_spike"]) == expected


def test_temp_drift_weight():
    expected = round(_ANY_TAG_BONUS + _WEIGHTS["temp_drift"], 4)
    assert _score_of(["temp_drift"]) == expected


def test_multi_source_weight():
    expected = round(_ANY_TAG_BONUS + _WEIGHTS["multi_source"], 4)
    assert _score_of(["multi_source"]) == expected


def test_omi_present_weight():
    expected = round(_ANY_TAG_BONUS + _WEIGHTS["omi_present"], 4)
    assert _score_of(["omi_present"]) == expected


# ---------------------------------------------------------------------------
# Combination and clamping
# ---------------------------------------------------------------------------

def test_presence_onset_plus_co2_elevated_plus_multi_source():
    # 0.05 + 0.40 + 0.25 + 0.10 = 0.80
    expected = round(_ANY_TAG_BONUS + 0.40 + 0.25 + 0.10, 4)
    assert _score_of(["presence_onset", "co2_elevated", "multi_source"]) == expected


def test_score_clamped_at_one():
    # All known tags combined would sum well above 1.0.
    all_tags = list(_WEIGHTS.keys())
    assert _score_of(all_tags) == 1.0


def test_unknown_tag_contributes_only_baseline():
    # An unknown tag should still trigger the baseline bonus but add no weight.
    s_unknown = _score_of(["unknown_tag_xyz"])
    assert s_unknown == _ANY_TAG_BONUS


# ---------------------------------------------------------------------------
# use_llm stub
# ---------------------------------------------------------------------------

def test_use_llm_false_and_true_produce_same_score_in_v1():
    """v1 LLM hook is a no-op — both paths must return the same score."""
    tags = ["co2_elevated"]
    s_no_llm  = _score_of(tags)
    c = make_candidate(tags=tags)
    score(c, use_llm=True)
    assert c.salience == s_no_llm


# ---------------------------------------------------------------------------
# provenance["scoring"]
# ---------------------------------------------------------------------------

def test_scoring_provenance_populated():
    c = make_candidate(tags=["presence_onset"])
    score(c)
    assert "scoring" in c.provenance


def test_scoring_provenance_rule_score_matches_final_when_no_llm():
    c = make_candidate(tags=["co2_elevated"])
    score(c)
    assert c.provenance["scoring"]["rule_score"] == c.provenance["scoring"]["final"]


def test_scoring_provenance_llm_used_false_by_default():
    c = make_candidate(tags=["presence_onset"])
    score(c)
    assert c.provenance["scoring"]["llm_used"] is False


def test_scoring_provenance_tags_fired_excludes_unknown_tags():
    c = make_candidate(tags=["presence_onset", "not_a_known_tag"])
    score(c)
    fired = c.provenance["scoring"]["tags_fired"]
    assert "presence_onset" in fired
    assert "not_a_known_tag" not in fired


# ---------------------------------------------------------------------------
# score_all
# ---------------------------------------------------------------------------

def test_score_all_mutates_every_candidate():
    candidates = [
        make_candidate(tags=["presence_onset"]),
        make_candidate(tags=[]),
        make_candidate(tags=["co2_elevated", "multi_source"]),
    ]
    score_all(candidates)
    assert all(c.salience is not None for c in candidates)


def test_score_all_returns_same_list():
    candidates = [make_candidate(tags=["presence_onset"])]
    result = score_all(candidates)
    assert result is candidates


def test_score_all_empty_list():
    assert score_all([]) == []
