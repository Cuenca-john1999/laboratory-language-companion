from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from deutschos_api.models import (
    DiagnosticAxis,
    DiagnosticBand,
    DiagnosticConfidenceLabel,
    DiagnosticOutcome,
    DiagnosticPolarity,
    DiagnosticSessionStatus,
    DiagnosticTaskStatus,
    DiagnosticTaskType,
)
from deutschos_api.schemas.base import APIModel, ORMModel

AssistanceKind = Literal[
    "none",
    "instruction_translation",
    "clarification",
    "lexical_hint",
    "grammar_hint",
    "worked_example",
    "retry",
]
InstructionState = Literal["understood", "not_understood", "unknown"]
DiagnosticOrigin = Literal["bank", "manual", "structured_llm"]
EvaluatorType = Literal["deterministic", "manual", "structured_llm"]
ConfidenceLabel = Literal["low", "moderate", "high"]
CefrBand = Literal["pre-A1", "A1"]
TEXT_TASK_AXES = {
    DiagnosticAxis.READING_COMPREHENSION,
    DiagnosticAxis.WRITTEN_PRODUCTION,
    DiagnosticAxis.ACTIVE_GRAMMAR,
    DiagnosticAxis.RECEPTIVE_VOCABULARY,
    DiagnosticAxis.PRODUCTIVE_VOCABULARY,
    DiagnosticAxis.WRITTEN_FLUENCY,
    DiagnosticAxis.TYPED_COMMUNICATION_REPAIR,
    DiagnosticAxis.EVERYDAY_FAMILIARITY,
    DiagnosticAxis.PROFESSIONAL_LABORATORY_FAMILIARITY,
}
FUTURE_MODALITY_AXES = {
    DiagnosticAxis.LISTENING_COMPREHENSION,
    DiagnosticAxis.ORAL_PRODUCTION,
    DiagnosticAxis.PRONUNCIATION,
    DiagnosticAxis.ORAL_FLUENCY,
}


class DiagnosticSessionCreate(APIModel):
    profile_id: Literal[1] = 1
    diagnostic_version: str = Field(min_length=1, max_length=50)
    persistence_version: str = Field(
        default="diagnostic-persistence-v1", min_length=1, max_length=50
    )
    curriculum_version: str = Field(min_length=1, max_length=30)
    status: Literal[DiagnosticSessionStatus.NOT_STARTED] = DiagnosticSessionStatus.NOT_STARTED
    active_section: str | None = Field(default=None, max_length=100)
    selection_state: dict[str, JsonValue] = Field(default_factory=dict)
    random_seed: str = Field(min_length=1, max_length=100)
    instruction_language: str = Field(default="es", min_length=2, max_length=20)
    target_task_count: int = Field(default=14, ge=12, le=16)
    max_task_count: Literal[20] = 20
    target_duration_seconds: int = Field(default=1200, ge=900, le=1500)
    max_duration_seconds: Literal[1500] = 1500
    repeats_session_id: int | None = Field(default=None, ge=1)


class DiagnosticSessionStateWrite(APIModel):
    status: DiagnosticSessionStatus
    paused_from_status: DiagnosticSessionStatus | None = None
    active_section: str | None = Field(default=None, max_length=100)
    selection_state: dict[str, JsonValue] = Field(default_factory=dict)
    active_seconds: int = Field(ge=0)
    tasks_presented: int = Field(ge=0, le=20)
    tasks_evaluable: int = Field(ge=0, le=20)
    paused_at: AwareDatetime | None = None
    resumed_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    abandoned_at: AwareDatetime | None = None
    termination_reason: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def state_metadata_is_consistent(self):
        if self.tasks_evaluable > self.tasks_presented:
            raise ValueError("tasks_evaluable cannot exceed tasks_presented")
        if self.status == DiagnosticSessionStatus.PAUSED and self.paused_at is None:
            raise ValueError("paused sessions require paused_at")
        if self.status == DiagnosticSessionStatus.COMPLETED and self.completed_at is None:
            raise ValueError("completed sessions require completed_at")
        if self.status == DiagnosticSessionStatus.ABANDONED and self.abandoned_at is None:
            raise ValueError("abandoned sessions require abandoned_at")
        return self


