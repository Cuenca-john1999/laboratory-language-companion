import asyncio
import logging
from collections.abc import Sequence
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select

from deutschos_api.api.diagnostic import get_diagnostic_candidate_provider
from deutschos_api.db.session import get_db
from deutschos_api.diagnostic_engine.schemas import (
    DeterministicRubric,
    RubricStrategy,
    TaskCandidate,
)
from deutschos_api.main import app
from deutschos_api.models import (
    DiagnosticAxis,
    DiagnosticResponse,
    DiagnosticResult,
    DiagnosticSession,
    DiagnosticTask,
    DiagnosticTaskType,
    SkillEvidence,
    StudentSkill,
)
from deutschos_api.providers.ollama import OllamaProvider

pytestmark = pytest.mark.anyio


class StaticCandidateProvider:
    def __init__(self, candidates: Sequence[TaskCandidate]) -> None:
        self._candidates = tuple(candidates)

    def candidates(self, *, diagnostic_version: str):
        assert diagnostic_version == "diagnostic-text.v1"
        return self._candidates


def candidate(
    suffix: str,
    *,
    answer: str = "ja",
    task_type: DiagnosticTaskType = DiagnosticTaskType.BINARY_CHOICE,
    content: dict[str, object] | None = None,
) -> TaskCandidate:
    return TaskCandidate(
        candidate_id=f"http.{suffix}",
        version="1",
        equivalence_key=f"http.eq.{suffix}",
        axis=DiagnosticAxis.READING_COMPREHENSION,
        task_type=task_type,
        difficulty=1,
        modality="text",
        content=content
        or {
            "instruction": "Selecciona la respuesta correcta.",
            "prompt": f"Tarea pública {suffix}",
        },
        options=[answer, "nein"],
        expected_answer={"value": answer},
        rubric=DeterministicRubric(
            strategy=RubricStrategy.EXACT_MATCH,
            accepted_answers=[answer],
        ),
        auto_evaluable=True,
        estimated_seconds=30,
    )


def option_id_candidate(suffix: str = "option-v2") -> TaskCandidate:
    return TaskCandidate(
        candidate_id=f"http.{suffix}",
        version="2",
        equivalence_key=f"http.eq.{suffix}",
        axis=DiagnosticAxis.READING_COMPREHENSION,
        task_type=DiagnosticTaskType.BINARY_CHOICE,
        difficulty=1,
        modality="text",
        content={
            "instructions": "Selecciona una opción sintética.",
            "prompt": "Tarea HTTP v2 exclusiva de prueba.",
        },
        options=[
            {"id": "opt_k4m2", "label": "Alpha"},
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


def structured_text_candidate() -> TaskCandidate:
    return TaskCandidate(
        candidate_id="http.text-v2",
        version="2",
        equivalence_key="http.eq.text-v2",
        axis=DiagnosticAxis.WRITTEN_PRODUCTION,
        task_type=DiagnosticTaskType.GAP_FILL,
        difficulty=1,
        modality="text",
        content={
            "instructions": "Escribe la palabra sintética.",
            "prompt": "___",
        },
        options=[],
        expected_answer={
            "response_type": "short_text",
            "answer_contract": "text.v2",
            "rubric_version": "deterministic-text.v1",
        },
        rubric=DeterministicRubric(
            strategy=RubricStrategy.EXACT_MATCH,
            accepted_answers=["alpha"],
        ),
        auto_evaluable=True,
        estimated_seconds=30,
    )


@pytest.fixture
def candidate_provider() -> StaticCandidateProvider:
    return StaticCandidateProvider(
        [
            candidate("one"),
            candidate("two", task_type=DiagnosticTaskType.GAP_FILL),
        ]
    )


@pytest.fixture
async def diagnostic_client(db_session_factory, candidate_provider):
    def override_db():
        with db_session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_diagnostic_candidate_provider] = lambda: candidate_provider
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.pop(get_diagnostic_candidate_provider, None)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
async def diagnostic_file_client(file_db_session_factory, candidate_provider):
    def override_db():
        with file_db_session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_diagnostic_candidate_provider] = lambda: candidate_provider
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.pop(get_diagnostic_candidate_provider, None)
    app.dependency_overrides.pop(get_db, None)


