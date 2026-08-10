import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from llc_api.db.session import make_engine
from llc_api.diagnostic_engine.exceptions import (
    CandidateUnavailableError,
    DiagnosticIdempotencyConflictError,
    EvaluationConflictError,
    InvalidTransitionError,
)
from llc_api.diagnostic_engine.schemas import (
    CorrectEvaluationCommand,
    CreateSessionCommand,
    DeterministicRubric,
    EngineSessionState,
    EvaluationOutcome,
    ResponseSubmission,
    RubricStrategy,
    SelectTaskCommand,
    SessionOperation,
    SessionQuery,
    TaskCandidate,
)
from llc_api.diagnostic_engine.service import DiagnosticEngineService
from llc_api.models import (
    CurriculumSkill,
    DiagnosticAxis,
    DiagnosticPolarity,
    DiagnosticResponse,
    DiagnosticResult,
    DiagnosticSession,
    DiagnosticSessionStatus,
    DiagnosticTask,
    DiagnosticTaskStatus,
    DiagnosticTaskType,
    Skill,
    SkillEvidence,
    StudentSkill,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class MutableClock:
    def __init__(self, value: datetime | None = None) -> None:
        self.value = value or datetime(2026, 7, 13, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: float) -> None:
        self.value += timedelta(**kwargs)


class StaticCandidateProvider:
    def __init__(self, candidates: list[TaskCandidate]) -> None:
        self._candidates = tuple(candidates)

    def candidates(self, *, diagnostic_version: str):
        assert diagnostic_version == "diagnostic-text.v1"
        return self._candidates


def candidate(
    suffix: str,
    *,
    answer: str = "ja",
    axis: DiagnosticAxis = DiagnosticAxis.READING_COMPREHENSION,
    task_type: DiagnosticTaskType = DiagnosticTaskType.BINARY_CHOICE,
    difficulty: int = 1,
    skill_id: int | None = None,
) -> TaskCandidate:
    return TaskCandidate(
        candidate_id=f"test.{suffix}",
        version="1",
        equivalence_key=f"test.eq.{suffix}",
        axis=axis,
        task_type=task_type,
        difficulty=difficulty,
        modality="text",
        content={"instruction": f"Responde {answer}"},
        options=[answer, "nein"],
        expected_answer={"value": answer},
        rubric=DeterministicRubric(
            strategy=RubricStrategy.EXACT_MATCH,
            accepted_answers=[answer],
            partial_answers=["fast"],
        ),
        auto_evaluable=True,
        estimated_seconds=30,
        skill_id=skill_id,
    )


def option_candidate(*, label: str = "Alpha", version: str = "2") -> TaskCandidate:
    return TaskCandidate(
        candidate_id="test.option-id",
        version=version,
        equivalence_key="test.eq.option-id",
        axis=DiagnosticAxis.READING_COMPREHENSION,
        task_type=DiagnosticTaskType.BINARY_CHOICE,
        difficulty=1,
        modality="text",
        content={"instruction": "Selecciona una opción sintética."},
        options=[
            {"id": "opt_k4m2", "label": label},
            {"id": "opt_p7q9", "label": "Beta"},
        ],
        expected_answer={
            "response_type": "single_choice",
            "answer_contract": "option-id.v1",
            "rubric_version": "deterministic-option-id.v1",
        },
        rubric=DeterministicRubric(
            strategy=RubricStrategy.OPTION_ID,
            accepted_option_ids=["opt_k4m2"],
        ),
        auto_evaluable=True,
        estimated_seconds=30,
    )


def run_alembic(database_path: Path, *arguments: str) -> None:
    environment = os.environ.copy()
    environment["LLC_DATABASE_URL"] = f"sqlite:///{database_path}"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(PROJECT_ROOT / "apps/api/alembic.ini"),
            *arguments,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )


@pytest.fixture(scope="module")
def migrated_diagnostic_template(tmp_path_factory):
    database_path = tmp_path_factory.mktemp("diagnostic-engine") / "template.sqlite3"
    run_alembic(database_path, "upgrade", "head")
    return database_path


@pytest.fixture
def diagnostic_database(tmp_path, migrated_diagnostic_template):
    database_path = tmp_path / "diagnostic-engine.sqlite3"
    shutil.copy2(migrated_diagnostic_template, database_path)
    return database_path


@pytest.fixture
def diagnostic_factory(diagnostic_database):
    engine = make_engine(f"sqlite:///{diagnostic_database}")
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield factory
    engine.dispose()