class DiagnosticSessionRead(ORMModel):
    id: int
    profile_id: Literal[1]
    diagnostic_version: str
    persistence_version: str
    curriculum_version: str
    status: DiagnosticSessionStatus
    paused_from_status: DiagnosticSessionStatus | None
    active_section: str | None
    selection_state: dict[str, JsonValue]
    random_seed: str
    instruction_language: str
    target_task_count: int = Field(ge=12, le=16)
    max_task_count: Literal[20]
    target_duration_seconds: int = Field(ge=900, le=1500)
    max_duration_seconds: Literal[1500]
    active_seconds: int = Field(ge=0)
    tasks_presented: int = Field(ge=0, le=20)
    tasks_evaluable: int = Field(ge=0, le=20)
    termination_reason: str | None
    started_at: AwareDatetime
    paused_at: AwareDatetime | None
    resumed_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    abandoned_at: AwareDatetime | None
    repeats_session_id: int | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class DiagnosticTaskCreate(APIModel):
    session_id: int = Field(ge=1)
    sequence: int = Field(ge=1, le=20)
    status: DiagnosticTaskStatus = DiagnosticTaskStatus.SELECTED
    template_id: str = Field(min_length=1, max_length=100)
    template_version: str = Field(min_length=1, max_length=50)
    task_type: DiagnosticTaskType
    primary_axis: DiagnosticAxis
    secondary_axes: list[DiagnosticAxis] = Field(default_factory=list, max_length=12)
    skill_id: int | None = Field(default=None, ge=1)
    difficulty: int = Field(ge=1, le=5)
    modality: Literal["text"] = "text"
    selection_reason: str = Field(min_length=1, max_length=100)
    content: dict[str, JsonValue]
    options: list[JsonValue] = Field(default_factory=list)
    expected_answer: dict[str, JsonValue] | None = None
    rubric: dict[str, JsonValue] = Field(default_factory=dict)
    origin: DiagnosticOrigin = "bank"
    generator_version: str | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def axes_are_text_capable(self):
        if self.primary_axis not in TEXT_TASK_AXES or any(
            axis not in TEXT_TASK_AXES for axis in self.secondary_axes
        ):
            raise ValueError("text v1 tasks may only measure text-capable axes")
        return self


class DiagnosticTaskRead(DiagnosticTaskCreate):
    model_config = ORMModel.model_config

    id: int
    selected_at: AwareDatetime
    presented_at: AwareDatetime | None
    answered_at: AwareDatetime | None


class DiagnosticResponseCreate(APIModel):
    evaluation_id: UUID
    submission_id: UUID
    task_id: int = Field(ge=1)
    attempt_number: int = Field(ge=1)
    evaluation_revision: int = Field(default=1, ge=1)
    response_text: str | None = Field(default=None, max_length=10_000)
    response_language: str | None = Field(default=None, max_length=20)
    instruction_state: InstructionState = "unknown"
    assistance: list[AssistanceKind] = Field(default_factory=list, max_length=20)
    active_seconds: int | None = Field(default=None, ge=0)
    outcome: DiagnosticOutcome
    score: float | None = Field(default=None, ge=0, le=1)
    rubric: dict[str, JsonValue] = Field(default_factory=dict)
    polarity: DiagnosticPolarity
    evaluator_confidence: float = Field(ge=0, le=1)
    evaluator_type: EvaluatorType
    evaluator_version: str = Field(min_length=1, max_length=50)
    diagnostic_version: str = Field(min_length=1, max_length=50)
    task_version: str = Field(min_length=1, max_length=50)
    justification: str = Field(default="", max_length=10_000)
    reason_codes: list[str] = Field(default_factory=list, max_length=50)
    supersedes_response_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def evaluation_is_consistent(self):
        is_not_evaluable = self.outcome == DiagnosticOutcome.NOT_EVALUABLE
        if is_not_evaluable != (self.score is None):
            raise ValueError("not_evaluable requires a null score and other outcomes require one")
        if is_not_evaluable and self.polarity != DiagnosticPolarity.INSUFFICIENT:
            raise ValueError("not_evaluable responses must have insufficient polarity")
        if not is_not_evaluable and not self.response_text:
            raise ValueError("evaluable responses require response_text")
        is_correction = self.supersedes_response_id is not None
        if is_correction != (self.evaluation_revision > 1):
            raise ValueError("corrections require supersedes_response_id and a revision above 1")
        return self


