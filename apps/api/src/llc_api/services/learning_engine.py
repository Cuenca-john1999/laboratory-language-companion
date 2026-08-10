"""Compatibility imports for the provisional Milestone 0 module path.

New code belongs to :mod:`llc_api.learning_engine`.
"""

from llc_api.learning_engine.reviews import calculate_review_schedule
from llc_api.learning_engine.scoring import (
    ENGINE_VERSION,
    calculate_mastery_update,
    score_for_outcome,
)
from llc_api.learning_engine.service import (
    CorrectionConflictError,
    CurriculumUnavailableError,
    DailyPlanNotFoundError,
    IdempotencyConflictError,
    IncompatibleExerciseTypeError,
    LearningEngineBusyError,
    UnknownEvidenceError,
    UnknownSkillError,
    create_daily_plan,
    get_curriculum,
    get_today_plan,
    list_due_reviews,
    list_skills,
    record_attempt,
    record_evaluated_attempt,
)

__all__ = [
    "ENGINE_VERSION",
    "CorrectionConflictError",
    "CurriculumUnavailableError",
    "DailyPlanNotFoundError",
    "IdempotencyConflictError",
    "IncompatibleExerciseTypeError",
    "LearningEngineBusyError",
    "UnknownEvidenceError",
    "UnknownSkillError",
    "calculate_mastery_update",
    "calculate_review_schedule",
    "create_daily_plan",
    "get_curriculum",
    "get_today_plan",
    "list_due_reviews",
    "list_skills",
    "record_attempt",
    "record_evaluated_attempt",
    "score_for_outcome",
]