def create_command(request_id: UUID | None = None, **overrides) -> CreateSessionCommand:
    values = {
        "request_id": request_id or uuid4(),
        "curriculum_version": "a0-a1.v1",
    }
    values.update(overrides)
    return CreateSessionCommand.model_validate(values)


def operation(session_id: int, **overrides) -> SessionOperation:
    values = {"session_id": session_id, "operation_id": uuid4()}
    values.update(overrides)
    return SessionOperation.model_validate(values)


def query(session_id: int) -> SessionQuery:
    return SessionQuery(session_id=session_id)


def create_started_session(service: DiagnosticEngineService) -> int:
    created = service.create_session(create_command())
    started = service.start_session(operation(created.session_id))
    assert started.state == EngineSessionState.ACTIVE
    return created.session_id


def present_task(service: DiagnosticEngineService, session_id: int):
    selected = service.select_next_task(
        SelectTaskCommand(session_id=session_id, operation_id=uuid4())
    )
    assert selected.task is not None
    assert selected.task.status == DiagnosticTaskStatus.PRESENTED
    return selected.task


def submission(session_id: int, task_id: int, **overrides) -> ResponseSubmission:
    values = {
        "session_id": session_id,
        "task_id": task_id,
        "evaluation_id": uuid4(),
        "submission_id": uuid4(),
        "response_text": "ja",
        "response_language": "de",
        "instruction_state": "understood",
        "active_seconds": 12,
    }
    values.update(overrides)
    return ResponseSubmission.model_validate(values)


def test_create_state_idempotency_pause_time_and_restart(diagnostic_database, diagnostic_factory):
    clock = MutableClock()
    provider = StaticCandidateProvider([candidate("state")])
    request_id = uuid4()
    command = create_command(request_id)

    with diagnostic_factory() as db:
        service = DiagnosticEngineService(db, provider, clock=clock)
        created = service.create_session(command)
        replay = service.create_session(command)
        assert replay.session_id == created.session_id
        assert created.state == EngineSessionState.CREATED
        assert db.scalar(select(func.count(DiagnosticSession.id))) == 1

        with pytest.raises(DiagnosticIdempotencyConflictError):
            service.create_session(create_command(request_id, instruction_language="de"))

        clock.advance(hours=1)
        start_id = uuid4()
        started = service.start_session(
            SessionOperation(session_id=created.session_id, operation_id=start_id)
        )
        assert started.active_seconds == 0
        assert (
            service.start_session(
                SessionOperation(session_id=created.session_id, operation_id=start_id)
            ).state
            == EngineSessionState.ACTIVE
        )
        with pytest.raises(DiagnosticIdempotencyConflictError):
            service.start_session(
                SessionOperation(
                    session_id=created.session_id,
                    operation_id=start_id,
                    reason="different-payload",
                )
            )

        clock.advance(minutes=2)
        pause_id = uuid4()
        paused = service.pause_session(
            SessionOperation(session_id=created.session_id, operation_id=pause_id)
        )
        assert paused.state == EngineSessionState.PAUSED
        assert paused.active_seconds == 120
        assert (
            service.pause_session(
                SessionOperation(session_id=created.session_id, operation_id=pause_id)
            ).active_seconds
            == 120
        )

    clock.advance(hours=8)
    restarted_engine = make_engine(f"sqlite:///{diagnostic_database}")
    restarted_factory = sessionmaker(bind=restarted_engine, expire_on_commit=False, autoflush=False)
    try:
        with restarted_factory() as db:
            service = DiagnosticEngineService(db, provider, clock=clock)
            persisted = service.get_session_state(query(created.session_id))
            assert persisted.session.state == EngineSessionState.PAUSED
            assert persisted.session.active_seconds == 120

            resumed = service.resume_session(operation(created.session_id))
            assert resumed.state == EngineSessionState.ACTIVE
            clock.advance(seconds=30)
            assert (
                service.get_session_state(query(created.session_id)).session.active_seconds == 150
            )
            paused_again = service.pause_session(operation(created.session_id))
            assert paused_again.active_seconds == 150
            assert paused_again.paused_at is not None
            assert paused_again.paused_at.tzinfo is UTC
    finally:
        restarted_engine.dispose()


