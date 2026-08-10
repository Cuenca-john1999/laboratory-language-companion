from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from llc_api.schemas.base import APIModel

AttemptOutcome = Literal[
    "failure",
    "partial",
    "correct_with_help",
    "correct_without_help",
]
EvidenceSource = Literal["manual_assessment", "diagnostic"]
PlanIntensity = Literal["low", "normal", "high"]
PlanBlockKind = Literal["review", "new_skill", "practice"]


class MasteryCriteriaRead(APIModel):
    min_mastery: float = Field(ge=0, le=1)
    min_confidence: float = Field(ge=0, le=1)
    min_evidence: int = Field(ge=1)
    min_unassisted_streak: int = Field(ge=1)


class CurriculumSkillRead(APIModel):
    code: str
    name: str
    category: str
    description: str
    cefr_hint: Literal["pre-A1", "A1"]
    order: int = Field(ge=1)
    prerequisite_codes: list[str]
    mastery_criteria: MasteryCriteriaRead
    difficulty: int = Field(ge=1, le=5)
    compatible_exercise_types: list[str]
    curriculum_version: str
    is_active: bool


class CurriculumRead(APIModel):
    version: str
    skills: list[CurriculumSkillRead]


class StudentSkillStateRead(APIModel):
    skill_code: str
    estimated_mastery: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence_count: int = Field(ge=0)
    last_practised_at: datetime | None
    next_review_at: datetime | None
    last_outcome: AttemptOutcome | None
    unassisted_streak: int = Field(ge=0)
    internally_mastered: bool
    mastered_at: datetime | None


class LearningSkillRead(CurriculumSkillRead):
    state: StudentSkillStateRead | None
    eligible_for_introduction: bool
    unmet_prerequisites: list[str]


class AttemptCreate(APIModel):
    submission_id: UUID
    skill_code: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.-]+$")
    source: EvidenceSource = "manual_assessment"
    exercise_type: str = Field(min_length=1, max_length=50)
    prompt: str = Field(min_length=1, max_length=10_000)
    student_answer: str = Field(min_length=1, max_length=10_000)
    expected_answer: str | None = Field(default=None, max_length=10_000)
    outcome: AttemptOutcome
    feedback: str = Field(default="", max_length=10_000)
    corrects_submission_id: UUID | None = None

    @model_validator(mode="after")
    def correction_must_use_a_different_id(self):
        if self.corrects_submission_id == self.submission_id:
            raise ValueError("A correction must use a new submission_id")
        return self


class AttemptReceipt(APIModel):
    created: bool
    evidence_id: int
    corrected_evidence_id: int | None
    exercise_attempt_id: int
    learning_session_id: int
    engine_version: str
    outcome: AttemptOutcome
    derived_score: float = Field(ge=0, le=1)
    state: StudentSkillStateRead


class ReviewRead(APIModel):
    skill_code: str
    skill_name: str
    due_at: datetime
    overdue: bool
    estimated_mastery: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence_count: int = Field(ge=1)


class ReviewsRead(APIModel):
    as_of: datetime
    total_due: int = Field(ge=0)
    items: list[ReviewRead]


class DailyPlanCreate(APIModel):
    available_minutes: int = Field(ge=10, le=120)
    motivation: int = Field(ge=1, le=5)


class DailyPlanBlockRead(APIModel):
    position: int = Field(ge=1)
    kind: PlanBlockKind
    skill_code: str
    exercise_type: str
    duration_minutes: int = Field(gt=0)
    objective: str
    internal_reason: str


class DailyPlanRead(APIModel):
    id: int
    plan_date: date
    engine_version: str
    curriculum_version: str
    generated_at: datetime
    requested_minutes: int = Field(ge=10, le=120)
    duration_minutes: int = Field(ge=10, le=120)
    motivation: int = Field(ge=1, le=5)
    objective: str
    intensity: PlanIntensity
    primary_skill_code: str
    review_skill_codes: list[str]
    new_skill_code: str | None
    blocks: list[DailyPlanBlockRead]
    internal_reason: str
    reason_codes: list[str]
