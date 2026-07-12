"""Simple, auditable spaced-repetition rules."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from deutschos_api.learning_engine.scoring import AttemptOutcome, coerce_outcome

UNAIDED_INTERVAL_DAYS = (3, 7, 14, 30, 60)


@dataclass(frozen=True, slots=True)
class ReviewSchedule:
    interval_days: int
    next_review_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewCandidate:
    skill_code: str
    due_at: datetime | None
    estimated_mastery: float
    confidence: float
    evidence_count: int
    curriculum_order: int
    last_outcome: AttemptOutcome | None = None


@dataclass(frozen=True, slots=True)
class RankedReview:
    priority_rank: int
    skill_code: str
    due_at: datetime
    overdue_days: int
    estimated_mastery: float
    confidence: float
    evidence_count: int
    last_outcome: AttemptOutcome | None


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")


def review_interval_days(outcome: AttemptOutcome | str, *, unassisted_streak: int) -> int:
    """Return the next interval after the just-recorded outcome.

    Assisted or incomplete work resets the unaided streak in scoring and uses a
    short fixed interval. Only unaided success advances through 3/7/14/30/60.
    """

    if unassisted_streak < 0:
        raise ValueError("unassisted_streak cannot be negative")
    normalized_outcome = coerce_outcome(outcome)
    if normalized_outcome is AttemptOutcome.FAILURE:
        return 1
    if normalized_outcome is AttemptOutcome.PARTIAL:
        return 2
    if normalized_outcome is AttemptOutcome.CORRECT_WITH_HELP:
        return 3
    if unassisted_streak < 1:
        raise ValueError("an unaided correct outcome requires a positive streak")
    return UNAIDED_INTERVAL_DAYS[min(unassisted_streak, len(UNAIDED_INTERVAL_DAYS)) - 1]


def calculate_review_schedule(
    *,
    outcome: AttemptOutcome | str,
    observed_at: datetime,
    unassisted_streak: int,
) -> ReviewSchedule:
    _require_aware(observed_at, "observed_at")
    days = review_interval_days(outcome, unassisted_streak=unassisted_streak)
    return ReviewSchedule(interval_days=days, next_review_at=observed_at + timedelta(days=days))


def rank_due_reviews(
    candidates: tuple[ReviewCandidate, ...] | list[ReviewCandidate], *, as_of: datetime
) -> tuple[RankedReview, ...]:
    """Filter and rank due work without a hidden weighted score.

    Oldest due date wins; ties favor lower mastery, lower confidence, then the
    versioned curriculum order and stable skill code.
    """

    _require_aware(as_of, "as_of")
    due: list[ReviewCandidate] = []
    for candidate in candidates:
        if candidate.evidence_count < 0:
            raise ValueError("evidence_count cannot be negative")
        if not 0 <= candidate.estimated_mastery <= 1:
            raise ValueError("estimated_mastery must be between 0 and 1")
        if not 0 <= candidate.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if candidate.due_at is None or candidate.evidence_count == 0:
            continue
        _require_aware(candidate.due_at, "due_at")
        if candidate.due_at <= as_of:
            due.append(candidate)

    due.sort(
        key=lambda item: (
            item.due_at,
            item.estimated_mastery,
            item.confidence,
            item.curriculum_order,
            item.skill_code,
        )
    )
    return tuple(
        RankedReview(
            priority_rank=index,
            skill_code=item.skill_code,
            due_at=item.due_at,  # type: ignore[arg-type]
            overdue_days=max(0, (as_of - item.due_at).days),  # type: ignore[operator]
            estimated_mastery=item.estimated_mastery,
            confidence=item.confidence,
            evidence_count=item.evidence_count,
            last_outcome=item.last_outcome,
        )
        for index, item in enumerate(due, start=1)
    )
