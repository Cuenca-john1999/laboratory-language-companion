from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from deutschos_api.core.time import utc_now
from deutschos_api.learning_engine.curriculum import CURRICULUM_VERSION
from deutschos_api.learning_engine.schemas import AttemptCreate
from deutschos_api.learning_engine.scoring import (
    ENGINE_VERSION,
    AttemptOutcome,
    calculate_mastery_update,
)
from deutschos_api.learning_engine.service import (
    LearningEngineBusyError,
    _priority_categories,
    create_daily_plan,
    record_attempt,
)
from deutschos_api.models import (
    DailyPlan,
    LearningSession,
    SkillEvidence,
    StudentProfile,
    StudentSkill,
)

pytestmark = pytest.mark.anyio


def attempt_payload(submission_id=None, **overrides):
    payload = {
        "submission_id": str(submission_id or uuid4()),
        "skill_code": "grammar.personal_pronouns",
        "source": "manual_assessment",
        "exercise_type": "recognition",
        "prompt": "Selecciona el pronombre para Anna.",
        "student_answer": "sie",
        "expected_answer": "sie",
        "outcome": "correct_without_help",
        "feedback": "Correcto.",
        "corrects_submission_id": None,
    }
    payload.update(overrides)
    return payload


def test_profile_priority_aliases_are_understood():
    profile = StudentProfile(
        id=1,
        preferred_name="Jhon",
        native_language="español",
        learning_preferences=('{"priority": ["listening", "spontaneous_speech", "grammar"]}'),
    )
    assert _priority_categories(profile) == ("listening", "speaking", "grammar")


async def test_curriculum_and_skills_expose_only_versioned_a0_a1_data(client):
    curriculum = await client.get("/api/learning/curriculum")
    assert curriculum.status_code == 200
    body = curriculum.json()
    assert body["version"] == CURRICULUM_VERSION
    assert len(body["skills"]) == 14
    assert {skill["cefr_hint"] for skill in body["skills"]} == {"pre-A1", "A1"}
    assert body["skills"][0]["prerequisite_codes"] == []

    skills = await client.get("/api/learning/skills")
    assert skills.status_code == 200
    rows = skills.json()
    assert len(rows) == 14
    first = rows[0]
    assert first["state"] is None
    assert first["eligible_for_introduction"] is True
    assert "estimated_mastery" not in first
    assert all(row["curriculum_version"] == CURRICULUM_VERSION for row in rows)


async def test_today_is_404_until_a_plan_is_persisted(client):
    response = await client.get("/api/learning/today")
    assert response.status_code == 404
    assert "hoy" in response.json()["detail"]


async def test_ten_minute_plan_is_created_persisted_and_returned_as_today(
    client, db_session_factory
):
    response = await client.post(
        "/api/learning/daily-plan",
        json={"available_minutes": 10, "motivation": 2},
    )
    assert response.status_code == 201
    plan = response.json()
    assert plan["engine_version"] == ENGINE_VERSION
    assert plan["curriculum_version"] == CURRICULUM_VERSION
    assert plan["duration_minutes"] == 10
    assert plan["intensity"] == "low"
    assert sum(block["duration_minutes"] for block in plan["blocks"]) == 10
    assert len([block for block in plan["blocks"] if block["kind"] == "new_skill"]) <= 1
    assert "ten_minute_session_supported" in plan["reason_codes"]

    today = await client.get("/api/learning/today")
    assert today.status_code == 200
    assert today.json() == plan
    with db_session_factory() as db:
        assert db.scalar(select(func.count(DailyPlan.id))) == 1