def create_payload(request_id: UUID | None = None, **overrides):
    payload = {
        "request_id": str(request_id or uuid4()),
        "curriculum_version": "a0-a1.v1",
    }
    payload.update(overrides)
    return payload


def operation_payload(operation_id: UUID | None = None, **overrides):
    payload = {"operation_id": str(operation_id or uuid4())}
    payload.update(overrides)
    return payload


def response_payload(task_id: int, **overrides):
    payload = {
        "task_id": task_id,
        "evaluation_id": str(uuid4()),
        "submission_id": str(uuid4()),
        "response_text": "ja",
        "response_language": "de",
        "instruction_state": "understood",
    }
    payload.update(overrides)
    return payload


async def create_started_session(client: httpx.AsyncClient) -> int:
    created = await client.post("/api/diagnostic/sessions", json=create_payload())
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    started = await client.post(
        f"/api/diagnostic/sessions/{session_id}/start",
        json=operation_payload(),
    )
    assert started.status_code == 200
    assert started.json()["state"] == "active"
    return session_id


async def select_task(client: httpx.AsyncClient, session_id: int) -> dict:
    response = await client.post(
        f"/api/diagnostic/sessions/{session_id}/next-task",
        json=operation_payload(),
    )
    assert response.status_code == 200
    task = response.json()["task"]
    assert task is not None
    return task


def recursive_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            nested_key for nested in value.values() for nested_key in recursive_keys(nested)
        }
    if isinstance(value, list):
        return {nested_key for nested in value for nested_key in recursive_keys(nested)}
    return set()


async def test_create_read_and_create_idempotency_are_strict(
    diagnostic_client,
    db_session_factory,
):
    request_id = uuid4()
    payload = create_payload(request_id)
    first = await diagnostic_client.post("/api/diagnostic/sessions", json=payload)
    replay = await diagnostic_client.post("/api/diagnostic/sessions", json=payload)

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["session_id"] == first.json()["session_id"]
    assert first.json()["state"] == "created"
    session_id = first.json()["session_id"]
    read = await diagnostic_client.get(f"/api/diagnostic/sessions/{session_id}")
    assert read.status_code == 200
    assert read.json()["session"]["session_id"] == session_id
    assert read.json()["session"]["phase"] == "not_started"

    conflict = await diagnostic_client.post(
        "/api/diagnostic/sessions",
        json=create_payload(request_id, instruction_language="de"),
    )
    assert conflict.status_code == 409
    invalid = await diagnostic_client.post(
        "/api/diagnostic/sessions",
        json={**create_payload(), "unknown": True},
    )
    assert invalid.status_code == 422
    with db_session_factory() as db:
        assert db.scalar(select(func.count(DiagnosticSession.id))) == 1


async def test_session_transitions_and_reasonable_replays(diagnostic_client):
    session_id = await create_started_session(diagnostic_client)
    pause_id = uuid4()
    paused = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/pause",
        json=operation_payload(pause_id),
    )
    replayed_pause = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/pause",
        json=operation_payload(pause_id),
    )
    assert paused.status_code == 200
    assert replayed_pause.status_code == 200
    assert paused.json()["state"] == replayed_pause.json()["state"] == "paused"

    resumed = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/resume",
        json=operation_payload(),
    )
    failed = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/fail",
        json=operation_payload(reason="controlled_test_failure"),
    )
    recovered = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/resume",
        json=operation_payload(),
    )
    abandoned = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/abandon",
        json=operation_payload(),
    )
    abandoned_replay = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/abandon",
        json=operation_payload(),
    )
    invalid_resume = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/resume",
        json=operation_payload(),
    )

    assert resumed.json()["state"] == "active"
    assert failed.json()["state"] == "failed"
    assert recovered.json()["state"] == "active"
    assert abandoned.json()["state"] == "abandoned"
    assert abandoned_replay.json()["state"] == "abandoned"
    assert invalid_resume.status_code == 409
    assert "traceback" not in invalid_resume.text.casefold()


async def test_invalid_transition_and_missing_resource_use_domain_status_codes(
    diagnostic_client,
):
    created = await diagnostic_client.post(
        "/api/diagnostic/sessions",
        json=create_payload(),
    )
    session_id = created.json()["session_id"]
    invalid = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/pause",
        json=operation_payload(),
    )
    missing = await diagnostic_client.get("/api/diagnostic/sessions/999999")
    invalid_path = await diagnostic_client.get("/api/diagnostic/sessions/0")

    assert invalid.status_code == 409
    assert missing.status_code == 404
    assert invalid_path.status_code == 422
    assert missing.json()["detail"] == "El recurso diagnóstico solicitado no existe."


