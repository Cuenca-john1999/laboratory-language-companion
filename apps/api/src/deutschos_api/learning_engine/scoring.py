"""Deterministic conversion of evaluated outcomes into skill state."""

from dataclasses import dataclass
from enum import StrEnum

from deutschos_api.learning_engine.curriculum import MasteryCriteria

ENGINE_VERSION = "learning-engine-v2"
MASTERY_ALPHA = 0.35
MAX_CONFIDENCE = 0.95
EVIDENCE_FOR_MAX_CONFIDENCE = 10


class AttemptOutcome(StrEnum):
    FAILURE = "failure"
    PARTIAL = "partial"
    CORRECT_WITH_HELP = "correct_with_help"
    CORRECT_WITHOUT_HELP = "correct_without_help"


OUTCOME_SCORES: dict[AttemptOutcome, float] = {
    AttemptOutcome.FAILURE: 0.0,
    AttemptOutcome.PARTIAL: 0.4,
    AttemptOutcome.CORRECT_WITH_HELP: 0.7,
    AttemptOutcome.CORRECT_WITHOUT_HELP: 1.0,
}


@dataclass(frozen=True, slots=True)
class ScoringUpdate:
    estimated_mastery: float
    confidence: float
    evidence_count: int
    last_outcome: AttemptOutcome
    unassisted_streak: int
    lapse_count: int
    derived_score: float


def coerce_outcome(outcome: AttemptOutcome | str) -> AttemptOutcome:
    try:
        return AttemptOutcome(outcome)
    except ValueError as exc:
        raise ValueError(f"unsupported attempt outcome: {outcome}") from exc


def score_for_outcome(outcome: AttemptOutcome | str) -> float:
    return OUTCOME_SCORES[coerce_outcome(outcome)]


def calculate_mastery_update(
    *,
    current_mastery: float,
    evidence_count: int,
    outcome: AttemptOutcome | str,
    current_unassisted_streak: int = 0,
    current_lapse_count: int = 0,
) -> ScoringUpdate:
    """Apply the versioned v2 mastery rule.

    The first observation becomes the initial estimate. Later observations use a
    bounded exponentially weighted update, so recent evidence matters without
    allowing a single attempt to replace the history.
    """

    if not 0 <= current_mastery <= 1:
        raise ValueError("current_mastery must be between 0 and 1")
    if evidence_count < 0:
        raise ValueError("evidence_count cannot be negative")
    if current_unassisted_streak < 0:
        raise ValueError("current_unassisted_streak cannot be negative")
    if current_lapse_count < 0:
        raise ValueError("current_lapse_count cannot be negative")

    normalized_outcome = coerce_outcome(outcome)
    score = score_for_outcome(normalized_outcome)
    new_count = evidence_count + 1
    if evidence_count == 0:
        mastery = score
    else:
        mastery = current_mastery + MASTERY_ALPHA * (score - current_mastery)

    unassisted_streak = (
        current_unassisted_streak + 1
        if normalized_outcome is AttemptOutcome.CORRECT_WITHOUT_HELP
        else 0
    )
    lapse_count = current_lapse_count + (1 if normalized_outcome is AttemptOutcome.FAILURE else 0)
    confidence = min(MAX_CONFIDENCE, new_count / EVIDENCE_FOR_MAX_CONFIDENCE)
    return ScoringUpdate(
        estimated_mastery=round(max(0.0, min(1.0, mastery)), 4),
        confidence=round(confidence, 4),
        evidence_count=new_count,
        last_outcome=normalized_outcome,
        unassisted_streak=unassisted_streak,
        lapse_count=lapse_count,
        derived_score=score,
    )


def is_internally_mastered(
    *,
    estimated_mastery: float,
    confidence: float,
    evidence_count: int,
    unassisted_streak: int,
    criteria: MasteryCriteria,
) -> bool:
    """Evaluate a skill against that skill's own versioned mastery criteria."""

    return (
        estimated_mastery >= criteria.min_mastery
        and confidence >= criteria.min_confidence
        and evidence_count >= criteria.min_evidence
        and unassisted_streak >= criteria.unassisted_streak
    )