def test_active_session_does_not_count_application_downtime(diagnostic_factory):
    clock = MutableClock()
    provider = StaticCandidateProvider([candidate("process-restart")])
    with diagnostic_factory() as db:
        first_process = DiagnosticEngineService(
            db,
            provider,
            clock=clock,
            process_instance_id="process-before-restart",
        )
        session_id = create_started_session(first_process)
        task = present_task(first_process, session_id)
        clock.advance(minutes=2)
        # A repeated selection checkpoints the elapsed active segment.
        first_process.select_next_task(
            SelectTaskCommand(session_id=session_id, operation_id=uuid4())
        )
        assert first_process.get_session_state(query(session_id)).session.active_seconds == 120

    clock.advance(hours=8)
    with diagnostic_factory() as db:
        restarted_process = DiagnosticEngineService(
            db,
            provider,
            clock=clock,
            process_instance_id="process-after-restart",
        )
        state = restarted_process.get_session_state(query(session_id))
        assert state.session.state == EngineSessionState.ACTIVE
        assert state.session.active_seconds == 120
        replay = restarted_process.select_next_task(
            SelectTaskCommand(session_id=session_id, operation_id=uuid4())
        )
        assert replay.task is not None
        assert replay.task.task_id == task.task_id
        paused = restarted_process.pause_session(operation(session_id))
        assert paused.active_seconds == 120


def test_selection_retries_return_one_persisted_task(diagnostic_factory):
    provider = StaticCandidateProvider([candidate("selection")])
    with diagnostic_factory() as db:
        service = DiagnosticEngineService(db, provider, clock=MutableClock())
        session_id = create_started_session(service)
        command = SelectTaskCommand(session_id=session_id, operation_id=uuid4())

        first = service.select_next_task(command)
        exact_replay = service.select_next_task(command)
        pending_replay = service.select_next_task(
            SelectTaskCommand(session_id=session_id, operation_id=uuid4())
        )

        assert first.task is not None
        assert exact_replay.task is not None
        assert pending_replay.task is not None
        assert first.task.created is True
        assert exact_replay.task.created is False
        assert pending_replay.task.created is False
        assert {first.task.task_id, exact_replay.task.task_id, pending_replay.task.task_id} == {
            first.task.task_id
        }
        assert db.scalar(select(func.count(DiagnosticTask.id))) == 1
        persisted_session = db.get(DiagnosticSession, session_id)
        assert persisted_session is not None
        assert persisted_session.tasks_presented == 1


def test_completion_cannot_treat_an_open_task_as_candidate_exhaustion(
    diagnostic_factory,
):
    provider = StaticCandidateProvider([candidate("open-task")])
    with diagnostic_factory() as db:
        service = DiagnosticEngineService(db, provider, clock=MutableClock())
        session_id = create_started_session(service)
        present_task(service, session_id)

        with pytest.raises(InvalidTransitionError, match="tarea presentada"):
            service.complete_session(operation(session_id))

        persisted = db.get(DiagnosticSession, session_id)
        assert persisted is not None
        assert persisted.status == DiagnosticSessionStatus.CALIBRATING


def test_time_limit_can_close_an_open_task_as_an_explicit_partial(diagnostic_factory):
    clock = MutableClock()
    provider = StaticCandidateProvider([candidate("time-limit")])
    with diagnostic_factory() as db:
        service = DiagnosticEngineService(db, provider, clock=clock)
        session_id = create_started_session(service)
        task = present_task(service, session_id)
        clock.advance(seconds=1500)

        completed = service.complete_session(operation(session_id))
        assert completed.session.state == EngineSessionState.COMPLETED
        assert completed.stop.reason.value == "time_limit"
        assert completed.stop.partial is True
        persisted_task = db.get(DiagnosticTask, task.task_id)
        assert persisted_task is not None
        assert persisted_task.status == DiagnosticTaskStatus.ABANDONED


def test_candidate_skill_must_belong_to_the_session_curriculum(diagnostic_factory):
    with diagnostic_factory() as db:
        curriculum_skill_ids = select(CurriculumSkill.skill_id).where(
            CurriculumSkill.curriculum_version == "a0-a1.v1"
        )
        legacy_skill = db.scalar(
            select(Skill).where(Skill.id.not_in(curriculum_skill_ids)).limit(1)
        )
        assert legacy_skill is not None
        provider = StaticCandidateProvider([candidate("foreign-skill", skill_id=legacy_skill.id)])
        service = DiagnosticEngineService(db, provider, clock=MutableClock())
        session_id = create_started_session(service)

        with pytest.raises(CandidateUnavailableError, match="no pertenece al currículo"):
            service.select_next_task(SelectTaskCommand(session_id=session_id, operation_id=uuid4()))
        assert db.scalar(select(func.count(DiagnosticTask.id))) == 0