async def test_next_task_is_idempotent_and_never_exposes_private_fields(
    diagnostic_client,
    db_session_factory,
):
    session_id = await create_started_session(diagnostic_client)
    operation_id = uuid4()
    first = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/next-task",
        json=operation_payload(operation_id),
    )
    replay = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/next-task",
        json=operation_payload(operation_id),
    )

    assert first.status_code == replay.status_code == 200
    first_task = first.json()["task"]
    replayed_task = replay.json()["task"]
    assert first_task["task_id"] == replayed_task["task_id"]
    assert first_task["created"] is True
    assert replayed_task["created"] is False
    forbidden = {
        "accepted_answers",
        "candidate",
        "expected_answer",
        "justification",
        "reason_codes",
        "rubric",
        "rubric_snapshot",
        "selection_reason",
    }
    assert recursive_keys(first_task).isdisjoint(forbidden)
    with db_session_factory() as db:
        assert db.scalar(select(func.count(DiagnosticTask.id))) == 1


async def test_v2_option_http_contract_scores_ids_and_persists_encoding(
    diagnostic_client,
    db_session_factory,
):
    app.dependency_overrides[get_diagnostic_candidate_provider] = lambda: StaticCandidateProvider(
        [option_id_candidate()]
    )
    session_id = await create_started_session(diagnostic_client)
    task = await select_task(diagnostic_client, session_id)

    assert task["response_type"] == "single_choice"
    assert task["answer_contract"] == "option-id.v1"
    assert task["options"] == [
        {"id": "opt_k4m2", "label": "Alpha"},
        {"id": "opt_p7q9", "label": "Beta"},
    ]
    assert recursive_keys(task).isdisjoint(
        {"accepted_option_ids", "correct_option_id", "rubric", "scoring_policy"}
    )

    payload = {
        "task_id": task["task_id"],
        "evaluation_id": str(uuid4()),
        "submission_id": str(uuid4()),
        "answer": {"kind": "single_choice", "selected_option_id": "opt_k4m2"},
        "instruction_state": "understood",
    }
    first = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/responses",
        json=payload,
    )
    replay = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/responses",
        json=payload,
    )
    conflict = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/responses",
        json={
            **payload,
            "answer": {"kind": "single_choice", "selected_option_id": "opt_p7q9"},
        },
    )

    assert first.status_code == replay.status_code == 200
    assert first.json()["evaluation"] == {
        "outcome": "correct_without_help",
        "score": 1.0,
        "polarity": "positive",
        "evaluator_confidence": 1.0,
    }
    assert first.json()["created"] is True
    assert replay.json()["created"] is False
    assert conflict.status_code == 409
    with db_session_factory() as db:
        response = db.scalar(select(DiagnosticResponse))
        persisted_task = db.get(DiagnosticTask, task["task_id"])
        assert response is not None
        assert response.response_text == "opt_k4m2"
        assert response.rubric["submission_encoding"] == "option-id.v1"
        assert persisted_task is not None
        assert persisted_task.options == task["options"]
        assert persisted_task.expected_answer["answer_contract"] == "option-id.v1"
        assert persisted_task.rubric["deterministic"]["accepted_option_ids"] == ["opt_k4m2"]


@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"answer": {"kind": "single_choice", "selected_option_id": "opt_z8x7"}},
        {"response_text": "Alpha"},
        {"answer": {"kind": "text", "text": "Alpha"}},
        {
            "response_text": "Alpha",
            "answer": {"kind": "single_choice", "selected_option_id": "opt_k4m2"},
        },
        {"answer": {"kind": "single_choice", "selected_option_id": "OPT_K4M2"}},
        {"answer": {"kind": "single_choice", "selected_option_id": " opt_k4m2"}},
    ],
)
async def test_v2_option_http_rejects_invalid_contract_without_evidence(
    diagnostic_client,
    db_session_factory,
    invalid_fields,
):
    app.dependency_overrides[get_diagnostic_candidate_provider] = lambda: StaticCandidateProvider(
        [option_id_candidate()]
    )
    session_id = await create_started_session(diagnostic_client)
    task = await select_task(diagnostic_client, session_id)
    payload = {
        "task_id": task["task_id"],
        "evaluation_id": str(uuid4()),
        "submission_id": str(uuid4()),
        "instruction_state": "understood",
        **invalid_fields,
    }

    response = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/responses",
        json=payload,
    )

    assert response.status_code == 422
    assert "accepted" not in response.text.casefold()
    assert "correct_option" not in response.text.casefold()
    with db_session_factory() as db:
        assert db.scalar(select(func.count(DiagnosticResponse.id))) == 0
        persisted_task = db.get(DiagnosticTask, task["task_id"])
        assert persisted_task is not None and persisted_task.status == "presented"


