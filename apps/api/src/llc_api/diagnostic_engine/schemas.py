"""Strict commands, receipts, and pure-engine value objects."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BeforeValidator,
    Field,
    JsonValue,
    StringConstraints,
    TypeAdapter,
    model_validator,
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

DIAGNOSTIC_ENGINE_VERSION = "diagnostic-engine.v1"
DEFAULT_DIAGNOSTIC_VERSION = "diagnostic-text.v1"
DEFAULT_PERSISTENCE_VERSION = "diagnostic-persistence-v1"
LEGACY_TEXT_ANSWER_CONTRACT = "legacy-text.v1"
OPTION_ID_ANSWER_CONTRACT = "option-id.v1"
STRUCTURED_TEXT_ANSWER_CONTRACT = "text.v2"
CORE_TEXT_AXES = (
    DiagnosticAxis.READING_COMPREHENSION,
    DiagnosticAxis.WRITTEN_PRODUCTION,
    DiagnosticAxis.ACTIVE_GRAMMAR,
    DiagnosticAxis.RECEPTIVE_VOCABULARY,
    DiagnosticAxis.PRODUCTIVE_VOCABULARY,
    DiagnosticAxis.TYPED_COMMUNICATION_REPAIR,
)
TEXT_AXES = (
    *CORE_TEXT_AXES,
    DiagnosticAxis.WRITTEN_FLUENCY,
    DiagnosticAxis.EVERYDAY_FAMILIARITY,
    DiagnosticAxis.PROFESSIONAL_LABORATORY_FAMILIARITY,
)
FUTURE_MODALITY_AXES = (
    DiagnosticAxis.LISTENING_COMPREHENSION,
    DiagnosticAxis.ORAL_PRODUCTION,
    DiagnosticAxis.PRONUNCIATION,
    DiagnosticAxis.ORAL_FLUENCY,
)


class EvaluationOutcome(StrEnum):
    """Engine vocabulary; ``incorrect`` is persisted as the legacy ``failure`` value."""

    INCORRECT = "incorrect"
    PARTIAL = "partial"
    CORRECT_WITH_HELP = "correct_with_help"
    CORRECT_WITHOUT_HELP = "correct_without_help"
    NOT_EVALUABLE = "not_evaluable"


class EngineSessionState(StrEnum):
    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    FAILED = "failed"


class SessionAction(StrEnum):
    START = "start"
    BEGIN_CALIBRATION = "begin_calibration"
    BEGIN_ASSESSMENT = "begin_assessment"
    BEGIN_REVIEW = "begin_review"
    CONTINUE_ASSESSMENT = "continue_assessment"
    REACH_TIME_LIMIT = "reach_time_limit"
    BEGIN_COMPLETION = "begin_completion"
    PAUSE = "pause"
    RESUME = "resume"
    ABANDON = "abandon"
    COMPLETE = "complete"
    FAIL = "fail"


class TaskAction(StrEnum):
    PRESENT = "present"
    ANSWER = "answer"
    SKIP = "skip"
    MARK_NOT_UNDERSTOOD = "mark_not_understood"
    ABANDON = "abandon"
    EVALUATE = "evaluate"
    RETRY = "retry"
    INVALIDATE = "invalidate"


class AmbiguityRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CoverageStatus(StrEnum):
    NOT_ASSESSED = "not_assessed"
    INSUFFICIENT = "insufficient"
    OBSERVED = "observed"
    SUFFICIENT = "sufficient"


class CompletionReason(StrEnum):
    CONTINUE = "continue"
    COVERAGE_AND_CONFIDENCE = "coverage_and_confidence"
    TARGET_REACHED = "target_reached"
    MAX_TASKS = "max_tasks"
    TIME_LIMIT = "time_limit"
    NO_CANDIDATES = "no_candidates"


class RubricStrategy(StrEnum):
    EXACT_MATCH = "exact_match"
    ACCEPTED_ANSWERS = "accepted_answers"
    ORDERED_TOKENS = "ordered_tokens"
    MANUAL_ONLY = "manual_only"
    OPTION_ID = "option_id"


_BANNED_OPTION_ID_HINTS = frozenset({"answer", "correct", "expected", "wrong"})


def _validate_option_identifier(value: object) -> object:
    if not isinstance(value, str):
        return value
    if value != value.strip():
        raise ValueError("option IDs cannot contain outer whitespace")
    lowered = value.lower()
    if any(hint in lowered for hint in _BANNED_OPTION_ID_HINTS):
        raise ValueError("option IDs cannot contain scoring hints")
    return value


OptionIdentifier = Annotated[
    str,
    BeforeValidator(_validate_option_identifier),
    StringConstraints(
        min_length=8,
        max_length=40,
        pattern=r"^opt_[a-z0-9]{4,36}$",
        strip_whitespace=False,
    ),
]
_OPTION_ID_ADAPTER = TypeAdapter(OptionIdentifier)


class SingleChoiceAnswer(APIModel):
    kind: Literal["single_choice"]
    selected_option_id: OptionIdentifier


class TextAnswer(APIModel):
    kind: Literal["text"]
    text: str = Field(max_length=10_000)


class NoAnswer(APIModel):
    kind: Literal["no_answer"]


StructuredAnswer = Annotated[
    SingleChoiceAnswer | TextAnswer | NoAnswer,
    Field(discriminator="kind"),
]


class DeterministicRubric(APIModel):
    strategy: RubricStrategy
    accepted_answers: list[str] = Field(default_factory=list, max_length=50)
    partial_answers: list[str] = Field(default_factory=list, max_length=50)
    expected_tokens: list[str] = Field(default_factory=list, max_length=100)
    accepted_option_ids: list[OptionIdentifier] = Field(default_factory=list, max_length=50)
    partial_option_ids: list[OptionIdentifier] = Field(default_factory=list, max_length=50)
    case_sensitive: bool = False

    @model_validator(mode="after")
    def strategy_has_a_key(self):
        if self.strategy in {RubricStrategy.EXACT_MATCH, RubricStrategy.ACCEPTED_ANSWERS}:
            if not self.accepted_answers:
                raise ValueError("answer-matching rubrics require accepted_answers")
        if self.strategy == RubricStrategy.ORDERED_TOKENS and not self.expected_tokens:
            raise ValueError("ordered-token rubrics require expected_tokens")
        if self.strategy == RubricStrategy.OPTION_ID:
            if not self.accepted_option_ids:
                raise ValueError("option-id rubrics require accepted_option_ids")
            if self.accepted_answers or self.partial_answers or self.expected_tokens:
                raise ValueError("option-id rubrics cannot contain textual answer keys")
            if self.case_sensitive:
                raise ValueError("option IDs use exact identity instead of text case policy")
            all_ids = [*self.accepted_option_ids, *self.partial_option_ids]
            if len(set(all_ids)) != len(all_ids):
                raise ValueError("option-id rubric keys must be unique")
        elif self.accepted_option_ids or self.partial_option_ids:
            raise ValueError("text rubrics cannot contain option IDs")
        return self


class TaskCandidate(APIModel):
    candidate_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_.-]+$")
    version: str = Field(min_length=1, max_length=50)
    equivalence_key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_.-]+$")
    axis: DiagnosticAxis
    secondary_axes: list[DiagnosticAxis] = Field(default_factory=list, max_length=8)
    task_type: DiagnosticTaskType
    difficulty: int = Field(ge=1, le=5)
    prerequisite_candidate_ids: list[str] = Field(default_factory=list, max_length=20)
    modality: Literal["text", "audio", "oral"] = "text"
    content: dict[str, JsonValue]
    options: list[JsonValue] = Field(default_factory=list, max_length=50)
    expected_answer: dict[str, JsonValue] | None = None
    rubric: DeterministicRubric
    auto_evaluable: bool = True
    ambiguity_risk: AmbiguityRisk = AmbiguityRisk.LOW
    estimated_seconds: int = Field(ge=5, le=600)
    skill_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def modality_matches_axis(self):
        if self.modality == "text" and (
            self.axis in FUTURE_MODALITY_AXES
            or any(axis in FUTURE_MODALITY_AXES for axis in self.secondary_axes)
        ):
            raise ValueError("text candidates cannot claim audio or speech evidence")
        if self.axis == DiagnosticAxis.TYPED_COMMUNICATION_REPAIR and (
            self.modality != "text"
            or self.task_type != DiagnosticTaskType.TYPED_COMMUNICATION_REPAIR
        ):
            raise ValueError("typed communication repair requires its dedicated text task type")
        if (
            self.task_type == DiagnosticTaskType.TYPED_COMMUNICATION_REPAIR
            and self.axis != DiagnosticAxis.TYPED_COMMUNICATION_REPAIR
        ):
            raise ValueError("communication repair tasks must use the typed repair axis")
        if self.axis in self.secondary_axes or len(set(self.secondary_axes)) != len(
            self.secondary_axes
        ):
            raise ValueError("candidate axes must be unique")
        if self.candidate_id in self.prerequisite_candidate_ids or len(
            set(self.prerequisite_candidate_ids)
        ) != len(self.prerequisite_candidate_ids):
            raise ValueError("candidate prerequisites must be unique and cannot reference self")
        if self.auto_evaluable and self.rubric.strategy == RubricStrategy.MANUAL_ONLY:
            raise ValueError("manual-only rubrics cannot claim automatic evaluation")
        if self.rubric.strategy == RubricStrategy.OPTION_ID:
            if (self.expected_answer or {}).get("answer_contract") != OPTION_ID_ANSWER_CONTRACT:
                raise ValueError("option-id candidates require an explicit option-id contract")
            option_ids: list[str] = []
            for option in self.options:
                if not isinstance(option, dict) or set(option) != {"id", "label"}:
                    raise ValueError("option-id candidates require strict public option objects")
                option_id = option.get("id")
                label = option.get("label")
                if not isinstance(option_id, str) or not isinstance(label, str):
                    raise ValueError("option-id candidates require string IDs and labels")
                option_ids.append(_OPTION_ID_ADAPTER.validate_python(option_id))
            if len(option_ids) < 2 or len(set(option_ids)) != len(option_ids):
                raise ValueError("option-id candidates require at least two unique option IDs")
            rubric_ids = {*self.rubric.accepted_option_ids, *self.rubric.partial_option_ids}
            if not rubric_ids.issubset(option_ids):
                raise ValueError("option-id rubric keys must exist in public options")
        elif any(isinstance(option, dict) for option in self.options):
            raise ValueError("structured option objects require an option-id rubric")
        elif (self.expected_answer or {}).get("answer_contract") == OPTION_ID_ANSWER_CONTRACT:
            raise ValueError("option-id contracts require an option-id rubric")
        return self

    @property
    def stable_key(self) -> str:
        return f"{self.candidate_id}@{self.version}"


class CandidateProvider(Protocol):
    """Injectable source; production question banks are intentionally out of scope."""

    def candidates(self, *, diagnostic_version: str) -> Sequence[TaskCandidate]: ...


class EvidenceObservation(APIModel):
    response_id: int | None = Field(default=None, ge=1)
    task_id: int = Field(ge=1)
    candidate_id: str = Field(min_length=1, max_length=100)
    candidate_version: str = Field(min_length=1, max_length=50)
    axis: DiagnosticAxis
    task_type: DiagnosticTaskType
    difficulty: int = Field(ge=1, le=5)
    attempt_number: int = Field(ge=1)
    outcome: EvaluationOutcome
    score: float | None = Field(default=None, ge=0, le=1)
    polarity: DiagnosticPolarity
    evaluator_confidence: float = Field(ge=0, le=1)
    assistance: list[AssistanceKind] = Field(default_factory=list, max_length=20)
    skill_id: int | None = Field(default=None, ge=1)
    skill_cefr_reference: Literal["A0", "A1"] | None = None

    @model_validator(mode="after")
    def outcome_shape_is_valid(self):
        _validate_evaluation_shape(self.outcome, self.score, self.polarity)
        if self.outcome == EvaluationOutcome.NOT_EVALUABLE and self.evaluator_confidence != 0:
            raise ValueError("not_evaluable evidence requires zero evaluator confidence")
        if self.skill_cefr_reference is not None and self.skill_id is None:
            raise ValueError("a curricular CEFR reference requires skill_id")
        return self


class PresentedTaskObservation(APIModel):
    task_id: int = Field(ge=1)
    candidate_id: str = Field(min_length=1, max_length=100)
    candidate_version: str = Field(min_length=1, max_length=50)
    equivalence_key: str = Field(min_length=1, max_length=100)
    axis: DiagnosticAxis
    task_type: DiagnosticTaskType
    difficulty: int = Field(ge=1, le=5)
    tiebreak: bool = False

    @property
    def stable_key(self) -> str:
        return f"{self.candidate_id}@{self.candidate_version}"


class SelectionContext(APIModel):
    presented: list[PresentedTaskObservation] = Field(default_factory=list, max_length=20)
    evidence: list[EvidenceObservation] = Field(default_factory=list, max_length=100)
    active_seconds: int = Field(ge=0, le=86_400)
    target_task_count: int = Field(default=14, ge=12, le=16)
    max_task_count: Literal[20] = 20
    max_duration_seconds: Literal[1500] = 1500
    core_axes: list[DiagnosticAxis] = Field(default_factory=lambda: list(CORE_TEXT_AXES))


class SelectionDecision(APIModel):
    candidate: TaskCandidate
    reason: str = Field(min_length=1, max_length=100)
    target_difficulty: int = Field(ge=1, le=5)
    tiebreak: bool = False


class StopDecision(APIModel):
    should_stop: bool
    reason: CompletionReason
    partial: bool = False
    detail: str = Field(default="", max_length=500)


class ResponseSubmission(APIModel):
    session_id: int = Field(ge=1)
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


class EvaluationResult(APIModel):
    outcome: EvaluationOutcome
    score: float | None = Field(default=None, ge=0, le=1)
    polarity: DiagnosticPolarity
    evaluator_confidence: float = Field(ge=0, le=1)
    justification: str = Field(max_length=1000)
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    rubric_snapshot: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def evaluation_shape_is_valid(self):
        _validate_evaluation_shape(self.outcome, self.score, self.polarity)
        if self.outcome == EvaluationOutcome.NOT_EVALUABLE and self.evaluator_confidence != 0:
            raise ValueError("not_evaluable deterministic results require zero confidence")
        return self


class AxisAggregate(APIModel):
    axis: DiagnosticAxis
    evidence_count: int = Field(ge=0)
    positive_evidence_count: int = Field(ge=0)
    negative_evidence_count: int = Field(ge=0)
    insufficient_evidence_count: int = Field(ge=0)
    maximum_demonstrated_difficulty: int | None = Field(default=None, ge=1, le=5)
    estimated_score: float | None = Field(default=None, ge=0, le=1)
    estimate_confidence: float = Field(ge=0, le=1)
    band: DiagnosticBand | None = None
    cefr_band: Literal["pre-A1", "A1"] | None = None
    confidence_label: DiagnosticConfidenceLabel
    reason: str = Field(max_length=1000)
    coverage_status: CoverageStatus
    task_types: list[DiagnosticTaskType] = Field(default_factory=list)
    difficulty_min: int | None = Field(default=None, ge=1, le=5)
    difficulty_max: int | None = Field(default=None, ge=1, le=5)
    skill_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def unassessed_axes_are_explicit(self):
        if self.positive_evidence_count + self.negative_evidence_count != self.evidence_count:
            raise ValueError("positive and negative evidence must equal evaluable evidence")
        if (self.difficulty_min is None) != (self.difficulty_max is None):
            raise ValueError("difficulty bounds must be both present or both absent")
        if (
            self.difficulty_min is not None
            and self.difficulty_max is not None
            and self.difficulty_min > self.difficulty_max
        ):
            raise ValueError("difficulty_min cannot exceed difficulty_max")
        if self.evidence_count > 0 and (
            not self.task_types or self.difficulty_min is None or self.difficulty_max is None
        ):
            raise ValueError("evaluable evidence requires task types and difficulty bounds")
        if len(set(self.task_types)) != len(self.task_types):
            raise ValueError("aggregate task types must be unique")
        expected_confidence_label = (
            DiagnosticConfidenceLabel.HIGH
            if self.estimate_confidence >= 0.8
            else (
                DiagnosticConfidenceLabel.MODERATE
                if self.estimate_confidence >= 0.6
                else DiagnosticConfidenceLabel.LOW
            )
        )
        if self.confidence_label != expected_confidence_label:
            raise ValueError("confidence label must match estimate_confidence")
        if self.coverage_status == CoverageStatus.NOT_ASSESSED and (
            self.evidence_count != 0
            or self.insufficient_evidence_count != 0
            or self.estimated_score is not None
            or self.estimate_confidence != 0
            or self.band is not None
            or self.cefr_band is not None
            or self.maximum_demonstrated_difficulty is not None
            or self.task_types
            or self.difficulty_min is not None
            or self.difficulty_max is not None
            or self.skill_id is not None
        ):
            raise ValueError("not_assessed axes cannot contain inferred results")
        if self.coverage_status != CoverageStatus.NOT_ASSESSED:
            if self.band == DiagnosticBand.INSUFFICIENT_EVIDENCE:
                if self.estimated_score is not None:
                    raise ValueError("insufficient evidence cannot contain an estimated score")
            elif self.band is None or self.estimated_score is None:
                raise ValueError("observed estimates require both a band and a score")
        if self.cefr_band is not None and (
            self.skill_id is None
            or self.positive_evidence_count < 2
            or self.estimate_confidence < 0.6
        ):
            raise ValueError("CEFR hints require a skill and sufficient positive evidence")
        if (
            self.axis in FUTURE_MODALITY_AXES
            and self.coverage_status != CoverageStatus.NOT_ASSESSED
        ):
            raise ValueError("future modalities must remain not_assessed in text v1")
        return self


class CreateSessionCommand(APIModel):
    request_id: UUID
    profile_id: Literal[1] = 1
    diagnostic_version: str = Field(default=DEFAULT_DIAGNOSTIC_VERSION, min_length=1, max_length=50)
    persistence_version: str = Field(
        default=DEFAULT_PERSISTENCE_VERSION, min_length=1, max_length=50
    )
    curriculum_version: str = Field(min_length=1, max_length=30)
    instruction_language: str = Field(default="es", min_length=2, max_length=20)
    target_task_count: int = Field(default=14, ge=12, le=16)
    max_task_count: Literal[20] = 20
    target_duration_seconds: int = Field(default=1200, ge=900, le=1500)
    max_duration_seconds: Literal[1500] = 1500
    repeats_session_id: int | None = Field(default=None, ge=1)


class SessionOperation(APIModel):
    session_id: int = Field(ge=1)
    operation_id: UUID
    reason: str | None = Field(default=None, max_length=100)


class SessionQuery(APIModel):
    session_id: int = Field(ge=1)


class SelectTaskCommand(APIModel):
    session_id: int = Field(ge=1)
    operation_id: UUID


class CorrectEvaluationCommand(APIModel):
    session_id: int = Field(ge=1)
    response_id: int = Field(ge=1)
    evaluation_id: UUID
    outcome: EvaluationOutcome
    score: float | None = Field(default=None, ge=0, le=1)
    polarity: DiagnosticPolarity
    evaluator_confidence: float = Field(ge=0, le=1)
    justification: str = Field(min_length=1, max_length=10_000)
    reason_codes: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def correction_shape_is_valid(self):
        _validate_evaluation_shape(self.outcome, self.score, self.polarity)
        if self.outcome == EvaluationOutcome.NOT_EVALUABLE and self.evaluator_confidence != 0:
            raise ValueError("not_evaluable corrections require zero evaluator confidence")
        return self


class SessionStateReceipt(APIModel):
    session_id: int = Field(ge=1)
    state: EngineSessionState
    persisted_status: DiagnosticSessionStatus
    active_seconds: int = Field(ge=0)
    tasks_presented: int = Field(ge=0, le=20)
    tasks_evaluable: int = Field(ge=0, le=20)
    active_section: str | None = None
    termination_reason: str | None = None
    started_at: AwareDatetime
    paused_at: AwareDatetime | None = None
    resumed_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    abandoned_at: AwareDatetime | None = None


class TaskReceipt(APIModel):
    task_id: int = Field(ge=1)
    session_id: int = Field(ge=1)
    sequence: int = Field(ge=1, le=20)
    status: DiagnosticTaskStatus
    candidate: TaskCandidate
    selection_reason: str
    presented_at: AwareDatetime | None = None
    created: bool


class SelectionReceipt(APIModel):
    session_id: int = Field(ge=1)
    task: TaskReceipt | None = None
    stop: StopDecision


class ResponseReceipt(APIModel):
    response_id: int = Field(ge=1)
    task_id: int = Field(ge=1)
    attempt_number: int = Field(ge=1)
    evaluation_revision: int = Field(ge=1)
    evaluation: EvaluationResult
    task_status: DiagnosticTaskStatus
    created: bool


class AggregateReceipt(APIModel):
    session_id: int = Field(ge=1)
    axes: list[AxisAggregate]
    persisted_result_ids: list[int] = Field(default_factory=list)
    created_revisions: int = Field(ge=0)


class SessionSnapshot(APIModel):
    session: SessionStateReceipt
    current_task: TaskReceipt | None = None
    aggregates: list[AxisAggregate] = Field(default_factory=list)
    stop: StopDecision


def require_aware_utc(value: datetime) -> datetime:
    """Validate injected clocks at the engine boundary."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("diagnostic engine clocks must return timezone-aware UTC values")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError("diagnostic engine clocks must return UTC")
    return value