def test_technical_failure_is_recoverable_but_terminal_sessions_are_not(
    diagnostic_factory,
):
    provider = StaticCandidateProvider([candidate("failure-recovery")])
    with diagnostic_factory() as db:
        service = DiagnosticEngineService(db, provider, clock=MutableClock())
        session_id = create_started_session(service)

        failed = service.fail_session(operation(session_id, reason="injected_technical_failure"))
        assert failed.state == EngineSessionState.FAILED
        recovered = service.resume_session(operation(session_id))
        assert recovered.state == EngineSessionState.ACTIVE
        assert recovered.termination_reason is None

        abandoned = service.abandon_session(operation(session_id))
        assert abandoned.state == EngineSessionState.ABANDONED
        with pytest.raises(InvalidTransitionError, match="Transición inválida"):
            service.resume_session(operation(session_id))


@pytest.mark.parametrize(
    ("response_overrides", "expected_outcome", "expected_score", "expected_evaluable"),
    [
        ({}, EvaluationOutcome.CORRECT_WITHOUT_HELP, 1.0, 1),
        (
            {"assistance": ["lexical_hint"]},
            EvaluationOutcome.CORRECT_WITH_HELP,
            0.7,
            1,
        ),
        (
            {"response_text": "hola", "response_language": "es"},
            EvaluationOutcome.NOT_EVALUABLE,
            None,
            0,
        ),
        (
            {"response_text": "morgen", "out_of_topic": True},
            EvaluationOutcome.INCORRECT,
            0.0,
            1,
        ),
        (
            {"response_text": "fast", "partially_communicative": True},
            EvaluationOutcome.PARTIAL,
            0.4,
            1,
        ),
    ],
)
def test_deterministic_submission_cases_are_atomic_and_idempotent(
    diagnostic_factory,
    response_overrides,
    expected_outcome,
    expected_score,
    expected_evaluable,
):
    provider = StaticCandidateProvider([candidate("responses")])
    with diagnostic_factory() as db:
        service = DiagnosticEngineService(db, provider, clock=MutableClock())
        session_id = create_started_session(service)
        task = present_task(service, session_id)
        command = submission(session_id, task.task_id, **response_overrides)

        receipt = service.submit_response(command)
        service.pause_session(operation(session_id))
        replay = service.submit_response(command)

        assert receipt.created is True
        assert replay.created is False
        assert replay.response_id == receipt.response_id
        assert receipt.evaluation.outcome == expected_outcome
        assert receipt.evaluation.score == expected_score
        assert db.scalar(select(func.count(DiagnosticResponse.id))) == 1
        persisted_session = db.get(DiagnosticSession, session_id)
        assert persisted_session is not None
        assert persisted_session.tasks_evaluable == expected_evaluable

        with pytest.raises(DiagnosticIdempotencyConflictError):
            service.submit_response(command.model_copy(update={"response_text": "changed"}))


def test_empty_response_twice_is_skipped_and_remains_insufficient(diagnostic_factory):
    provider = StaticCandidateProvider([candidate("empty")])
    with diagnostic_factory() as db:
        service = DiagnosticEngineService(db, provider, clock=MutableClock())
        session_id = create_started_session(service)
        task = present_task(service, session_id)

        first = service.submit_response(
            submission(
                session_id,
                task.task_id,
                response_text=None,
                response_language=None,
            )
        )
        with pytest.raises(EvaluationConflictError, match="asistencia retry"):
            service.submit_response(
                submission(
                    session_id,
                    task.task_id,
                    response_text="ja",
                    response_language="de",
                )
            )
        second = service.submit_response(
            submission(
                session_id,
                task.task_id,
                response_text="",
                response_language=None,
                assistance=["retry"],
            )
        )

        assert first.evaluation.outcome == EvaluationOutcome.NOT_EVALUABLE
        assert first.task_status == DiagnosticTaskStatus.PRESENTED
        assert second.attempt_number == 2
        assert second.evaluation.outcome == EvaluationOutcome.NOT_EVALUABLE
        assert second.task_status == DiagnosticTaskStatus.SKIPPED
        assert db.scalar(select(func.count(DiagnosticResponse.id))) == 2
        persisted_session = db.get(DiagnosticSession, session_id)
        assert persisted_session is not None
        assert persisted_session.tasks_evaluable == 0