async def test_v2_existing_incorrect_option_is_linguistic_evidence(diagnostic_client):
    app.dependency_overrides[get_diagnostic_candidate_provider] = lambda: StaticCandidateProvider(
        [option_id_candidate()]
    )
    session_id = await create_started_session(diagnostic_client)
    task = await select_task(diagnostic_client, session_id)
    response = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/responses",
        json={
            "task_id": task["task_id"],
            "evaluation_id": str(uuid4()),
            "submission_id": str(uuid4()),
            "answer": {"kind": "single_choice", "selected_option_id": "opt_p7q9"},
            "instruction_state": "understood",
        },
    )

    assert response.status_code == 200
    assert response.json()["evaluation"]["outcome"] == "incorrect"
    assert response.json()["evaluation"]["score"] == 0.0


async def test_structured_text_v2_accepts_text_and_rejects_option_ids(
    diagnostic_client,
    db_session_factory,
):
    app.dependency_overrides[get_diagnostic_candidate_provider] = lambda: StaticCandidateProvider(
        [structured_text_candidate()]
    )
    session_id = await create_started_session(diagnostic_client)
    task = await select_task(diagnostic_client, session_id)
    invalid = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/responses",
        json={
            "task_id": task["task_id"],
            "evaluation_id": str(uuid4()),
            "submission_id": str(uuid4()),
            "answer": {"kind": "single_choice", "selected_option_id": "opt_k4m2"},
        },
    )
    valid = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/responses",
        json={
            "task_id": task["task_id"],
            "evaluation_id": str(uuid4()),
            "submission_id": str(uuid4()),
            "answer": {"kind": "text", "text": "alpha"},
            "response_language": "de",
        },
    )

    assert task["response_type"] == "short_text"
    assert task["answer_contract"] == "text.v2"
    assert invalid.status_code == 422
    assert valid.status_code == 200
    assert valid.json()["evaluation"]["outcome"] == "correct_without_help"
    with db_session_factory() as db:
        rows = db.scalars(select(DiagnosticResponse)).all()
        assert len(rows) == 1
        assert rows[0].response_text == "alpha"
        assert rows[0].rubric["submission_encoding"] == "text.v2"


async def test_legacy_task_rejects_structured_answer_without_reinterpreting_it(
    diagnostic_client,
    db_session_factory,
):
    session_id = await create_started_session(diagnostic_client)
    task = await select_task(diagnostic_client, session_id)
    response = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/responses",
        json={
            "task_id": task["task_id"],
            "evaluation_id": str(uuid4()),
            "submission_id": str(uuid4()),
            "answer": {"kind": "text", "text": "ja"},
            "response_language": "de",
        },
    )

    assert task["answer_contract"] == "legacy-text.v1"
    assert response.status_code == 422
    with db_session_factory() as db:
        assert db.scalar(select(func.count(DiagnosticResponse.id))) == 0


@pytest.mark.parametrize(
    ("overrides", "outcome", "score"),
    [
        ({}, "correct_without_help", 1.0),
        (
            {"response_text": "", "response_language": None},
            "not_evaluable",
            None,
        ),
        (
            {"response_text": "sí", "response_language": "es"},
            "not_evaluable",
            None,
        ),
        (
            {
                "response_text": None,
                "response_language": None,
                "instruction_state": "not_understood",
            },
            "not_evaluable",
            None,
        ),
    ],
)
async def test_response_cases_are_public_and_ollama_independent(
    diagnostic_client,
    monkeypatch,
    overrides,
    outcome,
    score,
):
    def reject_ollama_access(*args, **kwargs):
        raise AssertionError("the diagnostic HTTP flow must not access Ollama")

    monkeypatch.setattr(OllamaProvider, "client", reject_ollama_access)
    session_id = await create_started_session(diagnostic_client)
    task = await select_task(diagnostic_client, session_id)
    response = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/responses",
        json=response_payload(task["task_id"], **overrides),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["evaluation"]["outcome"] == outcome
    assert body["evaluation"]["score"] == score
    assert recursive_keys(body).isdisjoint(
        {"response_text", "rubric", "rubric_snapshot", "justification", "reason_codes"}
    )


