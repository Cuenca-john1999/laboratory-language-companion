"""HTTP adapter for the deterministic diagnostic engine.

This module deliberately contains no pedagogical decisions. It validates HTTP
payloads, invokes ``DiagnosticEngineService``, and projects internal receipts to
public DTOs that omit evaluator-only fields.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import JsonValue, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from deutschos_api.content import DiagnosticContentError, FilesystemCandidateProvider
from deutschos_api.core.config import PROJECT_ROOT
from deutschos_api.db.session import get_db
from deutschos_api.diagnostic_engine.exceptions import (
    CandidateUnavailableError,
    DiagnosticConcurrencyError,
    DiagnosticEngineError,
    DiagnosticIdempotencyConflictError,
    DiagnosticNotFoundError,
    EvaluationConflictError,
    InvalidTransitionError,
)
from deutschos_api.diagnostic_engine.schemas import (
    AxisAggregate,
    CandidateProvider,
    CorrectEvaluationCommand,
    CreateSessionCommand,
    ResponseReceipt,
    ResponseSubmission,
    SelectionReceipt,
    SelectTaskCommand,
    SessionOperation,
    SessionQuery,
    SessionSnapshot,
    SessionStateReceipt,
    StopDecision,
    TaskReceipt,
)
from deutschos_api.diagnostic_engine.service import DiagnosticEngineService
from deutschos_api.schemas.diagnostic_api import (
    DiagnosticAxisResultPublic,
    DiagnosticCorrectionRequest,
    DiagnosticEvaluationPublic,
    DiagnosticNextTaskRequest,
    DiagnosticOperationRequest,
    DiagnosticResponsePublic,
    DiagnosticResponseSubmitRequest,
    DiagnosticResultsPublic,
    DiagnosticSelectionPublic,
    DiagnosticSessionCreateRequest,
    DiagnosticSessionPublic,
    DiagnosticSessionSnapshotPublic,
    DiagnosticStopPublic,
    DiagnosticTaskPublic,
)

router = APIRouter(prefix="/api/diagnostic", tags=["diagnostic"])

DIAGNOSTIC_CONTENT_DIRECTORY = PROJECT_ROOT / "data" / "diagnostic"
_PROVIDER_UNAVAILABLE_DETAIL = (
    "El banco diagnóstico versionado no está configurado en esta instalación."
)
_RESERVED_TASK_KEYS = frozenset(
    {
        "accepted_answers",
        "answer_key",
        "correct_answer",
        "equivalence_key",
        "evaluator",
        "evaluator_only",
        "expected_answer",
        "internal",
        "partial_answers",
        "prerequisite_candidate_ids",
        "private",
        "reason_codes",
        "rubric",
        "scoring",
        "selection_reason",
        "solution",
        "solutions",
    }
)


class DiagnosticPublicContractError(RuntimeError):
    """A provider attempted to expose evaluator-only task content."""


class _PublicSafeCandidateProvider:
    def __init__(self, provider: CandidateProvider) -> None:
        self.provider = provider

    def candidates(self, *, diagnostic_version: str):
        candidates = self.provider.candidates(diagnostic_version=diagnostic_version)
        for candidate in candidates:
            _assert_public_task_content(candidate.content)
        return candidates


def get_diagnostic_candidate_provider() -> CandidateProvider:
    """Load the local bank, remaining unavailable until it has reviewed tasks."""

    try:
        provider = FilesystemCandidateProvider.from_directory(DIAGNOSTIC_CONTENT_DIRECTORY)
    except DiagnosticContentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El banco diagnóstico local no supera la validación.",
        ) from exc
    if not provider.has_candidates:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_PROVIDER_UNAVAILABLE_DETAIL,
        )
    return provider


def get_diagnostic_service(
    db: Session = Depends(get_db),
    candidate_provider: CandidateProvider = Depends(get_diagnostic_candidate_provider),
) -> DiagnosticEngineService:
    return DiagnosticEngineService(db, _PublicSafeCandidateProvider(candidate_provider))


def _execute[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except DiagnosticNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El recurso diagnóstico solicitado no existe.",
        ) from exc
    except (
        DiagnosticIdempotencyConflictError,
        EvaluationConflictError,
        InvalidTransitionError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La operación entra en conflicto con el estado diagnóstico actual.",
        ) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="El contrato diagnóstico contiene una combinación inválida.",
        ) from exc
    except DiagnosticConcurrencyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El diagnóstico local está ocupado; vuelve a intentarlo.",
            headers={"Retry-After": "1"},
        ) from exc
    except (CandidateUnavailableError, DiagnosticPublicContractError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El contenido diagnóstico versionado no está disponible.",
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La persistencia diagnóstica local no está disponible.",
        ) from exc
    except DiagnosticEngineError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El motor diagnóstico local no está disponible.",
        ) from exc


def _assert_public_task_content(value: JsonValue) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalised = key.casefold().replace("-", "_")
            if normalised.startswith("_") or normalised in _RESERVED_TASK_KEYS:
                raise DiagnosticPublicContractError("reserved task content key")
            _assert_public_task_content(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_public_task_content(nested)


def _public_session(receipt: SessionStateReceipt) -> DiagnosticSessionPublic:
    return DiagnosticSessionPublic(
        session_id=receipt.session_id,
        state=receipt.state,
        phase=receipt.persisted_status,
        active_seconds=receipt.active_seconds,
        tasks_presented=receipt.tasks_presented,
        tasks_evaluable=receipt.tasks_evaluable,
        active_section=receipt.active_section,
        termination_reason=receipt.termination_reason,
        started_at=receipt.started_at,
        paused_at=receipt.paused_at,
        resumed_at=receipt.resumed_at,
        completed_at=receipt.completed_at,
        abandoned_at=receipt.abandoned_at,
    )


def _public_task(receipt: TaskReceipt) -> DiagnosticTaskPublic:
    candidate = receipt.candidate
    _assert_public_task_content(candidate.content)
    return DiagnosticTaskPublic(
        task_id=receipt.task_id,
        session_id=receipt.session_id,
        sequence=receipt.sequence,
        status=receipt.status,
        content_version=candidate.version,
        task_type=candidate.task_type,
        primary_axis=candidate.axis,
        secondary_axes=list(candidate.secondary_axes),
        skill_id=candidate.skill_id,
        difficulty=candidate.difficulty,
        modality="text",
        content=candidate.content,
        options=list(candidate.options),
        estimated_seconds=candidate.estimated_seconds,
        presented_at=receipt.presented_at,
        created=receipt.created,
    )


def _public_stop(stop: StopDecision) -> DiagnosticStopPublic:
    return DiagnosticStopPublic(
        should_stop=stop.should_stop,
        reason=stop.reason,
        partial=stop.partial,
        detail=stop.detail,
    )


def _public_axis(aggregate: AxisAggregate) -> DiagnosticAxisResultPublic:
    return DiagnosticAxisResultPublic(
        axis=aggregate.axis,
        skill_id=aggregate.skill_id,
        evidence_count=aggregate.evidence_count,
        positive_evidence_count=aggregate.positive_evidence_count,
        negative_evidence_count=aggregate.negative_evidence_count,
        insufficient_evidence_count=aggregate.insufficient_evidence_count,
        maximum_demonstrated_difficulty=aggregate.maximum_demonstrated_difficulty,
        estimated_score=aggregate.estimated_score,
        estimate_confidence=aggregate.estimate_confidence,
        confidence_label=aggregate.confidence_label,
        band=aggregate.band,
        cefr_band=aggregate.cefr_band,
        coverage_status=aggregate.coverage_status,
        task_types=list(aggregate.task_types),
        difficulty_min=aggregate.difficulty_min,
        difficulty_max=aggregate.difficulty_max,
        explanation=aggregate.reason,
    )


def _public_response(receipt: ResponseReceipt) -> DiagnosticResponsePublic:
    return DiagnosticResponsePublic(
        response_id=receipt.response_id,
        task_id=receipt.task_id,
        attempt_number=receipt.attempt_number,
        evaluation_revision=receipt.evaluation_revision,
        evaluation=DiagnosticEvaluationPublic(
            outcome=receipt.evaluation.outcome,
            score=receipt.evaluation.score,
            polarity=receipt.evaluation.polarity,
            evaluator_confidence=receipt.evaluation.evaluator_confidence,
        ),
        task_status=receipt.task_status,
        created=receipt.created,
    )


def _public_selection(receipt: SelectionReceipt) -> DiagnosticSelectionPublic:
    return DiagnosticSelectionPublic(
        session_id=receipt.session_id,
        task=_public_task(receipt.task) if receipt.task is not None else None,
        stop=_public_stop(receipt.stop),
    )


def _public_snapshot(snapshot: SessionSnapshot) -> DiagnosticSessionSnapshotPublic:
    return DiagnosticSessionSnapshotPublic(
        session=_public_session(snapshot.session),
        current_task=(
            _public_task(snapshot.current_task) if snapshot.current_task is not None else None
        ),
        results=[_public_axis(aggregate) for aggregate in snapshot.aggregates],
        stop=_public_stop(snapshot.stop),
    )


def _session_operation(
    session_id: int,
    payload: DiagnosticOperationRequest,
) -> SessionOperation:
    return SessionOperation(
        session_id=session_id,
        operation_id=payload.operation_id,
        reason=payload.reason,
    )


@router.post(
    "/sessions",
    response_model=DiagnosticSessionPublic,
    status_code=status.HTTP_201_CREATED,
)
def create_session(
    payload: DiagnosticSessionCreateRequest,
    service: DiagnosticEngineService = Depends(get_diagnostic_service),
) -> DiagnosticSessionPublic:
    command = CreateSessionCommand.model_validate(payload.model_dump())
    return _execute(lambda: _public_session(service.create_session(command)))


@router.get(
    "/sessions/{session_id}",
    response_model=DiagnosticSessionSnapshotPublic,
)
def read_session(
    session_id: int = Path(ge=1),
    service: DiagnosticEngineService = Depends(get_diagnostic_service),
) -> DiagnosticSessionSnapshotPublic:
    query = SessionQuery(session_id=session_id)
    return _execute(lambda: _public_snapshot(service.get_session_state(query)))


@router.post("/sessions/{session_id}/start", response_model=DiagnosticSessionPublic)
def start_session(
    payload: DiagnosticOperationRequest,
    session_id: int = Path(ge=1),
    service: DiagnosticEngineService = Depends(get_diagnostic_service),
) -> DiagnosticSessionPublic:
    command = _session_operation(session_id, payload)
    return _execute(lambda: _public_session(service.start_session(command)))


@router.post("/sessions/{session_id}/pause", response_model=DiagnosticSessionPublic)
def pause_session(
    payload: DiagnosticOperationRequest,
    session_id: int = Path(ge=1),
    service: DiagnosticEngineService = Depends(get_diagnostic_service),
) -> DiagnosticSessionPublic:
    command = _session_operation(session_id, payload)
    return _execute(lambda: _public_session(service.pause_session(command)))


@router.post("/sessions/{session_id}/resume", response_model=DiagnosticSessionPublic)
def resume_session(
    payload: DiagnosticOperationRequest,
    session_id: int = Path(ge=1),
    service: DiagnosticEngineService = Depends(get_diagnostic_service),
) -> DiagnosticSessionPublic:
    command = _session_operation(session_id, payload)
    return _execute(lambda: _public_session(service.resume_session(command)))


@router.post("/sessions/{session_id}/abandon", response_model=DiagnosticSessionPublic)
def abandon_session(
    payload: DiagnosticOperationRequest,
    session_id: int = Path(ge=1),
    service: DiagnosticEngineService = Depends(get_diagnostic_service),
) -> DiagnosticSessionPublic:
    command = _session_operation(session_id, payload)
    return _execute(lambda: _public_session(service.abandon_session(command)))


@router.post("/sessions/{session_id}/fail", response_model=DiagnosticSessionPublic)
def fail_session(
    payload: DiagnosticOperationRequest,
    session_id: int = Path(ge=1),
    service: DiagnosticEngineService = Depends(get_diagnostic_service),
) -> DiagnosticSessionPublic:
    command = _session_operation(session_id, payload)
    return _execute(lambda: _public_session(service.fail_session(command)))


@router.post("/sessions/{session_id}/next-task", response_model=DiagnosticSelectionPublic)
def next_task(
    payload: DiagnosticNextTaskRequest,
    session_id: int = Path(ge=1),
    service: DiagnosticEngineService = Depends(get_diagnostic_service),
) -> DiagnosticSelectionPublic:
    command = SelectTaskCommand(session_id=session_id, operation_id=payload.operation_id)
    return _execute(lambda: _public_selection(service.select_next_task(command)))


@router.post("/sessions/{session_id}/responses", response_model=DiagnosticResponsePublic)
def submit_response(
    payload: DiagnosticResponseSubmitRequest,
    session_id: int = Path(ge=1),
    service: DiagnosticEngineService = Depends(get_diagnostic_service),
) -> DiagnosticResponsePublic:
    command = ResponseSubmission(
        session_id=session_id,
        **payload.model_dump(),
    )
    return _execute(lambda: _public_response(service.submit_response(command)))


@router.post(
    "/responses/{response_id}/corrections",
    response_model=DiagnosticResponsePublic,
)
def correct_response(
    payload: DiagnosticCorrectionRequest,
    response_id: int = Path(ge=1),
    service: DiagnosticEngineService = Depends(get_diagnostic_service),
) -> DiagnosticResponsePublic:
    def record_correction() -> DiagnosticResponsePublic:
        command = CorrectEvaluationCommand(
            response_id=response_id,
            **payload.model_dump(),
        )
        return _public_response(service.record_evaluation(command))

    return _execute(record_correction)


@router.get(
    "/sessions/{session_id}/results",
    response_model=DiagnosticResultsPublic,
)
def results(
    session_id: int = Path(ge=1),
    service: DiagnosticEngineService = Depends(get_diagnostic_service),
) -> DiagnosticResultsPublic:
    query = SessionQuery(session_id=session_id)

    def read_results() -> DiagnosticResultsPublic:
        snapshot = service.get_session_state(query)
        return DiagnosticResultsPublic(
            session_id=session_id,
            results=[_public_axis(aggregate) for aggregate in snapshot.aggregates],
        )

    return _execute(read_results)


@router.post(
    "/sessions/{session_id}/complete",
    response_model=DiagnosticSessionSnapshotPublic,
)
def complete_session(
    payload: DiagnosticOperationRequest,
    session_id: int = Path(ge=1),
    service: DiagnosticEngineService = Depends(get_diagnostic_service),
) -> DiagnosticSessionSnapshotPublic:
    command = _session_operation(session_id, payload)
    return _execute(lambda: _public_snapshot(service.complete_session(command)))


__all__ = [
    "DIAGNOSTIC_CONTENT_DIRECTORY",
    "get_diagnostic_candidate_provider",
    "get_diagnostic_service",
    "router",
]