def test_option_id_snapshot_survives_restart_label_changes_and_corrections(
    diagnostic_factory,
):
    original_provider = StaticCandidateProvider([option_candidate(label="Alpha")])
    with diagnostic_factory() as db:
        service = DiagnosticEngineService(db, original_provider, clock=MutableClock())
        session_id = create_started_session(service)
        task = present_task(service, session_id)
        task_id = task.task_id

    changed_provider = StaticCandidateProvider(
        [option_candidate(label="Etiqueta cambiada", version="3")]
    )
    evaluation_id = uuid4()
    submission_id = uuid4()
    with diagnostic_factory() as db:
        service = DiagnosticEngineService(db, changed_provider, clock=MutableClock())
        command = submission(
            session_id,
            task_id,
            evaluation_id=evaluation_id,
            submission_id=submission_id,
            response_text=None,
            response_language=None,
            answer={"kind": "single_choice", "selected_option_id": "opt_k4m2"},
        )
        receipt = service.submit_response(command)
        replay = service.submit_response(command)

        assert receipt.evaluation.outcome == EvaluationOutcome.CORRECT_WITHOUT_HELP
        assert replay.created is False
        persisted_task = db.get(DiagnosticTask, task_id)
        persisted_response = db.get(DiagnosticResponse, receipt.response_id)
        assert persisted_task is not None
        assert persisted_task.options[0] == {"id": "opt_k4m2", "label": "Alpha"}
        assert persisted_task.template_version == "2"
        assert persisted_response is not None
        assert persisted_response.response_text == "opt_k4m2"
        assert persisted_response.rubric["submission_encoding"] == "option-id.v1"

        with pytest.raises(DiagnosticIdempotencyConflictError):
            service.submit_response(
                ResponseSubmission.model_validate(
                    {
                        **command.model_dump(),
                        "answer": {
                            "kind": "single_choice",
                            "selected_option_id": "opt_p7q9",
                        },
                    }
                )
            )
        with pytest.raises(DiagnosticIdempotencyConflictError):
            service.submit_response(
                submission(
                    session_id,
                    task_id,
                    evaluation_id=uuid4(),
                    submission_id=submission_id,
                    response_text=None,
                    response_language=None,
                    answer={"kind": "single_choice", "selected_option_id": "opt_p7q9"},
                )
            )

        correction = service.record_evaluation(
            CorrectEvaluationCommand(
                session_id=session_id,
                response_id=receipt.response_id,
                evaluation_id=uuid4(),
                outcome=EvaluationOutcome.INCORRECT,
                score=0.0,
                polarity=DiagnosticPolarity.NEGATIVE,
                evaluator_confidence=1.0,
                justification="Corrección sintética v2.",
                reason_codes=["synthetic_v2_correction"],
            )
        )
        corrected = db.get(DiagnosticResponse, correction.response_id)
        assert corrected is not None
        assert corrected.response_text == "opt_k4m2"
        assert corrected.rubric["submission_encoding"] == "option-id.v1"
        assert corrected.supersedes_response_id == receipt.response_id

    legacy_provider = StaticCandidateProvider([candidate("legacy-coexistence")])
    with diagnostic_factory() as db:
        service = DiagnosticEngineService(db, legacy_provider, clock=MutableClock())
        legacy_session_id = create_started_session(service)
        legacy_task = present_task(service, legacy_session_id)
        legacy = service.submit_response(submission(legacy_session_id, legacy_task.task_id))
        legacy_response = db.get(DiagnosticResponse, legacy.response_id)
        assert legacy_response is not None
        assert legacy_response.response_text == "ja"
        assert legacy_response.rubric["submission_encoding"] == "legacy-text.v1"


def test_not_understood_can_retry_once_without_fabricating_grammar_evidence(
    diagnostic_factory,
):
    provider = StaticCandidateProvider([candidate("clarification")])
    with diagnostic_factory() as db:
        service = DiagnosticEngineService(db, provider, clock=MutableClock())
        session_id = create_started_session(service)
        task = present_task(service, session_id)

        first = service.submit_response(
            submission(
                session_id,
                task.task_id,
                response_text=None,
                response_language=None,
                instruction_state="not_understood",
            )
        )
        second = service.submit_response(
            submission(
                session_id,
                task.task_id,
                assistance=["clarification", "retry"],
            )
        )

        assert first.evaluation.outcome == EvaluationOutcome.NOT_EVALUABLE
        assert first.task_status == DiagnosticTaskStatus.NOT_UNDERSTOOD
        assert second.attempt_number == 2
        assert second.evaluation.outcome == EvaluationOutcome.CORRECT_WITH_HELP
        assert second.task_status == DiagnosticTaskStatus.EVALUATED

        with pytest.raises(EvaluationConflictError, match="dos intentos"):
            service.submit_response(
                submission(
                    session_id,
                    task.task_id,
                    assistance=["retry"],
                )
            )