class DiagnosticResponseRead(DiagnosticResponseCreate):
    model_config = ORMModel.model_config

    id: int
    submitted_at: AwareDatetime
    created_at: AwareDatetime


class DiagnosticResultCreate(APIModel):
    result_id: UUID
    result_group_id: UUID
    result_revision: int = Field(default=1, ge=1)
    session_id: int = Field(ge=1)
    axis: DiagnosticAxis
    modality: Literal["text"] = "text"
    skill_id: int | None = Field(default=None, ge=1)
    band: DiagnosticBand
    cefr_band: CefrBand | None = None
    estimated_score: float | None = Field(default=None, ge=0, le=1)
    estimate_confidence: float = Field(ge=0, le=1)
    confidence_label: DiagnosticConfidenceLabel
    positive_evidence_count: int = Field(default=0, ge=0)
    negative_evidence_count: int = Field(default=0, ge=0)
    insufficient_evidence_count: int = Field(default=0, ge=0)
    task_types: list[DiagnosticTaskType] = Field(default_factory=list)
    difficulty_min: int | None = Field(default=None, ge=1, le=5)
    difficulty_max: int | None = Field(default=None, ge=1, le=5)
    strengths: str = Field(default="", max_length=10_000)
    limitations: str = Field(default="", max_length=10_000)
    recommendation: str = Field(default="", max_length=10_000)
    projection_status: Literal["not_projected"] = "not_projected"
    result_version: str = Field(min_length=1, max_length=50)
    supersedes_result_id: int | None = Field(default=None, ge=1)
    correction_reason: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def result_is_consistent(self):
        if (self.difficulty_min is None) != (self.difficulty_max is None):
            raise ValueError("difficulty_min and difficulty_max must both be set or both be null")
        if self.difficulty_min is not None and self.difficulty_max < self.difficulty_min:
            raise ValueError("difficulty_max cannot be lower than difficulty_min")
        if self.band == DiagnosticBand.INSUFFICIENT_EVIDENCE:
            if self.estimated_score is not None:
                raise ValueError("insufficient evidence cannot have an estimated score")
        elif self.estimated_score is None:
            raise ValueError("an observed band requires an estimated score")
        if self.cefr_band is not None and (
            self.skill_id is None
            or self.positive_evidence_count < 2
            or self.estimate_confidence < 0.6
        ):
            raise ValueError("a CEFR band requires a skill and sufficient positive evidence")
        if self.axis in FUTURE_MODALITY_AXES and (
            self.band != DiagnosticBand.INSUFFICIENT_EVIDENCE
            or self.cefr_band is not None
            or self.positive_evidence_count != 0
            or self.negative_evidence_count != 0
            or self.difficulty_min is not None
            or self.difficulty_max is not None
        ):
            raise ValueError("audio and speech axes must remain unassessed in text v1")
        is_correction = self.supersedes_result_id is not None
        if is_correction != (self.result_revision > 1):
            raise ValueError("corrections require supersedes_result_id and a revision above 1")
        return self


class DiagnosticResultRead(DiagnosticResultCreate):
    model_config = ORMModel.model_config

    id: int
    computed_at: AwareDatetime


__all__ = [
    "DiagnosticResponseCreate",
    "DiagnosticResponseRead",
    "DiagnosticResultCreate",
    "DiagnosticResultRead",
    "DiagnosticSessionCreate",
    "DiagnosticSessionRead",
    "DiagnosticSessionStateWrite",
    "DiagnosticTaskCreate",
    "DiagnosticTaskRead",
]
