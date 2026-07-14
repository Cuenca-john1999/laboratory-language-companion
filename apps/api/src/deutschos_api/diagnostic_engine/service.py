"""Transactional orchestration for the deterministic diagnostic engine.

All pedagogical choices live in the pure selector, scorer, and aggregator.
This service only serialises SQLite writes, reconstructs persisted state, and
commits each public mutation atomically.  It never imports the Learning Engine,
Ollama, or HTTP layers and never writes StudentSkill or SkillEvidence.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import func, null, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from deutschos_api.core.time import utc_now
from deutschos_api.diagnostic_engine.aggregation import aggregate_by_axis_and_skill
from deutschos_api.diagnostic_engine.exceptions import (
    CandidateUnavailableError,
    DiagnosticConcurrencyError,
    DiagnosticIdempotencyConflictError,
    DiagnosticNotFoundError,
    EvaluationConflictError,
    InvalidSubmissionContractError,
    InvalidTransitionError,
)
from deutschos_api.diagnostic_engine.schemas import (
    DIAGNOSTIC_ENGINE_VERSION,
    LEGACY_TEXT_ANSWER_CONTRACT,
    OPTION_ID_ANSWER_CONTRACT,
    STRUCTURED_TEXT_ANSWER_CONTRACT,
    AggregateReceipt,
    AxisAggregate,
    CandidateProvider,
    CompletionReason,
    CorrectEvaluationCommand,
    CoverageStatus,
    CreateSessionCommand,
    DeterministicRubric,
    EngineSessionState,
    EvaluationOutcome,
    EvaluationResult,
    EvidenceObservation,
    NoAnswer,
    PresentedTaskObservation,
    ResponseReceipt,
    ResponseSubmission,
    SelectionContext,
    SelectionReceipt,
    SelectTaskCommand,
    SessionAction,
    SessionOperation,
    SessionQuery,
    SessionSnapshot,
    SessionStateReceipt,
    SingleChoiceAnswer,
    StopDecision,
    TaskAction,
    TaskCandidate,
    TaskReceipt,
    TextAnswer,
    require_aware_utc,
)
from deutschos_api.diagnostic_engine.scoring import evaluate_submission
from deutschos_api.diagnostic_engine.selector import decide_stop, select_candidate
from deutschos_api.diagnostic_engine.state_machine import (
    ACTIVE_SESSION_STATUSES,
    engine_state,
    session_transition,
    task_transition,
)
from deutschos_api.models import (
    Curriculum,
    CurriculumSkill,
    DiagnosticAxis,
    DiagnosticBand,
    DiagnosticOutcome,
    DiagnosticPolarity,
    DiagnosticResponse,
    DiagnosticResult,
    DiagnosticSession,
    DiagnosticSessionStatus,
    DiagnosticTask,
    DiagnosticTaskStatus,
    StudentProfile,
)

_OUTCOME_TO_DB = {
    EvaluationOutcome.INCORRECT: DiagnosticOutcome.FAILURE,
    EvaluationOutcome.PARTIAL: DiagnosticOutcome.PARTIAL,
    EvaluationOutcome.CORRECT_WITH_HELP: DiagnosticOutcome.CORRECT_WITH_HELP,
    EvaluationOutcome.CORRECT_WITHOUT_HELP: DiagnosticOutcome.CORRECT_WITHOUT_HELP,
    EvaluationOutcome.NOT_EVALUABLE: DiagnosticOutcome.NOT_EVALUABLE,
}
_OUTCOME_FROM_DB = {value: key for key, value in _OUTCOME_TO_DB.items()}
_OPEN_TASK_STATUSES = {
    DiagnosticTaskStatus.SELECTED,
    DiagnosticTaskStatus.PRESENTED,
    DiagnosticTaskStatus.NOT_UNDERSTOOD,
}
_SELECTABLE_SESSION_STATUSES = {
    DiagnosticSessionStatus.ONBOARDING,
    DiagnosticSessionStatus.CALIBRATING,
    DiagnosticSessionStatus.ASSESSING,
    DiagnosticSessionStatus.REVIEWING,
}
_PROCESS_INSTANCE_ID = str(uuid4())


def _is_sqlite_lock_error(exc: OperationalError) -> bool:
    message = str(exc.orig if exc.orig is not None else exc).lower()
    return "database is locked" in message or "database is busy" in message


class DiagnosticEngineService:
    """Internal API for one deterministic diagnostic session at a time."""

    def __init__(
        self,
        db: Session,
        candidate_provider: CandidateProvider,
        *,
        clock: Callable[[], datetime] = utc_now,
        process_instance_id: str = _PROCESS_INSTANCE_ID,
    ) -> None:
        if not process_instance_id:
            raise ValueError("process_instance_id cannot be empty")
        self.db = db
        self.candidate_provider = candidate_provider
        self.clock = clock
        self.process_instance_id = process_instance_id

    def _now(self) -> datetime:
        return require_aware_utc(self.clock())

    def _begin_serialized_write(self) -> None:
        connection = self.db.connection()
        if connection.dialect.name != "sqlite":
            return
        raw_connection = connection.connection.driver_connection
        try:
            if getattr(raw_connection, "in_transaction", False):
                connection.execute(text("UPDATE diagnostic_sessions SET id = id WHERE 0"))
            else:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            # Callers may reuse a SQLAlchemy Session with expire_on_commit=False.
            # Once the writer lock is ours, force subsequent Session.get/select
            # calls to observe the state that was actually serialised.
            self.db.expire_all()
        except OperationalError as exc:
            self.db.rollback()
            if _is_sqlite_lock_error(exc):
                raise DiagnosticConcurrencyError(
                    "La base local está ocupada por otra operación diagnóstica."
                ) from exc
            raise

    def _write(self, operation: Callable[[], Any]) -> Any:
        try:
            self._begin_serialized_write()
            result = operation()
            self.db.commit()
            return result
        except OperationalError as exc:
            self.db.rollback()
            if _is_sqlite_lock_error(exc):
                raise DiagnosticConcurrencyError(
                    "La base local está ocupada por otra operación diagnóstica."
                ) from exc
            raise
        except Exception:
            self.db.rollback()
            raise

    def _get_session(self, session_id: int) -> DiagnosticSession:
        diagnostic_session = self.db.get(DiagnosticSession, session_id)
        if diagnostic_session is None:
            raise DiagnosticNotFoundError(f"No existe la sesión diagnóstica {session_id}.")
        return diagnostic_session

    def _get_task(self, task_id: int) -> DiagnosticTask:
        task = self.db.get(DiagnosticTask, task_id)
        if task is None:
            raise DiagnosticNotFoundError(f"No existe la tarea diagnóstica {task_id}.")
        return task

    def _selection_state(self, diagnostic_session: DiagnosticSession) -> dict[str, Any]:
        state = deepcopy(diagnostic_session.selection_state or {})
        state.setdefault("engine_version", DIAGNOSTIC_ENGINE_VERSION)
        state.setdefault("transition_log", [])
        state.setdefault("task_transition_log", [])
        state.setdefault("operation_receipts", {})
        return state

    def _operation_is_replay(
        self,
        diagnostic_session: DiagnosticSession,
        operation_id: UUID,
        *,
        kind: str,
        payload: dict[str, Any],
    ) -> bool:
        state = self._selection_state(diagnostic_session)
        receipt = state["operation_receipts"].get(str(operation_id))
        if receipt is None:
            return False
        if receipt.get("kind") != kind or receipt.get("payload") != payload:
            raise DiagnosticIdempotencyConflictError(
                "La clave de idempotencia ya pertenece a otra operación diagnóstica."
            )
        return True

    def _record_operation(
        self,
        diagnostic_session: DiagnosticSession,
        operation_id: UUID,
        *,
        kind: str,
        payload: dict[str, Any],
    ) -> None:
        state = self._selection_state(diagnostic_session)
        receipts = state["operation_receipts"]
        receipts[str(operation_id)] = {"kind": kind, "payload": payload}
        diagnostic_session.selection_state = state

    def _append_session_transition(
        self,
        diagnostic_session: DiagnosticSession,
        *,
        previous: DiagnosticSessionStatus,
        target: DiagnosticSessionStatus,
        action: SessionAction,
        reason: str,
        at: datetime,
        operation_id: UUID | None,
    ) -> None:
        state = self._selection_state(diagnostic_session)
        state["transition_log"].append(
            {
                "from": previous.value,
                "to": target.value,
                "action": action.value,
                "reason": reason,
                "at": at.isoformat(),
                "operation_id": str(operation_id) if operation_id else None,
                "engine_version": DIAGNOSTIC_ENGINE_VERSION,
            }
        )
        diagnostic_session.selection_state = state

    def _append_task_transition(
        self,
        diagnostic_session: DiagnosticSession,
        task: DiagnosticTask,
        *,
        previous: DiagnosticTaskStatus,
        target: DiagnosticTaskStatus,
        action: TaskAction,
        reason: str,
        at: datetime,
    ) -> None:
        if previous == target:
            return
        state = self._selection_state(diagnostic_session)
        state["task_transition_log"].append(
            {
                "task_id": task.id,
                "from": previous.value,
                "to": target.value,
                "action": action.value,
                "reason": reason,
                "at": at.isoformat(),
                "engine_version": DIAGNOSTIC_ENGINE_VERSION,
            }
        )
        diagnostic_session.selection_state = state

    def _effective_active_seconds(
        self, diagnostic_session: DiagnosticSession, now: datetime
    ) -> int:
        total = diagnostic_session.active_seconds
        status = DiagnosticSessionStatus(diagnostic_session.status)
        if status in ACTIVE_SESSION_STATUSES and diagnostic_session.resumed_at is not None:
            state = self._selection_state(diagnostic_session)
            clock_state = state.get("active_clock", {})
            if clock_state.get("process_instance_id") == self.process_instance_id:
                elapsed = max(0, int((now - diagnostic_session.resumed_at).total_seconds()))
                total += elapsed
        return min(total, diagnostic_session.max_duration_seconds)

    def _set_active_clock_owner(self, diagnostic_session: DiagnosticSession, now: datetime) -> None:
        state = self._selection_state(diagnostic_session)
        state["active_clock"] = {
            "process_instance_id": self.process_instance_id,
            "checkpoint_at": now.isoformat(),
        }
        diagnostic_session.selection_state = state

    def _checkpoint_or_adopt_active_clock(
        self, diagnostic_session: DiagnosticSession, now: datetime
    ) -> None:
        if DiagnosticSessionStatus(diagnostic_session.status) not in ACTIVE_SESSION_STATUSES:
            return
        diagnostic_session.active_seconds = self._effective_active_seconds(diagnostic_session, now)
        diagnostic_session.resumed_at = now
        self._set_active_clock_owner(diagnostic_session, now)

    def _stop_active_clock(self, diagnostic_session: DiagnosticSession, now: datetime) -> None:
        diagnostic_session.active_seconds = self._effective_active_seconds(diagnostic_session, now)
        diagnostic_session.resumed_at = None

    def _session_receipt(
        self, diagnostic_session: DiagnosticSession, *, now: datetime | None = None
    ) -> SessionStateReceipt:
        instant = now or self._now()
        return SessionStateReceipt(
            session_id=diagnostic_session.id,
            state=engine_state(diagnostic_session.status),
            persisted_status=DiagnosticSessionStatus(diagnostic_session.status),
            active_seconds=self._effective_active_seconds(diagnostic_session, instant),
            tasks_presented=diagnostic_session.tasks_presented,
            tasks_evaluable=diagnostic_session.tasks_evaluable,
            active_section=diagnostic_session.active_section,
            termination_reason=diagnostic_session.termination_reason,
            started_at=diagnostic_session.started_at,
            paused_at=diagnostic_session.paused_at,
            resumed_at=diagnostic_session.resumed_at,
            completed_at=diagnostic_session.completed_at,
            abandoned_at=diagnostic_session.abandoned_at,
        )

    @staticmethod
    def _create_payload(command: CreateSessionCommand) -> dict[str, Any]:
        return command.model_dump(mode="json", exclude={"request_id"})

    def create_session(self, command: CreateSessionCommand) -> SessionStateReceipt:
        def operation() -> SessionStateReceipt:
            existing = self.db.scalars(
                select(DiagnosticSession).where(
                    DiagnosticSession.random_seed == str(command.request_id)
                )
            ).all()
            if existing:
                if len(existing) != 1 or not self._create_session_matches(existing[0], command):
                    raise DiagnosticIdempotencyConflictError(
                        "La clave de creación ya se usó con otros parámetros."
                    )
                return self._session_receipt(existing[0])

            if self.db.get(StudentProfile, command.profile_id) is None:
                raise DiagnosticNotFoundError("No existe el perfil local requerido.")
            if self.db.get(Curriculum, command.curriculum_version) is None:
                raise DiagnosticNotFoundError("No existe la versión de currículo solicitada.")

            now = self._now()
            diagnostic_session = DiagnosticSession(
                profile_id=command.profile_id,
                diagnostic_version=command.diagnostic_version,
                persistence_version=command.persistence_version,
                curriculum_version=command.curriculum_version,
                status=DiagnosticSessionStatus.NOT_STARTED,
                active_section=None,
                selection_state={
                    "engine_version": DIAGNOSTIC_ENGINE_VERSION,
                    "create_payload": self._create_payload(command),
                    "transition_log": [],
                    "task_transition_log": [],
                    "operation_receipts": {},
                },
                random_seed=str(command.request_id),
                instruction_language=command.instruction_language,
                target_task_count=command.target_task_count,
                max_task_count=command.max_task_count,
                target_duration_seconds=command.target_duration_seconds,
                max_duration_seconds=command.max_duration_seconds,
                active_seconds=0,
                tasks_presented=0,
                tasks_evaluable=0,
                started_at=now,
                repeats_session_id=command.repeats_session_id,
            )
            self.db.add(diagnostic_session)
            self.db.flush()
            return self._session_receipt(diagnostic_session, now=now)

        try:
            return self._write(operation)
        except IntegrityError as exc:
            raise DiagnosticIdempotencyConflictError(
                "No se pudo crear la sesión sin duplicar estado persistido."
            ) from exc

    def _create_session_matches(
        self, diagnostic_session: DiagnosticSession, command: CreateSessionCommand
    ) -> bool:
        persisted = (diagnostic_session.selection_state or {}).get("create_payload")
        return persisted == self._create_payload(command)

    def _transition_session(
        self, command: SessionOperation, action: SessionAction
    ) -> SessionStateReceipt:
        kind = action.value
        payload = {"session_id": command.session_id, "reason": command.reason}

        def operation() -> SessionStateReceipt:
            diagnostic_session = self._get_session(command.session_id)
            if self._operation_is_replay(
                diagnostic_session, command.operation_id, kind=kind, payload=payload
            ):
                return self._session_receipt(diagnostic_session)

            now = self._now()
            previous = DiagnosticSessionStatus(diagnostic_session.status)
            transition = session_transition(
                previous,
                action,
                paused_from=diagnostic_session.paused_from_status,
            )
            if transition.changed:
                if action == SessionAction.START:
                    diagnostic_session.resumed_at = now
                    diagnostic_session.paused_at = None
                    self._set_active_clock_owner(diagnostic_session, now)
                elif action == SessionAction.PAUSE:
                    self._stop_active_clock(diagnostic_session, now)
                    diagnostic_session.paused_from_status = previous
                    diagnostic_session.paused_at = now
                elif action == SessionAction.RESUME:
                    diagnostic_session.resumed_at = now
                    diagnostic_session.paused_from_status = None
                    self._set_active_clock_owner(diagnostic_session, now)
                    if previous == DiagnosticSessionStatus.ERROR:
                        diagnostic_session.termination_reason = None
                elif action == SessionAction.ABANDON:
                    if previous in ACTIVE_SESSION_STATUSES:
                        self._stop_active_clock(diagnostic_session, now)
                    diagnostic_session.abandoned_at = now
                    diagnostic_session.termination_reason = command.reason or "user_abandoned"
                elif action == SessionAction.FAIL:
                    if previous in ACTIVE_SESSION_STATUSES:
                        self._stop_active_clock(diagnostic_session, now)
                        diagnostic_session.paused_from_status = (
                            previous
                            if previous
                            in {
                                DiagnosticSessionStatus.ONBOARDING,
                                DiagnosticSessionStatus.CALIBRATING,
                                DiagnosticSessionStatus.ASSESSING,
                                DiagnosticSessionStatus.REVIEWING,
                            }
                            else DiagnosticSessionStatus.REVIEWING
                        )
                    diagnostic_session.termination_reason = command.reason or "technical_failure"

                diagnostic_session.status = transition.target
                self._append_session_transition(
                    diagnostic_session,
                    previous=previous,
                    target=transition.target,
                    action=action,
                    reason=command.reason or kind,
                    at=now,
                    operation_id=command.operation_id,
                )
            self._record_operation(
                diagnostic_session, command.operation_id, kind=kind, payload=payload
            )
            self.db.flush()
            return self._session_receipt(diagnostic_session, now=now)

        return self._write(operation)

    def start_session(self, command: SessionOperation) -> SessionStateReceipt:
        return self._transition_session(command, SessionAction.START)

    def pause_session(self, command: SessionOperation) -> SessionStateReceipt:
        return self._transition_session(command, SessionAction.PAUSE)

    def resume_session(self, command: SessionOperation) -> SessionStateReceipt:
        return self._transition_session(command, SessionAction.RESUME)

    def abandon_session(self, command: SessionOperation) -> SessionStateReceipt:
        return self._transition_session(command, SessionAction.ABANDON)

    def fail_session(self, command: SessionOperation) -> SessionStateReceipt:
        return self._transition_session(command, SessionAction.FAIL)

    def _apply_internal_session_action(
        self,
        diagnostic_session: DiagnosticSession,
        action: SessionAction,
        *,
        now: datetime,
        reason: str,
        operation_id: UUID | None,
    ) -> None:
        previous = DiagnosticSessionStatus(diagnostic_session.status)
        transition = session_transition(
            previous, action, paused_from=diagnostic_session.paused_from_status
        )
        if not transition.changed:
            return
        diagnostic_session.status = transition.target
        self._append_session_transition(
            diagnostic_session,
            previous=previous,
            target=transition.target,
            action=action,
            reason=reason,
            at=now,
            operation_id=operation_id,
        )

    def _provider_candidates(self, diagnostic_version: str) -> list[TaskCandidate]:
        return list(self.candidate_provider.candidates(diagnostic_version=diagnostic_version))

    def _candidate_storage_rubric(self, candidate: TaskCandidate) -> dict[str, Any]:
        deterministic = candidate.rubric.model_dump(mode="json")
        if candidate.rubric.strategy.value == "option_id":
            for key in ("accepted_answers", "partial_answers", "expected_tokens"):
                deterministic.pop(key, None)
        else:
            for key in ("accepted_option_ids", "partial_option_ids"):
                deterministic.pop(key, None)
        return {
            "deterministic": deterministic,
            "engine": {
                "equivalence_key": candidate.equivalence_key,
                "prerequisite_candidate_ids": list(candidate.prerequisite_candidate_ids),
                "auto_evaluable": candidate.auto_evaluable,
                "ambiguity_risk": candidate.ambiguity_risk.value,
                "estimated_seconds": candidate.estimated_seconds,
                "engine_version": DIAGNOSTIC_ENGINE_VERSION,
            },
        }

    def _candidate_from_task(self, task: DiagnosticTask) -> TaskCandidate:
        try:
            engine_metadata = task.rubric["engine"]
            deterministic = task.rubric["deterministic"]
            return TaskCandidate(
                candidate_id=task.template_id,
                version=task.template_version,
                equivalence_key=engine_metadata["equivalence_key"],
                axis=DiagnosticAxis(task.primary_axis),
                secondary_axes=[DiagnosticAxis(value) for value in task.secondary_axes],
                task_type=task.task_type,
                difficulty=task.difficulty,
                prerequisite_candidate_ids=engine_metadata.get("prerequisite_candidate_ids", []),
                modality=task.modality,
                content=task.content,
                options=task.options,
                expected_answer=task.expected_answer,
                rubric=DeterministicRubric.model_validate(deterministic),
                auto_evaluable=engine_metadata["auto_evaluable"],
                ambiguity_risk=engine_metadata["ambiguity_risk"],
                estimated_seconds=engine_metadata["estimated_seconds"],
                skill_id=task.skill_id,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CandidateUnavailableError(
                f"La tarea persistida {task.id} no contiene metadatos de motor válidos."
            ) from exc

    def _task_receipt(self, task: DiagnosticTask, *, created: bool) -> TaskReceipt:
        return TaskReceipt(
            task_id=task.id,
            session_id=task.session_id,
            sequence=task.sequence,
            status=DiagnosticTaskStatus(task.status),
            candidate=self._candidate_from_task(task),
            selection_reason=task.selection_reason,
            presented_at=task.presented_at,
            created=created,
        )

    def _current_task(self, session_id: int) -> DiagnosticTask | None:
        return self.db.scalar(
            select(DiagnosticTask)
            .where(
                DiagnosticTask.session_id == session_id,
                DiagnosticTask.status.in_([status.value for status in _OPEN_TASK_STATUSES]),
            )
            .order_by(DiagnosticTask.sequence.desc())
            .limit(1)
        )

    def _effective_responses(self, session_id: int) -> dict[int, DiagnosticResponse]:
        rows = self.db.scalars(
            select(DiagnosticResponse)
            .join(DiagnosticTask, DiagnosticTask.id == DiagnosticResponse.task_id)
            .where(DiagnosticTask.session_id == session_id)
            .order_by(
                DiagnosticResponse.task_id,
                DiagnosticResponse.attempt_number,
                DiagnosticResponse.evaluation_revision,
                DiagnosticResponse.id,
            )
        ).all()
        superseded = {
            row.supersedes_response_id for row in rows if row.supersedes_response_id is not None
        }
        leaves = [row for row in rows if row.id not in superseded]
        latest_by_task: dict[int, DiagnosticResponse] = {}
        for row in leaves:
            previous = latest_by_task.get(row.task_id)
            if previous is None or (
                row.attempt_number,
                row.evaluation_revision,
                row.id,
            ) > (
                previous.attempt_number,
                previous.evaluation_revision,
                previous.id,
            ):
                latest_by_task[row.task_id] = row
        return latest_by_task

    def _evidence(self, session_id: int) -> list[EvidenceObservation]:
        diagnostic_session = self._get_session(session_id)
        tasks = self.db.scalars(
            select(DiagnosticTask)
            .where(DiagnosticTask.session_id == session_id)
            .order_by(DiagnosticTask.sequence)
        ).all()
        skill_ids = {task.skill_id for task in tasks if task.skill_id is not None}
        memberships = {
            membership.skill_id: membership
            for membership in self.db.scalars(
                select(CurriculumSkill).where(
                    CurriculumSkill.curriculum_version == diagnostic_session.curriculum_version,
                    CurriculumSkill.skill_id.in_(skill_ids),
                )
            ).all()
        }
        effective = self._effective_responses(session_id)
        evidence: list[EvidenceObservation] = []
        for task in tasks:
            if DiagnosticTaskStatus(task.status) == DiagnosticTaskStatus.INVALIDATED:
                continue
            response = effective.get(task.id)
            if response is None:
                continue
            membership = memberships.get(task.skill_id)
            evidence.append(
                EvidenceObservation(
                    response_id=response.id,
                    task_id=task.id,
                    candidate_id=task.template_id,
                    candidate_version=task.template_version,
                    axis=DiagnosticAxis(task.primary_axis),
                    task_type=task.task_type,
                    difficulty=task.difficulty,
                    attempt_number=response.attempt_number,
                    outcome=_OUTCOME_FROM_DB[DiagnosticOutcome(response.outcome)],
                    score=response.score,
                    polarity=DiagnosticPolarity(response.polarity),
                    evaluator_confidence=response.evaluator_confidence,
                    assistance=list(response.assistance),
                    skill_id=task.skill_id,
                    skill_cefr_reference=(
                        membership.cefr_reference if membership is not None else None
                    ),
                )
            )
        return evidence

    def _presented(self, session_id: int) -> list[PresentedTaskObservation]:
        tasks = self.db.scalars(
            select(DiagnosticTask)
            .where(
                DiagnosticTask.session_id == session_id,
                DiagnosticTask.presented_at.is_not(None),
            )
            .order_by(DiagnosticTask.sequence)
        ).all()
        return [
            PresentedTaskObservation(
                task_id=task.id,
                candidate_id=task.template_id,
                candidate_version=task.template_version,
                equivalence_key=self._candidate_from_task(task).equivalence_key,
                axis=DiagnosticAxis(task.primary_axis),
                task_type=task.task_type,
                difficulty=task.difficulty,
                tiebreak=task.selection_reason == "contradiction_tiebreak",
            )
            for task in tasks
        ]

    def _context(self, diagnostic_session: DiagnosticSession, *, now: datetime) -> SelectionContext:
        return SelectionContext(
            presented=self._presented(diagnostic_session.id),
            evidence=self._evidence(diagnostic_session.id),
            active_seconds=self._effective_active_seconds(diagnostic_session, now),
            target_task_count=diagnostic_session.target_task_count,
            max_task_count=diagnostic_session.max_task_count,
            max_duration_seconds=diagnostic_session.max_duration_seconds,
        )

    def _refresh_counters(self, diagnostic_session: DiagnosticSession) -> None:
        diagnostic_session.tasks_presented = int(
            self.db.scalar(
                select(func.count(DiagnosticTask.id)).where(
                    DiagnosticTask.session_id == diagnostic_session.id,
                    DiagnosticTask.presented_at.is_not(None),
                )
            )
            or 0
        )
        diagnostic_session.tasks_evaluable = sum(
            response.outcome != DiagnosticOutcome.NOT_EVALUABLE
            for response in self._effective_responses(diagnostic_session.id).values()
        )

    def select_next_task(self, command: SelectTaskCommand) -> SelectionReceipt:
        payload = {"session_id": command.session_id}

        def operation() -> SelectionReceipt:
            diagnostic_session = self._get_session(command.session_id)
            if self._operation_is_replay(
                diagnostic_session,
                command.operation_id,
                kind="select_next_task",
                payload=payload,
            ):
                task_id = self._selection_state(diagnostic_session)["operation_receipts"][
                    str(command.operation_id)
                ].get("task_id")
                task = self._get_task(task_id) if task_id is not None else None
                return SelectionReceipt(
                    session_id=diagnostic_session.id,
                    task=self._task_receipt(task, created=False) if task else None,
                    stop=(
                        StopDecision(
                            should_stop=False,
                            reason=CompletionReason.CONTINUE,
                            detail="Reintento de una selección ya persistida.",
                        )
                        if task is not None
                        else self._stop_for_current_state(diagnostic_session)
                    ),
                )

            status = DiagnosticSessionStatus(diagnostic_session.status)
            if status not in _SELECTABLE_SESSION_STATUSES:
                raise InvalidTransitionError("Solo una sesión activa puede seleccionar tareas.")

            now = self._now()
            self._checkpoint_or_adopt_active_clock(diagnostic_session, now)
            current = self._current_task(diagnostic_session.id)
            if current is not None:
                if DiagnosticTaskStatus(current.status) == DiagnosticTaskStatus.SELECTED:
                    previous = DiagnosticTaskStatus.SELECTED
                    current.status = DiagnosticTaskStatus.PRESENTED
                    current.presented_at = now
                    self._append_task_transition(
                        diagnostic_session,
                        current,
                        previous=previous,
                        target=DiagnosticTaskStatus.PRESENTED,
                        action=TaskAction.PRESENT,
                        reason="recover_selected_task",
                        at=now,
                    )
                    self.db.flush()
                self._refresh_counters(diagnostic_session)
                self._record_selection_operation(
                    diagnostic_session, command.operation_id, payload, current.id
                )
                self.db.flush()
                return SelectionReceipt(
                    session_id=diagnostic_session.id,
                    task=self._task_receipt(current, created=False),
                    stop=StopDecision(
                        should_stop=False,
                        reason=CompletionReason.CONTINUE,
                        detail="Hay una tarea presentada pendiente.",
                    ),
                )

            if status == DiagnosticSessionStatus.ONBOARDING:
                self._apply_internal_session_action(
                    diagnostic_session,
                    SessionAction.BEGIN_CALIBRATION,
                    now=now,
                    reason="first_task_requested",
                    operation_id=command.operation_id,
                )

            context = self._context(diagnostic_session, now=now)
            candidates = self._provider_candidates(diagnostic_session.diagnostic_version)
            decision = select_candidate(candidates, context)
            stop = decide_stop(context, candidates_available=decision is not None)
            if stop.should_stop:
                self._move_to_review_if_possible(
                    diagnostic_session,
                    now=now,
                    operation_id=command.operation_id,
                    reason=stop.reason.value,
                )
                self._record_selection_operation(
                    diagnostic_session, command.operation_id, payload, None
                )
                self.db.flush()
                return SelectionReceipt(
                    session_id=diagnostic_session.id,
                    task=None,
                    stop=stop,
                )
            if decision is None:
                raise CandidateUnavailableError("No existe un candidato de texto compatible.")
            if (
                DiagnosticSessionStatus(diagnostic_session.status)
                == DiagnosticSessionStatus.REVIEWING
            ):
                self._apply_internal_session_action(
                    diagnostic_session,
                    SessionAction.CONTINUE_ASSESSMENT,
                    now=now,
                    reason="new_evidence_needed_after_review",
                    operation_id=command.operation_id,
                )

            sequence = (
                self.db.scalar(
                    select(func.max(DiagnosticTask.sequence)).where(
                        DiagnosticTask.session_id == diagnostic_session.id
                    )
                )
                or 0
            ) + 1
            candidate = decision.candidate
            if candidate.skill_id is not None:
                membership = self.db.scalar(
                    select(CurriculumSkill).where(
                        CurriculumSkill.curriculum_version == diagnostic_session.curriculum_version,
                        CurriculumSkill.skill_id == candidate.skill_id,
                    )
                )
                if membership is None:
                    raise CandidateUnavailableError(
                        "La habilidad del candidato no pertenece al currículo de la sesión."
                    )
            task = DiagnosticTask(
                session_id=diagnostic_session.id,
                sequence=sequence,
                status=DiagnosticTaskStatus.SELECTED,
                template_id=candidate.candidate_id,
                template_version=candidate.version,
                task_type=candidate.task_type,
                primary_axis=candidate.axis,
                secondary_axes=[axis.value for axis in candidate.secondary_axes],
                skill_id=candidate.skill_id,
                difficulty=candidate.difficulty,
                modality="text",
                selection_reason=decision.reason,
                content=candidate.content,
                options=candidate.options,
                expected_answer=(
                    candidate.expected_answer if candidate.expected_answer is not None else null()
                ),
                rubric=self._candidate_storage_rubric(candidate),
                origin="bank",
                generator_version=None,
                selected_at=now,
            )
            self.db.add(task)
            self.db.flush()
            previous_task_status = DiagnosticTaskStatus.SELECTED
            task.status = DiagnosticTaskStatus.PRESENTED
            task.presented_at = now
            self._append_task_transition(
                diagnostic_session,
                task,
                previous=previous_task_status,
                target=DiagnosticTaskStatus.PRESENTED,
                action=TaskAction.PRESENT,
                reason=decision.reason,
                at=now,
            )
            self.db.flush()
            diagnostic_session.active_section = candidate.axis.value
            self._refresh_counters(diagnostic_session)
            self._record_selection_operation(
                diagnostic_session, command.operation_id, payload, task.id
            )
            self.db.flush()
            return SelectionReceipt(
                session_id=diagnostic_session.id,
                task=self._task_receipt(task, created=True),
                stop=stop,
            )

        try:
            return self._write(operation)
        except IntegrityError as exc:
            raise CandidateUnavailableError(
                "El candidato no cumple las restricciones de persistencia diagnóstica."
            ) from exc

    def _record_selection_operation(
        self,
        diagnostic_session: DiagnosticSession,
        operation_id: UUID,
        payload: dict[str, Any],
        task_id: int | None,
    ) -> None:
        self._record_operation(
            diagnostic_session,
            operation_id,
            kind="select_next_task",
            payload=payload,
        )
        state = self._selection_state(diagnostic_session)
        state["operation_receipts"][str(operation_id)]["task_id"] = task_id
        diagnostic_session.selection_state = state

    def _stop_for_current_state(self, diagnostic_session: DiagnosticSession) -> StopDecision:
        now = self._now()
        context = self._context(diagnostic_session, now=now)
        candidate = select_candidate(
            self._provider_candidates(diagnostic_session.diagnostic_version), context
        )
        return decide_stop(context, candidates_available=candidate is not None)

    def _move_to_review_if_possible(
        self,
        diagnostic_session: DiagnosticSession,
        *,
        now: datetime,
        operation_id: UUID | None,
        reason: str,
    ) -> None:
        status = DiagnosticSessionStatus(diagnostic_session.status)
        if status == DiagnosticSessionStatus.ONBOARDING:
            self._apply_internal_session_action(
                diagnostic_session,
                SessionAction.BEGIN_CALIBRATION,
                now=now,
                reason="onboarding_ended",
                operation_id=operation_id,
            )
            status = DiagnosticSessionStatus(diagnostic_session.status)
        if status == DiagnosticSessionStatus.CALIBRATING:
            self._apply_internal_session_action(
                diagnostic_session,
                SessionAction.BEGIN_ASSESSMENT,
                now=now,
                reason="calibration_ended",
                operation_id=operation_id,
            )
            status = DiagnosticSessionStatus(diagnostic_session.status)
        if reason == CompletionReason.TIME_LIMIT.value and status in {
            DiagnosticSessionStatus.ASSESSING,
            DiagnosticSessionStatus.REVIEWING,
        }:
            self._stop_active_clock(diagnostic_session, now)
            self._apply_internal_session_action(
                diagnostic_session,
                SessionAction.REACH_TIME_LIMIT,
                now=now,
                reason=reason,
                operation_id=operation_id,
            )
            return
        if status == DiagnosticSessionStatus.ASSESSING:
            self._apply_internal_session_action(
                diagnostic_session,
                SessionAction.BEGIN_REVIEW,
                now=now,
                reason=reason,
                operation_id=operation_id,
            )

    def _submission_flags(self, command: ResponseSubmission) -> dict[str, bool]:
        return {
            "abandoned": command.abandoned,
            "out_of_topic": command.out_of_topic,
            "partially_communicative": command.partially_communicative,
        }

    def _resolve_submission(
        self,
        task: DiagnosticTask,
        command: ResponseSubmission,
    ) -> tuple[str | None, str]:
        expected_answer = task.expected_answer or {}
        answer_contract = expected_answer.get(
            "answer_contract",
            LEGACY_TEXT_ANSWER_CONTRACT,
        )
        if answer_contract == LEGACY_TEXT_ANSWER_CONTRACT:
            if command.answer is not None:
                raise InvalidSubmissionContractError("La tarea legacy exige response_text.")
            return command.response_text, LEGACY_TEXT_ANSWER_CONTRACT
        if answer_contract == OPTION_ID_ANSWER_CONTRACT:
            if isinstance(command.answer, SingleChoiceAnswer):
                return command.answer.selected_option_id, OPTION_ID_ANSWER_CONTRACT
            if isinstance(command.answer, NoAnswer):
                return None, OPTION_ID_ANSWER_CONTRACT
            raise InvalidSubmissionContractError("La tarea cerrada exige selected_option_id.")
        if answer_contract == STRUCTURED_TEXT_ANSWER_CONTRACT:
            if isinstance(command.answer, TextAnswer):
                return command.answer.text, STRUCTURED_TEXT_ANSWER_CONTRACT
            if isinstance(command.answer, NoAnswer):
                return None, STRUCTURED_TEXT_ANSWER_CONTRACT
            raise InvalidSubmissionContractError(
                "La tarea textual exige una respuesta estructurada de texto."
            )
        raise InvalidSubmissionContractError(
            "La tarea persistida usa un contrato de respuesta desconocido."
        )

    def _response_matches_submission(
        self,
        response: DiagnosticResponse,
        command: ResponseSubmission,
        *,
        persisted_value: str | None,
        submission_encoding: str,
    ) -> bool:
        flags = (response.rubric or {}).get("submission_flags", {})
        return (
            response.task_id == command.task_id
            and response.submission_id == str(command.submission_id)
            and response.response_text == persisted_value
            and (response.rubric or {}).get(
                "submission_encoding",
                LEGACY_TEXT_ANSWER_CONTRACT,
            )
            == submission_encoding
            and response.response_language == command.response_language
            and response.instruction_state == command.instruction_state
            and list(response.assistance) == list(command.assistance)
            and response.active_seconds == command.active_seconds
            and flags == self._submission_flags(command)
        )

    def _evaluation_from_response(self, response: DiagnosticResponse) -> EvaluationResult:
        return EvaluationResult(
            outcome=_OUTCOME_FROM_DB[DiagnosticOutcome(response.outcome)],
            score=response.score,
            polarity=DiagnosticPolarity(response.polarity),
            evaluator_confidence=response.evaluator_confidence,
            justification=response.justification,
            reason_codes=list(response.reason_codes),
            rubric_snapshot=response.rubric,
        )

    def _response_receipt(
        self, response: DiagnosticResponse, task: DiagnosticTask, *, created: bool
    ) -> ResponseReceipt:
        return ResponseReceipt(
            response_id=response.id,
            task_id=task.id,
            attempt_number=response.attempt_number,
            evaluation_revision=response.evaluation_revision,
            evaluation=self._evaluation_from_response(response),
            task_status=DiagnosticTaskStatus(task.status),
            created=created,
        )

    def submit_response(self, command: ResponseSubmission) -> ResponseReceipt:
        def operation() -> ResponseReceipt:
            diagnostic_session = self._get_session(command.session_id)
            task = self._get_task(command.task_id)
            if task.session_id != diagnostic_session.id:
                raise EvaluationConflictError("La tarea no pertenece a la sesión indicada.")
            persisted_value, submission_encoding = self._resolve_submission(task, command)

            existing = self.db.scalar(
                select(DiagnosticResponse).where(
                    DiagnosticResponse.evaluation_id == str(command.evaluation_id)
                )
            )
            if existing is not None:
                if not self._response_matches_submission(
                    existing,
                    command,
                    persisted_value=persisted_value,
                    submission_encoding=submission_encoding,
                ):
                    raise DiagnosticIdempotencyConflictError(
                        "evaluation_id ya se usó con otra respuesta."
                    )
                return self._response_receipt(existing, task, created=False)

            same_submission = self.db.scalar(
                select(DiagnosticResponse).where(
                    DiagnosticResponse.submission_id == str(command.submission_id),
                    DiagnosticResponse.evaluation_revision == 1,
                )
            )
            if same_submission is not None:
                raise DiagnosticIdempotencyConflictError(
                    "submission_id ya se usó con otra evaluación."
                )
            if (
                DiagnosticSessionStatus(diagnostic_session.status)
                not in _SELECTABLE_SESSION_STATUSES
            ):
                raise InvalidTransitionError("Solo una sesión activa acepta respuestas.")
            now = self._now()
            self._checkpoint_or_adopt_active_clock(diagnostic_session, now)

            base_attempts = self.db.scalars(
                select(DiagnosticResponse).where(
                    DiagnosticResponse.task_id == task.id,
                    DiagnosticResponse.evaluation_revision == 1,
                )
            ).all()
            attempt_number = len(base_attempts) + 1
            if attempt_number > 2:
                raise EvaluationConflictError("La tarea ya agotó sus dos intentos permitidos.")
            if attempt_number > 1 and "retry" not in command.assistance:
                raise EvaluationConflictError(
                    "Todo segundo intento debe registrar la asistencia retry."
                )

            task_status = DiagnosticTaskStatus(task.status)
            if task_status == DiagnosticTaskStatus.NOT_UNDERSTOOD:
                transition = task_transition(task_status, TaskAction.RETRY)
                task.status = transition.target
                self._append_task_transition(
                    diagnostic_session,
                    task,
                    previous=task_status,
                    target=transition.target,
                    action=TaskAction.RETRY,
                    reason="instruction_retry",
                    at=now,
                )
                task_status = transition.target
            elif task_status == DiagnosticTaskStatus.EVALUATED:
                transition = task_transition(task_status, TaskAction.RETRY)
                task.status = transition.target
                self._append_task_transition(
                    diagnostic_session,
                    task,
                    previous=task_status,
                    target=transition.target,
                    action=TaskAction.RETRY,
                    reason="explicit_retry",
                    at=now,
                )
                task_status = transition.target
            elif task_status != DiagnosticTaskStatus.PRESENTED:
                raise InvalidTransitionError(
                    f"La tarea en estado {task_status.value} no acepta respuestas."
                )

            candidate = self._candidate_from_task(task)
            evaluation = evaluate_submission(candidate, command, attempt_number=attempt_number)
            rubric_snapshot = deepcopy(evaluation.rubric_snapshot)
            rubric_snapshot["submission_flags"] = self._submission_flags(command)
            rubric_snapshot["submission_encoding"] = submission_encoding

            response = DiagnosticResponse(
                evaluation_id=str(command.evaluation_id),
                submission_id=str(command.submission_id),
                task_id=task.id,
                attempt_number=attempt_number,
                evaluation_revision=1,
                response_text=persisted_value,
                response_language=command.response_language,
                instruction_state=command.instruction_state,
                assistance=list(command.assistance),
                active_seconds=command.active_seconds,
                submitted_at=now,
                outcome=_OUTCOME_TO_DB[evaluation.outcome],
                score=evaluation.score,
                rubric=rubric_snapshot,
                polarity=evaluation.polarity,
                evaluator_confidence=evaluation.evaluator_confidence,
                evaluator_type="deterministic",
                evaluator_version=DIAGNOSTIC_ENGINE_VERSION,
                diagnostic_version=diagnostic_session.diagnostic_version,
                task_version=task.template_version,
                justification=evaluation.justification,
                reason_codes=list(evaluation.reason_codes),
            )
            self.db.add(response)
            self.db.flush()
            self._after_response_flush(response)

            self._finalise_task_after_response(
                diagnostic_session,
                task,
                command=command,
                evaluation=evaluation,
                attempt_number=attempt_number,
                now=now,
            )
            self._refresh_counters(diagnostic_session)
            if (
                DiagnosticSessionStatus(diagnostic_session.status)
                == DiagnosticSessionStatus.CALIBRATING
                and diagnostic_session.tasks_evaluable >= 2
            ):
                self._apply_internal_session_action(
                    diagnostic_session,
                    SessionAction.BEGIN_ASSESSMENT,
                    now=now,
                    reason="two_evaluable_calibration_anchors",
                    operation_id=command.evaluation_id,
                )
            self.db.flush()
            return self._response_receipt(response, task, created=True)

        try:
            return self._write(operation)
        except IntegrityError as exc:
            raise EvaluationConflictError(
                "La respuesta coincide con un intento o evaluación ya persistidos."
            ) from exc

    def _after_response_flush(self, _response: DiagnosticResponse) -> None:
        """No-op fault-injection seam used to verify atomic rollback."""

    def _finalise_task_after_response(
        self,
        diagnostic_session: DiagnosticSession,
        task: DiagnosticTask,
        *,
        command: ResponseSubmission,
        evaluation: EvaluationResult,
        attempt_number: int,
        now: datetime,
    ) -> None:
        current = DiagnosticTaskStatus(task.status)
        if command.abandoned:
            action = TaskAction.ABANDON
        elif command.instruction_state == "not_understood" and attempt_number == 1:
            action = TaskAction.MARK_NOT_UNDERSTOOD
        elif evaluation.outcome == EvaluationOutcome.NOT_EVALUABLE and (
            attempt_number >= 2 or not (command.response_text or "").strip()
        ):
            if attempt_number >= 2:
                action = TaskAction.SKIP
            else:
                return
        else:
            answered = task_transition(current, TaskAction.ANSWER)
            task.status = answered.target
            task.answered_at = now
            self._append_task_transition(
                diagnostic_session,
                task,
                previous=current,
                target=answered.target,
                action=TaskAction.ANSWER,
                reason="response_submitted",
                at=now,
            )
            evaluated = task_transition(answered.target, TaskAction.EVALUATE)
            task.status = evaluated.target
            self._append_task_transition(
                diagnostic_session,
                task,
                previous=answered.target,
                target=evaluated.target,
                action=TaskAction.EVALUATE,
                reason=evaluation.reason_codes[0] if evaluation.reason_codes else "evaluated",
                at=now,
            )
            return

        transition = task_transition(current, action)
        task.status = transition.target
        self._append_task_transition(
            diagnostic_session,
            task,
            previous=current,
            target=transition.target,
            action=action,
            reason=evaluation.reason_codes[0] if evaluation.reason_codes else action.value,
            at=now,
        )

    def record_evaluation(self, command: CorrectEvaluationCommand) -> ResponseReceipt:
        def operation() -> ResponseReceipt:
            diagnostic_session = self._get_session(command.session_id)
            original = self.db.get(DiagnosticResponse, command.response_id)
            if original is None:
                raise DiagnosticNotFoundError("No existe la evaluación que se quiere corregir.")
            task = self._get_task(original.task_id)
            if task.session_id != diagnostic_session.id:
                raise EvaluationConflictError("La evaluación pertenece a otra sesión.")

            existing = self.db.scalar(
                select(DiagnosticResponse).where(
                    DiagnosticResponse.evaluation_id == str(command.evaluation_id)
                )
            )
            if existing is not None:
                if not self._correction_matches(existing, command):
                    raise DiagnosticIdempotencyConflictError(
                        "evaluation_id ya se usó para otra corrección."
                    )
                return self._response_receipt(existing, task, created=False)

            if original.correction is not None:
                raise EvaluationConflictError(
                    "La evaluación indicada ya fue sustituida por una corrección."
                )
            now = self._now()
            rubric = deepcopy(original.rubric)
            rubric["correction"] = {
                "supersedes_response_id": original.id,
                "engine_version": DIAGNOSTIC_ENGINE_VERSION,
            }
            correction = DiagnosticResponse(
                evaluation_id=str(command.evaluation_id),
                submission_id=original.submission_id,
                task_id=original.task_id,
                attempt_number=original.attempt_number,
                evaluation_revision=original.evaluation_revision + 1,
                response_text=original.response_text,
                response_language=original.response_language,
                instruction_state=original.instruction_state,
                assistance=list(original.assistance),
                active_seconds=original.active_seconds,
                submitted_at=original.submitted_at,
                outcome=_OUTCOME_TO_DB[command.outcome],
                score=command.score,
                rubric=rubric,
                polarity=command.polarity,
                evaluator_confidence=command.evaluator_confidence,
                evaluator_type="deterministic",
                evaluator_version=DIAGNOSTIC_ENGINE_VERSION,
                diagnostic_version=original.diagnostic_version,
                task_version=original.task_version,
                justification=command.justification,
                reason_codes=list(command.reason_codes),
                supersedes_response_id=original.id,
                created_at=now,
            )
            self.db.add(correction)
            self.db.flush()
            self._refresh_counters(diagnostic_session)
            return self._response_receipt(correction, task, created=True)

        try:
            return self._write(operation)
        except IntegrityError as exc:
            raise EvaluationConflictError(
                "La revisión no respeta el historial append-only."
            ) from exc

    def _correction_matches(
        self, response: DiagnosticResponse, command: CorrectEvaluationCommand
    ) -> bool:
        return (
            response.supersedes_response_id == command.response_id
            and DiagnosticOutcome(response.outcome) == _OUTCOME_TO_DB[command.outcome]
            and response.score == command.score
            and DiagnosticPolarity(response.polarity) == command.polarity
            and response.evaluator_confidence == command.evaluator_confidence
            and response.justification == command.justification
            and list(response.reason_codes) == list(command.reason_codes)
        )

    def _aggregate_values(self, session_id: int) -> list[AxisAggregate]:
        return aggregate_by_axis_and_skill(self._evidence(session_id))

    def _effective_results(
        self, session_id: int
    ) -> dict[tuple[DiagnosticAxis, int | None], DiagnosticResult]:
        rows = self.db.scalars(
            select(DiagnosticResult)
            .where(DiagnosticResult.session_id == session_id)
            .order_by(DiagnosticResult.axis, DiagnosticResult.result_revision, DiagnosticResult.id)
        ).all()
        superseded = {
            row.supersedes_result_id for row in rows if row.supersedes_result_id is not None
        }
        effective: dict[tuple[DiagnosticAxis, int | None], DiagnosticResult] = {}
        for row in rows:
            if row.id in superseded:
                continue
            dimension = (DiagnosticAxis(row.axis), row.skill_id)
            prior = effective.get(dimension)
            if prior is not None:
                raise EvaluationConflictError(
                    "Existen varias cadenas de resultado activas para la misma dimensión."
                )
            effective[dimension] = row
        return effective

    def _result_values(self, aggregate: AxisAggregate) -> dict[str, Any]:
        band = aggregate.band or DiagnosticBand.INSUFFICIENT_EVIDENCE
        return {
            "skill_id": aggregate.skill_id,
            "band": band,
            "cefr_band": aggregate.cefr_band,
            "estimated_score": aggregate.estimated_score,
            "estimate_confidence": aggregate.estimate_confidence,
            "confidence_label": aggregate.confidence_label,
            "positive_evidence_count": aggregate.positive_evidence_count,
            "negative_evidence_count": aggregate.negative_evidence_count,
            "insufficient_evidence_count": aggregate.insufficient_evidence_count,
            "task_types": [task_type.value for task_type in aggregate.task_types],
            "difficulty_min": aggregate.difficulty_min,
            "difficulty_max": aggregate.difficulty_max,
            "strengths": (aggregate.reason if aggregate.positive_evidence_count else ""),
            "limitations": (
                aggregate.reason
                if aggregate.negative_evidence_count
                or aggregate.coverage_status == CoverageStatus.INSUFFICIENT
                else ""
            ),
            "recommendation": "Recoger más evidencias independientes antes de proyectar.",
            "projection_status": "not_projected",
            "result_version": DIAGNOSTIC_ENGINE_VERSION,
        }

    def _result_matches(self, result: DiagnosticResult, values: dict[str, Any]) -> bool:
        return all(getattr(result, key) == value for key, value in values.items())

    def _persist_aggregates(
        self, diagnostic_session: DiagnosticSession, aggregates: Sequence[AxisAggregate]
    ) -> tuple[list[int], int]:
        current = self._effective_results(diagnostic_session.id)
        persistable: dict[tuple[DiagnosticAxis, int | None], AxisAggregate] = {}
        for aggregate in aggregates:
            if aggregate.coverage_status == CoverageStatus.NOT_ASSESSED:
                continue
            dimension = (aggregate.axis, aggregate.skill_id)
            if dimension in persistable:
                raise EvaluationConflictError(
                    "El agregado contiene dos valores para la misma dimensión."
                )
            persistable[dimension] = aggregate

        stale_dimensions = set(current) - set(persistable)
        if stale_dimensions:
            raise EvaluationConflictError(
                "Un resultado activo ya no tiene un agregado efectivo correspondiente."
            )

        persisted_ids: list[int] = []
        created = 0
        for dimension, aggregate in persistable.items():
            values = self._result_values(aggregate)
            prior = current.get(dimension)
            if prior is not None and self._result_matches(prior, values):
                persisted_ids.append(prior.id)
                continue
            group_id = (
                prior.result_group_id
                if prior is not None
                else str(
                    uuid5(
                        NAMESPACE_URL,
                        "deutschos:diagnostic-result:"
                        f"{diagnostic_session.id}:{aggregate.axis.value}:"
                        f"{aggregate.skill_id or 'general'}",
                    )
                )
            )
            revision = prior.result_revision + 1 if prior is not None else 1
            result = DiagnosticResult(
                result_id=str(uuid5(UUID(group_id), f"revision:{revision}")),
                result_group_id=group_id,
                result_revision=revision,
                session_id=diagnostic_session.id,
                axis=aggregate.axis,
                modality="text",
                supersedes_result_id=prior.id if prior is not None else None,
                correction_reason="aggregate_changed" if prior is not None else None,
                computed_at=self._now(),
                **values,
            )
            self.db.add(result)
            self.db.flush()
            persisted_ids.append(result.id)
            current[dimension] = result
            created += 1
        return persisted_ids, created

    def aggregate_results(self, query: SessionQuery) -> AggregateReceipt:
        session_id = query.session_id

        def operation() -> AggregateReceipt:
            diagnostic_session = self._get_session(session_id)
            aggregates = self._aggregate_values(session_id)
            persisted_ids, created = self._persist_aggregates(diagnostic_session, aggregates)
            return AggregateReceipt(
                session_id=session_id,
                axes=aggregates,
                persisted_result_ids=persisted_ids,
                created_revisions=created,
            )

        try:
            return self._write(operation)
        except IntegrityError as exc:
            raise EvaluationConflictError(
                "No se pudo anexar una revisión de resultados coherente."
            ) from exc

    def complete_session(self, command: SessionOperation) -> SessionSnapshot:
        payload = {"session_id": command.session_id, "reason": command.reason}

        def operation() -> SessionSnapshot:
            diagnostic_session = self._get_session(command.session_id)
            if self._operation_is_replay(
                diagnostic_session,
                command.operation_id,
                kind="complete",
                payload=payload,
            ):
                return self._snapshot(diagnostic_session)
            if (
                DiagnosticSessionStatus(diagnostic_session.status)
                == DiagnosticSessionStatus.COMPLETED
            ):
                self._record_operation(
                    diagnostic_session,
                    command.operation_id,
                    kind="complete",
                    payload=payload,
                )
                return self._snapshot(diagnostic_session)
            if engine_state(diagnostic_session.status) in {
                EngineSessionState.CREATED,
                EngineSessionState.ABANDONED,
                EngineSessionState.FAILED,
            }:
                raise InvalidTransitionError("La sesión actual no puede completarse.")

            now = self._now()
            self._checkpoint_or_adopt_active_clock(diagnostic_session, now)
            completion_context = self._context(diagnostic_session, now=now)
            next_candidate = select_candidate(
                self._provider_candidates(diagnostic_session.diagnostic_version),
                completion_context,
            )
            completion_stop = decide_stop(
                completion_context, candidates_available=next_candidate is not None
            )
            if not completion_stop.should_stop:
                raise InvalidTransitionError(
                    "La sesión aún no cumple un criterio de parada; puede pausarse o abandonarse."
                )
            current_task = self._current_task(diagnostic_session.id)
            if current_task is not None and completion_stop.reason not in {
                CompletionReason.MAX_TASKS,
                CompletionReason.TIME_LIMIT,
            }:
                raise InvalidTransitionError(
                    "La tarea presentada debe responderse, omitirse o abandonarse antes de cerrar."
                )
            if DiagnosticSessionStatus(diagnostic_session.status) == DiagnosticSessionStatus.PAUSED:
                self._apply_internal_session_action(
                    diagnostic_session,
                    SessionAction.RESUME,
                    now=now,
                    reason="complete_from_pause",
                    operation_id=command.operation_id,
                )
                diagnostic_session.resumed_at = now
                diagnostic_session.paused_from_status = None
            if current_task is not None:
                current_status = DiagnosticTaskStatus(current_task.status)
                action = (
                    TaskAction.INVALIDATE
                    if current_status == DiagnosticTaskStatus.SELECTED
                    else TaskAction.ABANDON
                )
                task_state = task_transition(current_status, action)
                current_task.status = task_state.target
                self._append_task_transition(
                    diagnostic_session,
                    current_task,
                    previous=current_status,
                    target=task_state.target,
                    action=action,
                    reason="session_completed_with_open_task",
                    at=now,
                )
            self._move_to_review_if_possible(
                diagnostic_session,
                now=now,
                operation_id=command.operation_id,
                reason=completion_stop.reason.value,
            )
            status = DiagnosticSessionStatus(diagnostic_session.status)
            if status in {
                DiagnosticSessionStatus.REVIEWING,
                DiagnosticSessionStatus.TIME_LIMITED,
            }:
                self._apply_internal_session_action(
                    diagnostic_session,
                    SessionAction.BEGIN_COMPLETION,
                    now=now,
                    reason=command.reason or "results_ready",
                    operation_id=command.operation_id,
                )
            if (
                DiagnosticSessionStatus(diagnostic_session.status)
                != DiagnosticSessionStatus.COMPLETING
            ):
                raise InvalidTransitionError("No fue posible alcanzar la fase de cierre.")

            aggregates = self._aggregate_values(diagnostic_session.id)
            self._persist_aggregates(diagnostic_session, aggregates)
            self._stop_active_clock(diagnostic_session, now)
            previous = DiagnosticSessionStatus.COMPLETING
            transition = session_transition(previous, SessionAction.COMPLETE)
            diagnostic_session.status = transition.target
            diagnostic_session.completed_at = now
            diagnostic_session.termination_reason = command.reason or completion_stop.reason.value
            state = self._selection_state(diagnostic_session)
            state["completion_reason"] = completion_stop.reason.value
            state["completion_partial"] = completion_stop.partial
            diagnostic_session.selection_state = state
            self._append_session_transition(
                diagnostic_session,
                previous=previous,
                target=transition.target,
                action=SessionAction.COMPLETE,
                reason=diagnostic_session.termination_reason,
                at=now,
                operation_id=command.operation_id,
            )
            self._record_operation(
                diagnostic_session,
                command.operation_id,
                kind="complete",
                payload=payload,
            )
            self.db.flush()
            return self._snapshot(diagnostic_session, aggregates=aggregates, now=now)

        return self._write(operation)

    def _snapshot(
        self,
        diagnostic_session: DiagnosticSession,
        *,
        aggregates: list[AxisAggregate] | None = None,
        now: datetime | None = None,
    ) -> SessionSnapshot:
        instant = now or self._now()
        current = self._current_task(diagnostic_session.id)
        context = self._context(diagnostic_session, now=instant)
        status = DiagnosticSessionStatus(diagnostic_session.status)
        if status == DiagnosticSessionStatus.COMPLETED:
            raw_completion_reason = self._selection_state(diagnostic_session).get(
                "completion_reason", CompletionReason.TARGET_REACHED.value
            )
            try:
                completion_reason = CompletionReason(raw_completion_reason)
            except ValueError:
                completion_reason = CompletionReason.TARGET_REACHED
            stop = StopDecision(
                should_stop=True,
                reason=completion_reason,
                partial=bool(
                    self._selection_state(diagnostic_session).get(
                        "completion_partial", diagnostic_session.tasks_evaluable < 10
                    )
                ),
                detail=diagnostic_session.termination_reason or "completed",
            )
        elif status in {
            DiagnosticSessionStatus.CANCELLED,
            DiagnosticSessionStatus.ABANDONED,
            DiagnosticSessionStatus.ERROR,
        }:
            stop = StopDecision(
                should_stop=True,
                reason=CompletionReason.NO_CANDIDATES,
                partial=True,
                detail=diagnostic_session.termination_reason or status.value,
            )
        else:
            candidate = select_candidate(
                self._provider_candidates(diagnostic_session.diagnostic_version), context
            )
            stop = decide_stop(context, candidates_available=candidate is not None)
        return SessionSnapshot(
            session=self._session_receipt(diagnostic_session, now=instant),
            current_task=self._task_receipt(current, created=False) if current else None,
            aggregates=aggregates or self._aggregate_values(diagnostic_session.id),
            stop=stop,
        )

    def get_session_state(self, query: SessionQuery) -> SessionSnapshot:
        # Read calls can reuse a Session with expire_on_commit=False. Refresh
        # its identity map so another local writer's committed transition is
        # visible even when this service has not performed a mutation.
        self.db.expire_all()
        return self._snapshot(self._get_session(query.session_id))


__all__ = ["DiagnosticEngineService"]