def test_corrections_aggregation_completion_and_learning_ledger_isolation(diagnostic_factory):
    provider = StaticCandidateProvider(
        [
            candidate("aggregate.one"),
            candidate(
                "aggregate.two",
                task_type=DiagnosticTaskType.GAP_FILL,
            ),
        ]
    )
    with diagnostic_factory() as db:
        service = DiagnosticEngineService(db, provider, clock=MutableClock())
        before_student_skills = db.scalar(select(func.count(StudentSkill.id)))
        before_skill_evidence = db.scalar(select(func.count(SkillEvidence.id)))
        session_id = create_started_session(service)

        first_task = present_task(service, session_id)
        first_response = service.submit_response(submission(session_id, first_task.task_id))
        persisted_session = db.get(DiagnosticSession, session_id)
        assert persisted_session is not None
        assert persisted_session.status == DiagnosticSessionStatus.CALIBRATING
        second_task = present_task(service, session_id)
        service.submit_response(submission(session_id, second_task.task_id))
        persisted_session = db.get(DiagnosticSession, session_id)
        assert persisted_session is not None
        assert persisted_session.status == DiagnosticSessionStatus.ASSESSING

        first_aggregate = service.aggregate_results(query(session_id))
        replayed_aggregate = service.aggregate_results(query(session_id))
        reading = next(
            item
            for item in first_aggregate.axes
            if item.axis == DiagnosticAxis.READING_COMPREHENSION
        )
        oral = next(
            item for item in first_aggregate.axes if item.axis == DiagnosticAxis.ORAL_PRODUCTION
        )
        assert reading.evidence_count == 2
        assert reading.estimated_score == 1.0
        assert reading.estimate_confidence >= 0.6
        assert oral.evidence_count == 0
        assert oral.estimated_score is None
        assert first_aggregate.created_revisions == 1
        assert replayed_aggregate.created_revisions == 0

        correction_command = CorrectEvaluationCommand(
            session_id=session_id,
            response_id=first_response.response_id,
            evaluation_id=uuid4(),
            outcome=EvaluationOutcome.INCORRECT,
            score=0.0,
            polarity=DiagnosticPolarity.NEGATIVE,
            evaluator_confidence=1.0,
            justification="La clave almacenada era incorrecta.",
            reason_codes=["manual_correction"],
        )
        correction = service.record_evaluation(correction_command)
        correction_replay = service.record_evaluation(correction_command)
        assert correction.created is True
        assert correction.evaluation_revision == 2
        assert correction_replay.created is False
        assert db.scalar(select(func.count(DiagnosticResponse.id))) == 3

        changed_aggregate = service.aggregate_results(query(session_id))
        assert changed_aggregate.created_revisions == 1
        assert db.scalar(select(func.count(DiagnosticResult.id))) == 2

        completion_command = operation(session_id, reason="service-test-complete")
        completed = service.complete_session(completion_command)
        completion_replay = service.complete_session(completion_command)
        assert completed.session.state == EngineSessionState.COMPLETED
        assert completed.session.completed_at is not None
        assert completed.stop.reason.value == "no_candidates"
        assert completed.stop.partial is True
        assert completion_replay.session.state == EngineSessionState.COMPLETED
        assert completion_replay.stop.reason.value == "no_candidates"
        assert db.scalar(select(func.count(DiagnosticResult.id))) == 2

        rows = db.scalars(
            select(DiagnosticResponse)
            .where(DiagnosticResponse.task_id == first_task.task_id)
            .order_by(DiagnosticResponse.evaluation_revision)
        ).all()
        assert [row.score for row in rows] == [1.0, 0.0]
        assert rows[1].supersedes_response_id == rows[0].id
        assert db.scalar(select(func.count(StudentSkill.id))) == before_student_skills
        assert db.scalar(select(func.count(SkillEvidence.id))) == before_skill_evidence