async def test_attempt_uses_categorical_outcome_and_is_atomic_and_idempotent(
    client, db_session_factory
):
    submission_id = uuid4()
    payload = attempt_payload(submission_id)
    first = await client.post("/api/learning/attempts", json=payload)
    assert first.status_code == 201
    receipt = first.json()
    assert receipt["created"] is True
    assert receipt["outcome"] == "correct_without_help"
    assert receipt["derived_score"] == 1.0
    assert receipt["state"]["estimated_mastery"] == 1.0
    assert receipt["state"]["confidence"] == 0.1
    assert receipt["state"]["evidence_count"] == 1
    assert receipt["state"]["internally_mastered"] is False

    retry = await client.post("/api/learning/attempts", json=payload)
    assert retry.status_code == 200
    assert retry.json()["created"] is False
    assert retry.json()["evidence_id"] == receipt["evidence_id"]
    with db_session_factory() as db:
        assert db.scalar(select(func.count(SkillEvidence.id))) == 1
        assert db.scalar(select(func.count(LearningSession.id))) == 1


async def test_idempotency_key_rejects_changed_outcome(client):
    submission_id = uuid4()
    first = await client.post("/api/learning/attempts", json=attempt_payload(submission_id))
    assert first.status_code == 201
    conflict = await client.post(
        "/api/learning/attempts",
        json=attempt_payload(submission_id, outcome="failure"),
    )
    assert conflict.status_code == 409


async def test_numeric_score_is_rejected_and_does_not_write(client, db_session_factory):
    response = await client.post(
        "/api/learning/attempts",
        json={**attempt_payload(), "score": 0.99},
    )
    assert response.status_code == 422
    with db_session_factory() as db:
        assert db.scalar(select(func.count(SkillEvidence.id))) == 0
        assert db.scalar(select(func.count(LearningSession.id))) == 0


async def test_incompatible_exercise_type_is_rejected_before_persistence(
    client, db_session_factory
):
    response = await client.post(
        "/api/learning/attempts",
        json=attempt_payload(exercise_type="spoken_response"),
    )
    assert response.status_code == 422
    with db_session_factory() as db:
        assert db.scalar(select(func.count(SkillEvidence.id))) == 0


async def test_unknown_skill_and_unknown_correction_are_404(client):
    unknown_skill = await client.post(
        "/api/learning/attempts",
        json=attempt_payload(skill_code="grammar.not_in_curriculum"),
    )
    assert unknown_skill.status_code == 404
    unknown_evidence = await client.post(
        "/api/learning/attempts",
        json=attempt_payload(corrects_submission_id=str(uuid4())),
    )
    assert unknown_evidence.status_code == 404


async def test_correction_is_append_only_and_recomputes_effective_state(client, db_session_factory):
    original_id = uuid4()
    original = await client.post(
        "/api/learning/attempts",
        json=attempt_payload(original_id, outcome="failure"),
    )
    assert original.status_code == 201
    assert original.json()["state"]["estimated_mastery"] == 0
    assert original.json()["state"]["evidence_count"] == 1

    correction_id = uuid4()
    correction_payload = attempt_payload(
        correction_id,
        corrects_submission_id=str(original_id),
        outcome="correct_without_help",
        feedback="Valoración manual corregida.",
    )
    corrected = await client.post("/api/learning/attempts", json=correction_payload)
    assert corrected.status_code == 201
    receipt = corrected.json()
    assert receipt["corrected_evidence_id"] == original.json()["evidence_id"]
    assert receipt["state"]["estimated_mastery"] == 1
    assert receipt["state"]["evidence_count"] == 1
    assert receipt["state"]["unassisted_streak"] == 1

    retry = await client.post("/api/learning/attempts", json=correction_payload)
    assert retry.status_code == 200
    second_correction = await client.post(
        "/api/learning/attempts",
        json=attempt_payload(
            corrects_submission_id=str(original_id),
            outcome="partial",
        ),
    )
    assert second_correction.status_code == 409

    with db_session_factory() as db:
        evidence = db.scalars(select(SkillEvidence).order_by(SkillEvidence.id)).all()
        assert len(evidence) == 2
        assert evidence[0].outcome == "failure"
        assert evidence[1].supersedes_evidence_id == evidence[0].id
        assert evidence[1].evidence_count_before == 1
        assert evidence[1].evidence_count_after == 1
        state = db.scalar(select(StudentSkill))
        assert state is not None
        assert state.evidence_count == 1
        assert state.last_evidence_id == evidence[1].id