async def test_response_idempotency_conflict_and_no_sensitive_logging(
    diagnostic_client,
    db_session_factory,
    caplog,
):
    session_id = await create_started_session(diagnostic_client)
    task = await select_task(diagnostic_client, session_id)
    sensitive = "PRIVATE-DIAGNOSTIC-TEXT-7b2c"
    payload = response_payload(task["task_id"], response_text=sensitive)
    caplog.set_level(logging.INFO)

    first = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/responses",
        json=payload,
    )
    replay = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/responses",
        json=payload,
    )
    conflict = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/responses",
        json={**payload, "response_text": "changed"},
    )

    assert first.status_code == replay.status_code == 200
    assert first.json()["created"] is True
    assert replay.json()["created"] is False
    assert conflict.status_code == 409
    assert sensitive not in caplog.text
    with db_session_factory() as db:
        assert db.scalar(select(func.count(DiagnosticResponse.id))) == 1


async def test_correction_is_append_only_idempotent_and_strict(
    diagnostic_client,
    db_session_factory,
):
    session_id = await create_started_session(diagnostic_client)
    task = await select_task(diagnostic_client, session_id)
    submitted = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/responses",
        json=response_payload(task["task_id"]),
    )
    response_id = submitted.json()["response_id"]
    evaluation_id = uuid4()
    correction = {
        "session_id": session_id,
        "evaluation_id": str(evaluation_id),
        "outcome": "incorrect",
        "score": 0.0,
        "polarity": "negative",
        "evaluator_confidence": 1.0,
        "justification": "Corrección manual estructurada.",
        "reason_codes": ["manual_correction"],
    }
    first = await diagnostic_client.post(
        f"/api/diagnostic/responses/{response_id}/corrections",
        json=correction,
    )
    replay = await diagnostic_client.post(
        f"/api/diagnostic/responses/{response_id}/corrections",
        json=correction,
    )
    conflict = await diagnostic_client.post(
        f"/api/diagnostic/responses/{response_id}/corrections",
        json={**correction, "outcome": "partial", "score": 0.4, "polarity": "positive"},
    )
    invalid = await diagnostic_client.post(
        f"/api/diagnostic/responses/{response_id}/corrections",
        json={**correction, "evaluation_id": str(uuid4()), "score": 0.4},
    )

    assert first.status_code == replay.status_code == 200
    assert first.json()["evaluation_revision"] == 2
    assert first.json()["created"] is True
    assert replay.json()["created"] is False
    assert conflict.status_code == 409
    assert invalid.status_code == 422
    with db_session_factory() as db:
        rows = db.scalars(
            select(DiagnosticResponse)
            .where(DiagnosticResponse.task_id == task["task_id"])
            .order_by(DiagnosticResponse.evaluation_revision)
        ).all()
        assert len(rows) == 2
        assert rows[1].supersedes_response_id == rows[0].id


async def test_partial_and_completed_results_are_safe_gets(
    diagnostic_client,
    db_session_factory,
):
    session_id = await create_started_session(diagnostic_client)
    for _ in range(2):
        task = await select_task(diagnostic_client, session_id)
        submitted = await diagnostic_client.post(
            f"/api/diagnostic/sessions/{session_id}/responses",
            json=response_payload(task["task_id"]),
        )
        assert submitted.status_code == 200

    partial = await diagnostic_client.get(f"/api/diagnostic/sessions/{session_id}/results")
    assert partial.status_code == 200
    reading = next(
        row for row in partial.json()["results"] if row["axis"] == "reading_comprehension"
    )
    assert reading["evidence_count"] == 2
    assert reading["estimated_score"] == 1.0
    with db_session_factory() as db:
        assert db.scalar(select(func.count(DiagnosticResult.id))) == 0

    exhausted = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/next-task",
        json=operation_payload(),
    )
    assert exhausted.status_code == 200
    assert exhausted.json()["task"] is None
    assert exhausted.json()["stop"]["reason"] == "no_candidates"
    complete_operation = operation_payload()
    completed = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/complete",
        json=complete_operation,
    )
    replay = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/complete",
        json=complete_operation,
    )
    assert completed.status_code == replay.status_code == 200
    assert completed.json()["session"]["state"] == "completed"
    final = await diagnostic_client.get(f"/api/diagnostic/sessions/{session_id}/results")
    assert final.status_code == 200
    with db_session_factory() as db:
        assert db.scalar(select(func.count(DiagnosticResult.id))) == 1