def test_result_revisions_keep_axis_and_curriculum_dimension_append_only(
    diagnostic_factory,
):
    with diagnostic_factory() as db:
        a0_skill_id = db.scalar(
            select(CurriculumSkill.skill_id)
            .where(
                CurriculumSkill.curriculum_version == "a0-a1.v1",
                CurriculumSkill.cefr_reference == "A0",
            )
            .order_by(CurriculumSkill.curriculum_order)
            .limit(1)
        )
        a1_skill_id = db.scalar(
            select(CurriculumSkill.skill_id)
            .where(
                CurriculumSkill.curriculum_version == "a0-a1.v1",
                CurriculumSkill.cefr_reference == "A1",
            )
            .order_by(CurriculumSkill.curriculum_order)
            .limit(1)
        )
        assert a0_skill_id is not None
        assert a1_skill_id is not None
        provider = StaticCandidateProvider(
            [
                candidate("dimension.a1", skill_id=a0_skill_id),
                candidate(
                    "dimension.a2",
                    skill_id=a0_skill_id,
                    task_type=DiagnosticTaskType.GAP_FILL,
                ),
                candidate(
                    "dimension.b",
                    skill_id=a1_skill_id,
                    task_type=DiagnosticTaskType.SENTENCE_CORRECTION,
                ),
            ]
        )
        service = DiagnosticEngineService(db, provider, clock=MutableClock())
        session_id = create_started_session(service)
        responses = []
        for _ in range(2):
            task = present_task(service, session_id)
            responses.append(service.submit_response(submission(session_id, task.task_id)))

        first = service.aggregate_results(query(session_id))
        assert first.created_revisions == 1
        first_reading = next(
            item for item in first.axes if item.axis == DiagnosticAxis.READING_COMPREHENSION
        )
        assert first_reading.skill_id == a0_skill_id
        assert first_reading.cefr_band == "pre-A1"
        assert db.scalar(select(func.count(DiagnosticResult.id))) == 1

        third = present_task(service, session_id)
        service.submit_response(submission(session_id, third.task_id))
        mixed = service.aggregate_results(query(session_id))
        assert mixed.created_revisions == 1
        assert db.scalar(select(func.count(DiagnosticResult.id))) == 2
        dimensions = set(
            db.execute(
                select(DiagnosticResult.axis, DiagnosticResult.skill_id).where(
                    DiagnosticResult.session_id == session_id
                )
            ).all()
        )
        assert dimensions == {
            (DiagnosticAxis.READING_COMPREHENSION, a0_skill_id),
            (DiagnosticAxis.READING_COMPREHENSION, a1_skill_id),
        }

        service.record_evaluation(
            CorrectEvaluationCommand(
                session_id=session_id,
                response_id=responses[0].response_id,
                evaluation_id=uuid4(),
                outcome=EvaluationOutcome.INCORRECT,
                score=0.0,
                polarity=DiagnosticPolarity.NEGATIVE,
                evaluator_confidence=1.0,
                justification="Corrección posterior de la primera evidencia.",
                reason_codes=["manual_correction"],
            )
        )
        revised = service.aggregate_results(query(session_id))
        assert revised.created_revisions == 1
        replayed_revision = service.aggregate_results(query(session_id))
        assert replayed_revision.created_revisions == 0
        results = db.scalars(
            select(DiagnosticResult)
            .where(DiagnosticResult.session_id == session_id)
            .order_by(DiagnosticResult.skill_id, DiagnosticResult.result_revision)
        ).all()
        assert len(results) == 3
        a0_results = [row for row in results if row.skill_id == a0_skill_id]
        a1_results = [row for row in results if row.skill_id == a1_skill_id]
        assert [row.result_revision for row in a0_results] == [1, 2]
        assert a0_results[1].supersedes_result_id == a0_results[0].id
        assert a0_results[1].positive_evidence_count == 1
        assert a0_results[1].negative_evidence_count == 1
        assert [row.result_revision for row in a1_results] == [1]
        assert a1_results[0].positive_evidence_count == 1
        superseded_ids = {
            row.supersedes_result_id for row in results if row.supersedes_result_id is not None
        }
        effective_results = [row for row in results if row.id not in superseded_ids]
        assert {(DiagnosticAxis(row.axis), row.skill_id) for row in effective_results} == {
            (DiagnosticAxis.READING_COMPREHENSION, a0_skill_id),
            (DiagnosticAxis.READING_COMPREHENSION, a1_skill_id),
        }

        stop = service.select_next_task(
            SelectTaskCommand(session_id=session_id, operation_id=uuid4())
        )
        assert stop.stop.should_stop is True
        completed = service.complete_session(operation(session_id))
        assert completed.session.state == EngineSessionState.COMPLETED