def _validate_evaluation_shape(
    outcome: EvaluationOutcome,
    score: float | None,
    polarity: DiagnosticPolarity,
) -> None:
    expected = {
        EvaluationOutcome.INCORRECT: (0.0, DiagnosticPolarity.NEGATIVE),
        EvaluationOutcome.PARTIAL: (0.4, DiagnosticPolarity.POSITIVE),
        EvaluationOutcome.CORRECT_WITH_HELP: (0.7, DiagnosticPolarity.POSITIVE),
        EvaluationOutcome.CORRECT_WITHOUT_HELP: (1.0, DiagnosticPolarity.POSITIVE),
        EvaluationOutcome.NOT_EVALUABLE: (None, DiagnosticPolarity.INSUFFICIENT),
    }
    expected_score, expected_polarity = expected[outcome]
    if score != expected_score or polarity != expected_polarity:
        raise ValueError(
            f"{outcome.value} requires score {expected_score!r} and polarity "
            f"{expected_polarity.value}"
        )


__all__ = [
    "AggregateReceipt",
    "AmbiguityRisk",
    "AxisAggregate",
    "CORE_TEXT_AXES",
    "CandidateProvider",
    "CompletionReason",
    "CorrectEvaluationCommand",
    "CoverageStatus",
    "CreateSessionCommand",
    "DIAGNOSTIC_ENGINE_VERSION",
    "DeterministicRubric",
    "EngineSessionState",
    "EvaluationOutcome",
    "EvaluationResult",
    "EvidenceObservation",
    "FUTURE_MODALITY_AXES",
    "LEGACY_TEXT_ANSWER_CONTRACT",
    "NoAnswer",
    "OPTION_ID_ANSWER_CONTRACT",
    "OptionIdentifier",
    "PresentedTaskObservation",
    "ResponseReceipt",
    "ResponseSubmission",
    "RubricStrategy",
    "SingleChoiceAnswer",
    "SelectTaskCommand",
    "SelectionContext",
    "SelectionDecision",
    "SelectionReceipt",
    "SessionAction",
    "SessionOperation",
    "SessionQuery",
    "SessionSnapshot",
    "SessionStateReceipt",
    "StopDecision",
    "StructuredAnswer",
    "TEXT_AXES",
    "TaskAction",
    "TaskCandidate",
    "TaskReceipt",
    "TextAnswer",
    "STRUCTURED_TEXT_ANSWER_CONTRACT",
    "require_aware_utc",
]