async def test_provider_is_503_in_production_until_a_versioned_bank_exists(client):
    response = await client.post("/api/diagnostic/sessions", json=create_payload())
    assert response.status_code == 503
    assert response.json()["detail"] == (
        "El banco diagnóstico versionado no está configurado en esta instalación."
    )
    assert "ollama" not in response.text.casefold()


async def test_reserved_provider_content_returns_503_and_rolls_back(
    diagnostic_client,
    db_session_factory,
):
    unsafe_provider = StaticCandidateProvider(
        [
            candidate(
                "unsafe",
                content={
                    "instruction": "No debe persistirse.",
                    "nested": {"expected_answer": "secreto"},
                },
            )
        ]
    )
    app.dependency_overrides[get_diagnostic_candidate_provider] = lambda: unsafe_provider
    session_id = await create_started_session(diagnostic_client)
    response = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/next-task",
        json=operation_payload(),
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "El contenido diagnóstico versionado no está disponible."
    assert "expected_answer" not in response.text
    with db_session_factory() as db:
        session = db.get(DiagnosticSession, session_id)
        assert session is not None
        assert session.status == "onboarding"
        assert db.scalar(select(func.count(DiagnosticTask.id))) == 0


async def test_openapi_and_runtime_dto_have_no_evaluator_private_fields(diagnostic_client):
    openapi = await diagnostic_client.get("/openapi.json")
    assert openapi.status_code == 200
    task_properties = openapi.json()["components"]["schemas"]["DiagnosticTaskPublic"]["properties"]
    response_properties = openapi.json()["components"]["schemas"]["DiagnosticResponsePublic"][
        "properties"
    ]
    assert set(task_properties).isdisjoint(
        {"expected_answer", "rubric", "selection_reason", "candidate"}
    )
    assert set(response_properties).isdisjoint(
        {"response_text", "rubric", "justification", "reason_codes"}
    )


async def test_http_flow_does_not_project_normal_learning_progress(
    diagnostic_client,
    db_session_factory,
):
    with db_session_factory() as db:
        before_student_skills = db.scalar(select(func.count(StudentSkill.id)))
        before_skill_evidence = db.scalar(select(func.count(SkillEvidence.id)))

    session_id = await create_started_session(diagnostic_client)
    task = await select_task(diagnostic_client, session_id)
    submitted = await diagnostic_client.post(
        f"/api/diagnostic/sessions/{session_id}/responses",
        json=response_payload(task["task_id"]),
    )
    assert submitted.status_code == 200

    with db_session_factory() as db:
        assert db.scalar(select(func.count(StudentSkill.id))) == before_student_skills
        assert db.scalar(select(func.count(SkillEvidence.id))) == before_skill_evidence


async def test_two_concurrent_identical_http_submissions_create_one_response(
    diagnostic_file_client,
    file_db_session_factory,
):
    session_id = await create_started_session(diagnostic_file_client)
    task = await select_task(diagnostic_file_client, session_id)
    payload = response_payload(task["task_id"])

    first, second = await asyncio.gather(
        diagnostic_file_client.post(
            f"/api/diagnostic/sessions/{session_id}/responses",
            json=payload,
        ),
        diagnostic_file_client.post(
            f"/api/diagnostic/sessions/{session_id}/responses",
            json=payload,
        ),
    )

    assert first.status_code == second.status_code == 200
    assert {first.json()["response_id"], second.json()["response_id"]} == {
        first.json()["response_id"]
    }
    assert sorted([first.json()["created"], second.json()["created"]]) == [False, True]
    with file_db_session_factory() as db:
        assert db.scalar(select(func.count(DiagnosticResponse.id))) == 1