def test_not_evaluable_evidence_keeps_its_curriculum_result_dimension(
    diagnostic_factory,
):
    with diagnostic_factory() as db:
        skill_id = db.scalar(
            select(CurriculumSkill.skill_id)
            .where(
                CurriculumSkill.curriculum_version == "a0-a1.v1",
                CurriculumSkill.cefr_reference == "A0",
            )
            .order_by(CurriculumSkill.curriculum_order)
            .limit(1)
        )
        assert skill_id is not None
        provider = StaticCandidateProvider([candidate("dimension.insufficient", skill_id=skill_id)])
        service = DiagnosticEngineService(db, provider, clock=MutableClock())
        session_id = create_started_session(service)
        task = present_task(service, session_id)
        evaluated = service.submit_response(
            submission(
                session_id,
                task.task_id,
                response_text="sí",
                response_language="es",
            )
        )
        assert evaluated.evaluation.outcome == EvaluationOutcome.NOT_EVALUABLE

        receipt = service.aggregate_results(query(session_id))
        reading = next(
            item for item in receipt.axes if item.axis == DiagnosticAxis.READING_COMPREHENSION
        )
        assert receipt.created_revisions == 1
        assert reading.skill_id == skill_id
        assert reading.evidence_count == 0
        assert reading.insufficient_evidence_count == 1
        assert reading.estimated_score is None
        persisted = db.scalar(
            select(DiagnosticResult).where(DiagnosticResult.session_id == session_id)
        )
        assert persisted is not None
        assert persisted.skill_id == skill_id
        assert persisted.estimated_score is None


class FaultingDiagnosticService(DiagnosticEngineService):
    def _after_response_flush(self, _response: DiagnosticResponse) -> None:
        raise RuntimeError("injected failure after response flush")


def test_failure_after_flush_rolls_back_response_task_and_counters(diagnostic_factory):
    provider = StaticCandidateProvider([candidate("rollback")])
    clock = MutableClock()
    with diagnostic_factory() as db:
        normal_service = DiagnosticEngineService(db, provider, clock=clock)
        session_id = create_started_session(normal_service)
        task = present_task(normal_service, session_id)
        faulting_service = FaultingDiagnosticService(db, provider, clock=clock)

        with pytest.raises(RuntimeError, match="injected failure"):
            faulting_service.submit_response(submission(session_id, task.task_id))
        assert db.in_transaction() is False

        assert db.scalar(select(func.count(DiagnosticResponse.id))) == 0
        persisted_task = db.get(DiagnosticTask, task.task_id)
        persisted_session = db.get(DiagnosticSession, session_id)
        assert persisted_task is not None
        assert persisted_task.status == DiagnosticTaskStatus.PRESENTED
        assert persisted_session is not None
        assert persisted_session.tasks_presented == 1
        assert persisted_session.tasks_evaluable == 0


def test_concurrent_identical_submission_creates_one_response(
    diagnostic_database,
    diagnostic_factory,
):
    provider = StaticCandidateProvider([candidate("concurrent")])
    clock = MutableClock()
    with diagnostic_factory() as db:
        service = DiagnosticEngineService(db, provider, clock=clock)
        session_id = create_started_session(service)
        task = present_task(service, session_id)
    command = submission(session_id, task.task_id)
    barrier = Barrier(2)

    def submit_concurrently():
        engine = make_engine(f"sqlite:///{diagnostic_database}")
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        try:
            with factory() as db:
                service = DiagnosticEngineService(db, provider, clock=clock)
                barrier.wait(timeout=10)
                receipt = service.submit_response(command)
                assert db.in_transaction() is False
                return receipt
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = [
            future.result(timeout=15)
            for future in [executor.submit(submit_concurrently) for _ in range(2)]
        ]

    assert {receipt.response_id for receipt in receipts} == {receipts[0].response_id}
    assert sorted(receipt.created for receipt in receipts) == [False, True]
    with diagnostic_factory() as db:
        assert db.scalar(select(func.count(DiagnosticResponse.id))) == 1
        persisted_session = db.get(DiagnosticSession, session_id)
        assert persisted_session is not None
        assert persisted_session.tasks_evaluable == 1


def test_reused_session_refreshes_stale_state_after_writer_lock(diagnostic_factory):
    provider = StaticCandidateProvider([candidate("stale-session")])
    with diagnostic_factory() as first_db, diagnostic_factory() as second_db:
        first = DiagnosticEngineService(first_db, provider, clock=MutableClock())
        session_id = create_started_session(first)
        # Keep the active entity in the first identity map.
        assert first.get_session_state(query(session_id)).session.state == EngineSessionState.ACTIVE

        second = DiagnosticEngineService(second_db, provider, clock=MutableClock())
        assert second.pause_session(operation(session_id)).state == EngineSessionState.PAUSED
        assert first.get_session_state(query(session_id)).session.state == EngineSessionState.PAUSED

        with pytest.raises(InvalidTransitionError, match="sesión activa"):
            first.select_next_task(SelectTaskCommand(session_id=session_id, operation_id=uuid4()))
        assert first_db.scalar(select(func.count(DiagnosticTask.id))) == 0
