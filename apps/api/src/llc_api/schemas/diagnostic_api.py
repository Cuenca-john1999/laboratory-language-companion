"""Strict public HTTP contracts for the deterministic diagnostic engine."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from llc_api.diagnostic_engine.schemas import (
    DEFAULT_DIAGNOSTIC_VERSION,
    DEFAULT_PERSISTENCE_VERSION,
    CompletionReason,
    CoverageStatus,
    EngineSessionState,
    EvaluationOutcome,
    OptionIdentifier,
    StructuredAnswer,
)
from llc_api.models import (
    DiagnosticAxis,
    DiagnosticBand,
    DiagnosticConfidenceLabel,
    DiagnosticPolarity,
    DiagnosticSessionStatus,
    DiagnosticTaskStatus,
    DiagnosticTaskType,
)
from llc_api.schemas.base import APIModel
from llc_api.schemas.diagnostic import AssistanceKind, InstructionState


class DiagnosticSessionCreateRequest(APIModel):
    request_id: UUID
    profile_id: Literal[1] = 1
    diagnostic_version: str = Field(
        default=DEFAULT_DIAGNOSTIC_VERSION,
        min_length=1,
        max_length=50,
    )
    persistence_version: str = Field(
        default=DEFAULT_PERSISTENCE_VERSION,
        min_length=1,
        max_length=50,
    )
    curriculum_version: str = Field(default="a0-a1.v1", min_length=1, max_length=30)
    instruction_language: str = Field(default="es", min_length=2, max_length=20)
    target_task_count: int = Field(default=14, ge=12, le=16)
    max_task_count: Literal[20] = 20
    target_duration_seconds: int = Field(default=1200, ge=900, le=1500)
    max_duration_seconds: Literal[1500] = 1500
    repeats_session_id: int | None = Field(default=None, ge=1)


class DiagnosticOperationRequest(APIModel):
    operation_id: UUID
    reason: str | None = Field(default=None, max_length=100)


class DiagnosticNextTaskRequest(APIModel):
    operation_id: UUID


class DiagnosticResponseSubmitRequest(APIModel):
    task_id: int = Field(ge=1)
    evaluation_id: UUID
    submission_id: UUID
    response_text: str | None = Field(default=None, max_length=10_000)
    answer: StructuredAnswer | None = None
    response_language: str | None = Field(default=None, max_length=20)
    instruction_state: InstructionState = "unknown"
    assistance: list[AssistanceKind] = Field(default_factory=list, max_length=20)
    active_seconds: int | None = Field(default=None, ge=0, le=1500)
    abandoned: bool = False
    out_of_topic: bool = False
    partially_communicative: bool = False

    @model_validator(mode="after")
    def response_flags_are_unambiguous(self):
        if self.answer is not None and self.response_text is not None:
            raise ValueError("legacy response_text and structured answer are mutually exclusive")
        if len(set(self.assistance)) != len(self.assistance):
            raise ValueError("assistance entries cannot be duplicated")
        if "none" in self.assistance and len(self.assistance) > 1:
            raise ValueError("none cannot be combined with other assistance")
        if self.out_of_topic and self.partially_communicative:
            raise ValueError("a response cannot be both out_of_topic and partially communicative")
        return self


class DiagnosticCorrectionRequest(APIModel):
    session_id: int = Field(ge=1)
    evaluation_id: UUID
    outcome: EvaluationOutcome
    score: float | None = Field(default=None, ge=0, le=1)
    polarity: DiagnosticPolarity
    evaluator_confidence: float = Field(ge=0, le=1)
    justification: str = Field(min_length=1, max_length=10_000)
    reason_codes: list[str] = Field(default_factory=list, max_length=50)


class DiagnosticSessionPublic(APIModel):
    session_id: int = Field(ge=1)
    state: EngineSessionState
    phase: DiagnosticSessionStatus
    active_seconds: int = Field(ge=0, le=1500)
    tasks_presented: int = Field(ge=0, le=20)
    tasks_evaluable: int = Field(ge=0, le=20)
    active_section: str | None = None
    termination_reason: str | None = None
    started_at: AwareDatetime
    paused_at: AwareDatetime | None = None
    resumed_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    abandoned_at: AwareDatetime | None = None


class DiagnosticOptionPublic(APIModel):
    id: OptionIdentifier
    label: str = Field(min_length=1, max_length=5000)


class DiagnosticTaskPublic(APIModel):
    task_id: int = Field(ge=1)
    session_id: int = Field(ge=1)
    sequence: int = Field(ge=1, le=20)
    status: DiagnosticTaskStatus
    content_version: str = Field(min_length=1, max_length=50)
    task_type: DiagnosticTaskType
    primary_axis: DiagnosticAxis
    secondary_axes: list[DiagnosticAxis] = Field(default_factory=list, max_length=8)
    skill_id: int | None = Field(default=None, ge=1)
    difficulty: int = Field(ge=1, le=5)
    modality: Literal["text"] = "text"
    response_type: Literal["single_choice", "short_text", "ordered_tokens", "free_text"]
    answer_contract: Literal["legacy-text.v1", "option-id.v1", "text.v2"]
    content: dict[str, JsonValue]
    options: list[str | DiagnosticOptionPublic] = Field(default_factory=list, max_length=50)
    estimated_seconds: int = Field(ge=5, le=600)
    presented_at: AwareDatetime | None = None
    created: bool


class DiagnosticStopPublic(APIModel):
    should_stop: bool
    reason: CompletionReason
    partial: bool
    detail: str


class DiagnosticAxisResultPublic(APIModel):
    axis: DiagnosticAxis
    skill_id: int | None = Field(default=None, ge=1)
    evidence_count: int = Field(ge=0)
    positive_evidence_count: int = Field(ge=0)
    negative_evidence_count: int = Field(ge=0)
    insufficient_evidence_count: int = Field(ge=0)
    maximum_demonstrated_difficulty: int | None = Field(default=None, ge=1, le=5)
    estimated_score: float | None = Field(default=None, ge=0, le=1)
    estimate_confidence: float = Field(ge=0, le=1)
    confidence_label: DiagnosticConfidenceLabel
    band: DiagnosticBand | None = None
    cefr_band: Literal["pre-A1", "A1"] | None = None
    coverage_status: CoverageStatus
    task_types: list[DiagnosticTaskType] = Field(default_factory=list)
    difficulty_min: int | None = Field(default=None, ge=1, le=5)
    difficulty_max: int | None = Field(default=None, ge=1, le=5)
    explanation: str = Field(max_length=1000)


class DiagnosticEvaluationPublic(APIModel):
    outcome: EvaluationOutcome
    score: float | None = Field(default=None, ge=0, le=1)
    polarity: DiagnosticPolarity
    evaluator_confidence: float = Field(ge=0, le=1)


class DiagnosticResponsePublic(APIModel):
    response_id: int = Field(ge=1)
    task_id: int = Field(ge=1)
    attempt_number: int = Field(ge=1)
    evaluation_revision: int = Field(ge=1)
    evaluation: DiagnosticEvaluationPublic
    task_status: DiagnosticTaskStatus
    created: bool


class DiagnosticSelectionPublic(APIModel):
    session_id: int = Field(ge=1)
    task: DiagnosticTaskPublic | None = None
    stop: DiagnosticStopPublic


class DiagnosticSessionSnapshotPublic(APIModel):
    session: DiagnosticSessionPublic
    current_task: DiagnosticTaskPublic | None = None
    results: list[DiagnosticAxisResultPublic] = Field(default_factory=list)
    stop: DiagnosticStopPublic


class DiagnosticResultsPublic(APIModel):
    session_id: int = Field(ge=1)
    results: list[DiagnosticAxisResultPublic] = Field(default_factory=list)


__all__ = [
    "DiagnosticAxisResultPublic",
    "DiagnosticCorrectionRequest",
    "DiagnosticEvaluationPublic",
    "DiagnosticNextTaskRequest",
    "DiagnosticOptionPublic",
    "DiagnosticOperationRequest",
    "DiagnosticResponsePublic",
    "DiagnosticResponseSubmitRequest",
    "DiagnosticResultsPublic",
    "DiagnosticSelectionPublic",
    "DiagnosticSessionCreateRequest",
    "DiagnosticSessionPublic",
    "DiagnosticSessionSnapshotPublic",
    "DiagnosticStopPublic",
    "DiagnosticTaskPublic",
]
