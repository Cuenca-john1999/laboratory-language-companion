import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from deutschos_api.db.session import make_engine
from deutschos_api.models import (
    DiagnosticAxis,
    DiagnosticBand,
    DiagnosticConfidenceLabel,
    DiagnosticOutcome,
    DiagnosticPolarity,
    DiagnosticResponse,
    DiagnosticResult,
    DiagnosticSession,
    DiagnosticSessionStatus,
    DiagnosticTask,
    DiagnosticTaskStatus,
    DiagnosticTaskType,
    SkillEvidence,
    StudentSkill,
)
from deutschos_api.schemas.diagnostic import (
    DiagnosticResponseCreate,
    DiagnosticResponseRead,
    DiagnosticResultCreate,
    DiagnosticSessionCreate,
    DiagnosticSessionRead,
    DiagnosticSessionStateWrite,
    DiagnosticTaskCreate,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def run_alembic(database_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DEUTSCHOS_DATABASE_URL"] = f"sqlite:///{database_path}"
    return subprocess.run(
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


@pytest.fixture
def diagnostic_database(tmp_path):
    database_path = tmp_path / "diagnostic.sqlite3"
    run_alembic(database_path, "upgrade", "head")
    return database_path


@pytest.fixture
def diagnostic_factory(diagnostic_database):
    engine = make_engine(f"sqlite:///{diagnostic_database}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def add_session(db, *, status=DiagnosticSessionStatus.NOT_STARTED):
    now = datetime.now(UTC)
    session = DiagnosticSession(
        profile_id=1,
        diagnostic_version="diagnostic-text.v1",
        persistence_version="diagnostic-persistence-v1",
        curriculum_version="a0-a1.v1",
        status=status,
        selection_state={},
        random_seed=str(uuid4()),
        instruction_language="es",
        target_task_count=14,
        max_task_count=20,
        target_duration_seconds=1200,
        max_duration_seconds=1500,
        active_seconds=0,
        tasks_presented=0,
        tasks_evaluable=0,
        paused_at=now if status == DiagnosticSessionStatus.PAUSED else None,
    )
    db.add(session)
    db.flush()
    return session


def add_task(db, session, *, status=DiagnosticTaskStatus.PRESENTED, sequence=1):
    now = datetime.now(UTC)
    task = DiagnosticTask(
        session_id=session.id,
        sequence=sequence,
        status=status,
        template_id="diagnostic.binary-choice",
        template_version="1",
        task_type=DiagnosticTaskType.BINARY_CHOICE,
        primary_axis=DiagnosticAxis.ACTIVE_GRAMMAR,
        secondary_axes=[],
        difficulty=1,
        modality="text",
        selection_reason="calibration",
        content={"instruction": "Elige una forma"},
        options=["bin", "bist"],
        expected_answer={"value": "bin"},
        rubric={},
        origin="bank",
        selected_at=now,
        presented_at=now if status != DiagnosticTaskStatus.SELECTED else None,
    )
    db.add(task)
    db.flush()
    return task


def add_response(db, task, **overrides):
    values = {
        "evaluation_id": str(uuid4()),
        "submission_id": str(uuid4()),
        "task_id": task.id,
        "attempt_number": 1,
        "evaluation_revision": 1,
        "response_text": "Ich bin Jhon.",
        "response_language": "de",
        "instruction_state": "understood",
        "assistance": [],
        "active_seconds": 12,
        "submitted_at": datetime.now(UTC),
        "outcome": DiagnosticOutcome.CORRECT_WITHOUT_HELP,
        "score": 1.0,
        "rubric": {"target": "correct"},
        "polarity": DiagnosticPolarity.POSITIVE,
        "evaluator_confidence": 1.0,
        "evaluator_type": "deterministic",
        "evaluator_version": "exact-key-v1",
        "diagnostic_version": "diagnostic-text.v1",
        "task_version": "1",
        "justification": "Coincide con la clave.",
        "reason_codes": ["exact_match"],
    }
    values.update(overrides)
    response = DiagnosticResponse(**values)
    db.add(response)
    db.flush()
    return response


def add_result(db, session, **overrides):
    values = {
        "result_id": str(uuid4()),
        "result_group_id": str(uuid4()),
        "result_revision": 1,
        "session_id": session.id,
        "axis": DiagnosticAxis.ACTIVE_GRAMMAR,
        "modality": "text",
        "band": DiagnosticBand.DEVELOPING,
        "estimated_score": 0.6,
        "estimate_confidence": 0.6,
        "confidence_label": DiagnosticConfidenceLabel.MODERATE,
        "positive_evidence_count": 2,
        "negative_evidence_count": 1,
        "insufficient_evidence_count": 0,
        "task_types": [DiagnosticTaskType.BINARY_CHOICE],
        "difficulty_min": 1,
        "difficulty_max": 2,
        "strengths": "Reconoce la forma objetivo.",
        "limitations": "Falta una muestra productiva.",
        "recommendation": "Practicar producción breve.",
        "projection_status": "not_projected",
        "result_version": "diagnostic-result-v1",
        "computed_at": datetime.now(UTC),
    }
    values.update(overrides)
    result = DiagnosticResult(**values)
    db.add(result)
    db.flush()
    return result


def test_upgrade_from_existing_0004_preserves_all_existing_rows(tmp_path):
    database_path = tmp_path / "existing-0004.sqlite3"
    run_alembic(database_path, "upgrade", "0004")
    with sqlite3.connect(database_path) as connection:
        before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("student_profiles", "skills", "curricula", "curriculum_skills")
        }

    run_alembic(database_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        after = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
        assert after == before
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0006",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)


def test_session_pause_resume_and_utc_timestamps_persist_after_restart(
    diagnostic_database, diagnostic_factory
):
    paused_at = datetime.now(UTC).replace(microsecond=0)
    with diagnostic_factory() as db:
        session = add_session(db)
        session.status = DiagnosticSessionStatus.PAUSED
        session.paused_from_status = DiagnosticSessionStatus.ASSESSING
        session.paused_at = paused_at
        session.active_seconds = 321
        db.commit()
        session_id = session.id

    engine = make_engine(f"sqlite:///{diagnostic_database}")
    restarted_factory = sessionmaker(bind=engine, expire_on_commit=False)
    resumed_at = paused_at + timedelta(minutes=10)
    with restarted_factory() as db:
        persisted = db.get(DiagnosticSession, session_id)
        assert persisted is not None
        assert persisted.status == DiagnosticSessionStatus.PAUSED
        assert persisted.paused_from_status == DiagnosticSessionStatus.ASSESSING
        assert persisted.paused_at == paused_at
        assert persisted.paused_at.tzinfo is UTC
        persisted.status = DiagnosticSessionStatus.ASSESSING
        persisted.resumed_at = resumed_at
        db.commit()

    with restarted_factory() as db:
        persisted = db.get(DiagnosticSession, session_id)
        assert persisted.status == DiagnosticSessionStatus.ASSESSING
        assert persisted.resumed_at == resumed_at
        assert persisted.resumed_at.tzinfo is UTC
        assert DiagnosticSessionRead.model_validate(persisted).status == "assessing"
    engine.dispose()


@pytest.mark.parametrize("status", ["unknown", "running", "finished"])
def test_invalid_session_states_are_rejected(diagnostic_factory, status):
    with diagnostic_factory() as db:
        session = add_session(db)
        session.status = status
        with pytest.raises(IntegrityError):
            db.commit()


def test_paused_completed_and_abandoned_states_require_their_timestamp(diagnostic_factory):
    for status in ("paused", "completed", "abandoned"):
        with diagnostic_factory() as db:
            session = add_session(db)
            session.status = status
            with pytest.raises(IntegrityError):
                db.commit()


@pytest.mark.parametrize(
    "field,value", [("score", -0.01), ("score", 1.01), ("evaluator_confidence", 1.01)]
)
def test_response_values_must_be_between_zero_and_one(diagnostic_factory, field, value):
    with diagnostic_factory() as db:
        session = add_session(db)
        task = add_task(db, session)
        with pytest.raises(IntegrityError):
            add_response(db, task, **{field: value})


@pytest.mark.parametrize("value", [-0.01, 1.01])
def test_result_values_must_be_between_zero_and_one(diagnostic_factory, value):
    with diagnostic_factory() as db:
        session = add_session(db)
        with pytest.raises(IntegrityError):
            add_result(db, session, estimate_confidence=value)


def test_not_evaluable_response_has_no_score_and_insufficient_polarity(diagnostic_factory):
    with diagnostic_factory() as db:
        session = add_session(db)
        task = add_task(db, session)
        response = add_response(
            db,
            task,
            response_text=None,
            response_language=None,
            outcome=DiagnosticOutcome.NOT_EVALUABLE,
            score=None,
            polarity=DiagnosticPolarity.INSUFFICIENT,
            evaluator_confidence=0.0,
        )
        db.commit()
        assert response.score is None
        assert response.outcome == DiagnosticOutcome.NOT_EVALUABLE


def test_response_evaluations_and_corrections_are_append_only(diagnostic_factory):
    with diagnostic_factory() as db:
        session = add_session(db)
        task = add_task(db, session)
        original = add_response(db, task, outcome=DiagnosticOutcome.PARTIAL, score=0.4)
        db.commit()

        correction = add_response(
            db,
            task,
            evaluation_id=str(uuid4()),
            submission_id=original.submission_id,
            evaluation_revision=2,
            response_text=original.response_text,
            response_language=original.response_language,
            instruction_state=original.instruction_state,
            assistance=original.assistance,
            active_seconds=original.active_seconds,
            submitted_at=original.submitted_at,
            outcome=DiagnosticOutcome.CORRECT_WITHOUT_HELP,
            score=1.0,
            polarity=DiagnosticPolarity.POSITIVE,
            evaluator_confidence=1.0,
            diagnostic_version=original.diagnostic_version,
            task_version=original.task_version,
            supersedes_response_id=original.id,
        )
        db.commit()

        rows = db.scalars(
            select(DiagnosticResponse).order_by(DiagnosticResponse.evaluation_revision)
        ).all()
        assert [row.score for row in rows] == [0.4, 1.0]
        assert correction.supersedes_response_id == original.id
        assert original.correction.id == correction.id

        original.score = 0.2
        with pytest.raises(IntegrityError, match="immutable"):
            db.commit()
        db.rollback()

        db.delete(original)
        with pytest.raises(IntegrityError, match="immutable"):
            db.commit()


def test_response_correction_cannot_change_submitted_answer_or_branch(diagnostic_factory):
    with diagnostic_factory() as db:
        session = add_session(db)
        task = add_task(db, session)
        original = add_response(db, task)
        db.commit()

        with pytest.raises(IntegrityError, match="preserve the submitted response"):
            add_response(
                db,
                task,
                submission_id=original.submission_id,
                evaluation_revision=2,
                response_text="Eine andere Antwort",
                submitted_at=original.submitted_at,
                supersedes_response_id=original.id,
            )
        db.rollback()

        first_correction = add_response(
            db,
            task,
            submission_id=original.submission_id,
            evaluation_revision=2,
            response_text=original.response_text,
            response_language=original.response_language,
            instruction_state=original.instruction_state,
            assistance=original.assistance,
            active_seconds=original.active_seconds,
            submitted_at=original.submitted_at,
            diagnostic_version=original.diagnostic_version,
            task_version=original.task_version,
            supersedes_response_id=original.id,
        )
        db.commit()
        assert first_correction.id

        with pytest.raises(IntegrityError):
            add_response(
                db,
                task,
                submission_id=original.submission_id,
                evaluation_revision=2,
                response_text=original.response_text,
                response_language=original.response_language,
                instruction_state=original.instruction_state,
                assistance=original.assistance,
                active_seconds=original.active_seconds,
                submitted_at=original.submitted_at,
                diagnostic_version=original.diagnostic_version,
                task_version=original.task_version,
                supersedes_response_id=original.id,
            )


def test_results_and_result_corrections_are_append_only(diagnostic_factory):
    with diagnostic_factory() as db:
        session = add_session(db)
        original = add_result(db, session)
        db.commit()

        correction = add_result(
            db,
            session,
            result_group_id=original.result_group_id,
            result_revision=2,
            estimated_score=0.7,
            correction_reason="Revisión manual de la rúbrica",
            supersedes_result_id=original.id,
        )
        db.commit()
        assert correction.supersedes_result_id == original.id
        assert db.scalar(select(func.count(DiagnosticResult.id))) == 2

        original.estimated_score = 0.1
        with pytest.raises(IntegrityError, match="immutable"):
            db.commit()
        db.rollback()

        db.delete(original)
        with pytest.raises(IntegrityError, match="immutable"):
            db.commit()


def test_result_correction_cannot_change_axis(diagnostic_factory):
    with diagnostic_factory() as db:
        session = add_session(db)
        original = add_result(db, session)
        db.commit()
        with pytest.raises(IntegrityError, match="preserve its diagnostic dimension"):
            add_result(
                db,
                session,
                result_group_id=original.result_group_id,
                result_revision=2,
                axis=DiagnosticAxis.WRITTEN_PRODUCTION,
                supersedes_result_id=original.id,
            )


def test_foreign_keys_and_deletion_policy_are_enforced(diagnostic_factory):
    with diagnostic_factory() as db:
        db.add(
            DiagnosticTask(
                session_id=9999,
                sequence=1,
                status="selected",
                template_id="invalid",
                template_version="1",
                task_type="binary_choice",
                primary_axis="active_grammar",
                secondary_axes=[],
                difficulty=1,
                modality="text",
                selection_reason="test",
                content={},
                options=[],
                rubric={},
                origin="bank",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()

    with diagnostic_factory() as db:
        disposable = add_session(db)
        selected_task = add_task(db, disposable, status=DiagnosticTaskStatus.SELECTED)
        disposable_id = disposable.id
        selected_task_id = selected_task.id
        db.commit()
        db.delete(disposable)
        db.commit()
        assert db.get(DiagnosticSession, disposable_id) is None
        assert (
            db.scalar(
                select(func.count(DiagnosticTask.id)).where(DiagnosticTask.id == selected_task_id)
            )
            == 0
        )

    with diagnostic_factory() as db:
        preserved = add_session(db)
        presented_task = add_task(db, preserved)
        add_response(db, presented_task)
        db.commit()
        db.delete(preserved)
        with pytest.raises(IntegrityError):
            db.commit()


def test_presented_task_content_and_selected_state_cannot_be_rewritten(diagnostic_factory):
    with diagnostic_factory() as db:
        session = add_session(db)
        task = add_task(db, session)
        db.commit()

        task.content = {"instruction": "Contenido sustituido"}
        with pytest.raises(IntegrityError, match="content is immutable"):
            db.commit()
        db.rollback()

        task = db.get(DiagnosticTask, task.id)
        task.status = DiagnosticTaskStatus.SELECTED
        with pytest.raises(IntegrityError, match="cannot return to selected"):
            db.commit()


def test_diagnostic_records_never_project_to_learning_state(diagnostic_factory):
    with diagnostic_factory() as db:
        before_student_skills = db.scalar(select(func.count(StudentSkill.id)))
        before_evidence = db.scalar(select(func.count(SkillEvidence.id)))
        session = add_session(db)
        task = add_task(db, session)
        add_response(db, task)
        add_result(db, session)
        db.commit()
        assert db.scalar(select(func.count(StudentSkill.id))) == before_student_skills
        assert db.scalar(select(func.count(SkillEvidence.id))) == before_evidence


def test_strict_pydantic_schemas_reject_extra_fields_and_invalid_values():
    session_payload = {
        "diagnostic_version": "diagnostic-text.v1",
        "curriculum_version": "a0-a1.v1",
        "random_seed": "fixed-seed",
    }
    session = DiagnosticSessionCreate.model_validate(session_payload)
    assert session.target_task_count == 14
    assert session.max_task_count == 20

    with pytest.raises(ValidationError, match="extra_forbidden"):
        DiagnosticSessionCreate.model_validate({**session_payload, "invented": True})
    with pytest.raises(ValidationError):
        DiagnosticSessionCreate.model_validate({**session_payload, "target_task_count": 17})
    with pytest.raises(ValidationError):
        DiagnosticSessionStateWrite.model_validate(
            {
                "status": "paused",
                "active_seconds": 0,
                "tasks_presented": 0,
                "tasks_evaluable": 0,
            }
        )

    with pytest.raises(ValidationError):
        DiagnosticResponseCreate.model_validate(
            {
                "evaluation_id": str(uuid4()),
                "submission_id": str(uuid4()),
                "task_id": 1,
                "attempt_number": 1,
                "response_text": "Antwort",
                "outcome": "not_evaluable",
                "score": 0.5,
                "polarity": "insufficient",
                "evaluator_confidence": 0.5,
                "evaluator_type": "deterministic",
                "evaluator_version": "v1",
                "diagnostic_version": "v1",
                "task_version": "v1",
            }
        )


def test_pydantic_task_and_result_constraints_cover_text_only_and_cefr_evidence():
    with pytest.raises(ValidationError):
        DiagnosticTaskCreate.model_validate(
            {
                "session_id": 1,
                "sequence": 1,
                "template_id": "future-audio",
                "template_version": "1",
                "task_type": "binary_choice",
                "primary_axis": "listening_comprehension",
                "difficulty": 1,
                "selection_reason": "test",
                "content": {},
            }
        )

    with pytest.raises(ValidationError, match="CEFR band"):
        DiagnosticResultCreate.model_validate(
            {
                "result_id": str(uuid4()),
                "result_group_id": str(uuid4()),
                "session_id": 1,
                "axis": "active_grammar",
                "band": "developing",
                "cefr_band": "A1",
                "estimated_score": 0.7,
                "estimate_confidence": 0.5,
                "confidence_label": "low",
                "positive_evidence_count": 1,
                "result_version": "v1",
            }
        )

    with pytest.raises(ValidationError, match="must remain unassessed"):
        DiagnosticResultCreate.model_validate(
            {
                "result_id": str(uuid4()),
                "result_group_id": str(uuid4()),
                "session_id": 1,
                "axis": "oral_production",
                "band": "initial_basis",
                "estimated_score": 0.4,
                "estimate_confidence": 0.6,
                "confidence_label": "moderate",
                "positive_evidence_count": 2,
                "difficulty_min": 1,
                "difficulty_max": 1,
                "result_version": "v1",
            }
        )


def test_sqlite_storage_is_healthy_and_uses_only_text_modality(diagnostic_database):
    with sqlite3.connect(diagnostic_database) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name LIKE 'diagnostic_%'"
            )
        }
        assert tables == {
            "diagnostic_sessions",
            "diagnostic_tasks",
            "diagnostic_responses",
            "diagnostic_results",
        }
        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='trigger' AND name LIKE 'diagnostic_%'"
            )
        }
        assert triggers == {
            "diagnostic_responses_no_delete",
            "diagnostic_responses_no_update",
            "diagnostic_responses_validate_correction",
            "diagnostic_results_no_delete",
            "diagnostic_results_no_update",
            "diagnostic_results_validate_correction",
            "diagnostic_tasks_lock_presented_content",
            "diagnostic_tasks_no_return_to_selected",
            "diagnostic_tasks_preserve_presented",
        }


def test_response_read_schema_returns_aware_utc_timestamps(diagnostic_factory):
    with diagnostic_factory() as db:
        session = add_session(db)
        task = add_task(db, session)
        response = add_response(db, task)
        db.commit()
        parsed = DiagnosticResponseRead.model_validate(response)
        assert parsed.submitted_at.utcoffset() == timedelta(0)
        assert parsed.created_at.utcoffset() == timedelta(0)