async def test_due_reviews_are_ranked_and_require_no_model_provider(client, db_session_factory):
    recorded = await client.post("/api/learning/attempts", json=attempt_payload())
    assert recorded.status_code == 201
    before_due = await client.get("/api/learning/reviews")
    assert before_due.status_code == 200
    assert before_due.json()["total_due"] == 0

    with db_session_factory() as db:
        state = db.scalar(select(StudentSkill))
        assert state is not None
        state.next_review_at = utc_now() - timedelta(minutes=1)
        db.commit()

    due = await client.get("/api/learning/reviews")
    assert due.status_code == 200
    body = due.json()
    assert body["total_due"] == 1
    assert body["items"][0]["skill_code"] == "grammar.personal_pronouns"
    assert body["items"][0]["overdue"] is True


def test_concurrent_distinct_submissions_are_serialized_without_lost_updates(
    file_db_session_factory,
):
    outcomes = (
        "failure",
        "partial",
        "correct_with_help",
        "correct_without_help",
        "partial",
        "correct_without_help",
        "correct_with_help",
        "correct_without_help",
    )
    barrier = Barrier(len(outcomes))

    def submit(index: int, outcome: str):
        payload = AttemptCreate.model_validate(
            attempt_payload(
                prompt=f"Intento concurrente {index}",
                outcome=outcome,
            )
        )
        with file_db_session_factory() as db:
            barrier.wait(timeout=10)
            receipt = record_attempt(db, payload)
            assert db.in_transaction() is False
            return receipt

    with ThreadPoolExecutor(max_workers=len(outcomes)) as executor:
        futures = [
            executor.submit(submit, index, outcome) for index, outcome in enumerate(outcomes)
        ]
        receipts = [future.result(timeout=15) for future in futures]

    assert len({receipt.evidence_id for receipt in receipts}) == len(outcomes)
    with file_db_session_factory() as db:
        evidence = db.scalars(
            select(SkillEvidence).order_by(SkillEvidence.created_at, SkillEvidence.id)
        ).all()
        state = db.scalar(select(StudentSkill))
        assert state is not None
        assert len(evidence) == len(outcomes)
        assert state.evidence_count == len(evidence)

        expected_mastery = 0.0
        expected_count = 0
        expected_streak = 0
        expected_lapses = 0
        expected = None
        for item in evidence:
            expected = calculate_mastery_update(
                current_mastery=expected_mastery,
                evidence_count=expected_count,
                outcome=AttemptOutcome(item.outcome),
                current_unassisted_streak=expected_streak,
                current_lapse_count=expected_lapses,
            )
            expected_mastery = expected.estimated_mastery
            expected_count = expected.evidence_count
            expected_streak = expected.unassisted_streak
            expected_lapses = expected.lapse_count

        assert expected is not None
        assert state.estimated_mastery == expected.estimated_mastery
        assert state.confidence == expected.confidence
        assert state.last_outcome == expected.last_outcome.value
        assert state.unassisted_streak == expected.unassisted_streak
        assert state.lapse_count == expected.lapse_count


def test_locked_database_returns_retryable_service_error_for_mutations(
    file_db_session_factory,
):
    holder = file_db_session_factory()
    try:
        holder.execute(text("BEGIN IMMEDIATE"))

        with file_db_session_factory() as attempt_db:
            attempt_db.execute(text("PRAGMA busy_timeout=20"))
            with pytest.raises(LearningEngineBusyError, match="ocupada"):
                record_attempt(
                    attempt_db,
                    AttemptCreate.model_validate(attempt_payload()),
                )
            assert attempt_db.in_transaction() is False

        with file_db_session_factory() as plan_db:
            plan_db.execute(text("PRAGMA busy_timeout=20"))
            with pytest.raises(LearningEngineBusyError, match="ocupada"):
                create_daily_plan(
                    plan_db,
                    available_minutes=10,
                    motivation=3,
                    timezone_name="Europe/Berlin",
                )
            assert plan_db.in_transaction() is False
    finally:
        holder.rollback()
        holder.close()
