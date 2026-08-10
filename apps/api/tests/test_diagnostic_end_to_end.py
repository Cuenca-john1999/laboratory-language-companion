"""End-to-end diagnostic sessions over the real draft bank and migrated SQLite.

These tests intentionally sit above the focused selector, scorer, service, and
HTTP tests.  They migrate a real database, inject the draft bank explicitly,
and exercise the public API without replacing any diagnostic runtime component.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from llc_api.api.diagnostic import get_diagnostic_candidate_provider
from llc_api.content import FilesystemCandidateProvider
from llc_api.db.session import get_db, make_engine
from llc_api.diagnostic_engine.schemas import CORE_TEXT_AXES, RubricStrategy, TaskCandidate
from llc_api.main import app
from llc_api.models import (
    CurriculumSkill,
    DailyPlan,
    DailyPlanBlock,
    DiagnosticResponse,
    DiagnosticResult,
    DiagnosticSession,
    DiagnosticTask,
    ExerciseAttempt,
    LearningSession,
    Mistake,
    SkillEvidence,
    StudentSkill,
    StudentVocabulary,
    VocabularyItem,
)

pytestmark = pytest.mark.anyio

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONTENT_DIRECTORY = PROJECT_ROOT / "data" / "diagnostic"
ALEMBIC_CONFIG = PROJECT_ROOT / "apps" / "api" / "alembic.ini"
DIAGNOSTIC_VERSION = "diagnostic-text.v1"
CURRICULUM_VERSION = "a0-a1.v1"

_FORBIDDEN_PUBLIC_KEYS = {
    "accepted_answers",
    "accepted_option_ids",
    "accepted_responses",
    "authoring_notes",
    "candidate",
    "candidate_id",
    "correct_answer",
    "correct_option_id",
    "editorial",
    "expected_answer",
    "justification",
    "private",
    "reason_codes",
    "response_text",
    "rubric",
    "rubric_snapshot",
    "scoring",
    "scoring_policy",
    "selection_reason",
    "solution",
    "template_id",
}
_NORMAL_PROGRESS_MODELS = (
    StudentSkill,
    LearningSession,
    DailyPlan,
    DailyPlanBlock,
    ExerciseAttempt,
    Mistake,
    VocabularyItem,
    StudentVocabulary,
    SkillEvidence,
)
_EXPECTED_RESULT_DIMENSIONS = {
    ("reading_comprehension", None): 2,
    ("written_production", None): 1,
    ("written_production", 17): 1,
    ("active_grammar", 9): 1,
    ("active_grammar", 11): 1,
    ("receptive_vocabulary", 10): 2,
    ("productive_vocabulary", 10): 1,
    ("productive_vocabulary", 22): 1,
    ("communication_repair.typed", None): 2,
}


@dataclass(frozen=True)
class SessionRun:
    session_id: int
    route: tuple[str, ...]
    outcomes: tuple[str, ...]
    stop: dict[str, object]
    completed: dict[str, object]


class SyntheticOptionProvider:
    def __init__(self) -> None:
        self.candidate = TaskCandidate(
            candidate_id="e2e.synthetic.option-id",
            version="2",
            equivalence_key="e2e.synthetic.option-id.eq",
            axis="reading_comprehension",
            task_type="binary_choice",
            difficulty=1,
            content={
                "instructions": "Selecciona una opción sintética.",
                "prompt": "Contenido E2E v2 exclusivo de prueba.",
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
            rubric={
                "strategy": "option_id",
                "accepted_option_ids": ["opt_k4m2"],
            },
            estimated_seconds=30,
        )

    def candidates(self, *, diagnostic_version: str):
        assert diagnostic_version == DIAGNOSTIC_VERSION
        return [self.candidate]


class EndToEndHarness:
    """One migrated database plus an explicitly editorial candidate provider."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_url = f"sqlite:///{database_path}"
        self._open_database()

    def _open_database(self) -> None:
        self.engine = make_engine(self.database_url)
        self.factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )
        with self.factory() as db:
            skill_ids = set(
                db.scalars(
                    select(CurriculumSkill.skill_id).where(
                        CurriculumSkill.curriculum_version == CURRICULUM_VERSION
                    )
                ).all()
            )
        self.provider = FilesystemCandidateProvider.from_directory(
            CONTENT_DIRECTORY,
            curriculum_skill_ids={CURRICULUM_VERSION: skill_ids},
            production_only=False,
        )
        self.candidates = {
            candidate.candidate_id: candidate
            for candidate in self.provider.candidates(diagnostic_version=DIAGNOSTIC_VERSION)
        }

    def reopen(self) -> None:
        self.engine.dispose()
        self._open_database()

    def close(self) -> None:
        self.engine.dispose()

    @asynccontextmanager
    async def client(
        self,
        *,
        inject_draft: bool = True,
        provider: object | None = None,
    ) -> AsyncIterator[httpx.AsyncClient]:
        def override_db():
            with self.factory() as db:
                yield db

        missing = object()
        previous_db = app.dependency_overrides.get(get_db, missing)
        previous_provider = app.dependency_overrides.get(
            get_diagnostic_candidate_provider,
            missing,
        )
        app.dependency_overrides[get_db] = override_db
        if inject_draft:
            selected_provider = provider or self.provider
            app.dependency_overrides[get_diagnostic_candidate_provider] = lambda: selected_provider
        else:
            app.dependency_overrides.pop(get_diagnostic_candidate_provider, None)
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                yield client
        finally:
            if previous_db is missing:
                app.dependency_overrides.pop(get_db, None)
            else:
                app.dependency_overrides[get_db] = previous_db
            if previous_provider is missing:
                app.dependency_overrides.pop(get_diagnostic_candidate_provider, None)
            else:
                app.dependency_overrides[get_diagnostic_candidate_provider] = previous_provider

    def candidate_for_task(self, task_id: int) -> TaskCandidate:
        with self.factory() as db:
            task = db.get(DiagnosticTask, task_id)
            assert task is not None
            return self.candidates[task.template_id]

    def template_id_for_task(self, task_id: int) -> str:
        with self.factory() as db:
            task = db.get(DiagnosticTask, task_id)
            assert task is not None
            return task.template_id


@pytest.fixture(scope="session")
def migrated_database_template(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("diagnostic-e2e-migrated")
    database_path = directory / "migrated-0005.sqlite3"
    environment = {**os.environ, "LLC_DATABASE_URL": f"sqlite:///{database_path}"}
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ALEMBIC_CONFIG),
            "upgrade",
            "0005",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    engine = make_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005"
        assert connection.exec_driver_sql("PRAGMA quick_check").scalar_one() == "ok"
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    engine.dispose()
    return database_path


@pytest.fixture
def e2e_harness_factory(tmp_path, migrated_database_template):
    harnesses: list[EndToEndHarness] = []
    serial = 0

    def create(label: str = "session") -> EndToEndHarness:
        nonlocal serial
        serial += 1
        path = tmp_path / f"{serial:02d}-{label}.sqlite3"
        shutil.copyfile(migrated_database_template, path)
        harness = EndToEndHarness(path)
        harnesses.append(harness)
        return harness

    yield create
    for harness in harnesses:
        harness.close()


def _normalised(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _correct_answer(candidate: TaskCandidate) -> str:
    if candidate.rubric.strategy == RubricStrategy.ORDERED_TOKENS:
        return " ".join(candidate.rubric.expected_tokens)
    return candidate.rubric.accepted_answers[0]


def _incorrect_answer(candidate: TaskCandidate) -> str:
    accepted = {_normalised(item) for item in candidate.rubric.accepted_answers}
    for option in candidate.options:
        if isinstance(option, str) and _normalised(option) not in accepted:
            return option
    return "__respuesta_incorrecta_cerrada__"


def _recursive_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            nested_key for nested in value.values() for nested_key in _recursive_keys(nested)
        }
    if isinstance(value, list):
        return {nested_key for nested in value for nested_key in _recursive_keys(nested)}
    return set()


def _assert_public_payload(value: object) -> None:
    normalised_keys = {key.casefold().replace("-", "_") for key in _recursive_keys(value)}
    assert normalised_keys.isdisjoint(_FORBIDDEN_PUBLIC_KEYS)


def _json(response: httpx.Response, expected_status: int = 200) -> dict[str, object]:
    assert response.status_code == expected_status, response.text
    assert "traceback" not in response.text.casefold()
    body = response.json()
    _assert_public_payload(body)
    return body


def _creation_payload(request_id: UUID | None = None, **overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "request_id": str(request_id or uuid4()),
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "curriculum_version": CURRICULUM_VERSION,
        "target_task_count": 12,
    }
    payload.update(overrides)
    return payload


def _operation_payload(operation_id: UUID | None = None, **overrides) -> dict[str, object]:
    payload: dict[str, object] = {"operation_id": str(operation_id or uuid4())}
    payload.update(overrides)
    return payload


def _response_payload(task_id: int, **overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "task_id": task_id,
        "evaluation_id": str(uuid4()),
        "submission_id": str(uuid4()),
        "response_text": "",
        "response_language": "de",
        "instruction_state": "understood",
    }
    payload.update(overrides)
    return payload


async def _create_started_session(client: httpx.AsyncClient) -> int:
    created = _json(
        await client.post("/api/diagnostic/sessions", json=_creation_payload()),
        201,
    )
    session_id = int(created["session_id"])
    started = _json(
        await client.post(
            f"/api/diagnostic/sessions/{session_id}/start",
            json=_operation_payload(),
        )
    )
    assert started["state"] == "active"
    return session_id


async def _submit_mode(
    client: httpx.AsyncClient,
    session_id: int,
    task: dict[str, object],
    candidate: TaskCandidate,
    mode: str,
) -> dict[str, object]:
    task_id = int(task["task_id"])
    if mode == "not_evaluable":
        first = _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/responses",
                json=_response_payload(task_id, response_text="", response_language=None),
            )
        )
        assert first["evaluation"]["outcome"] == "not_evaluable"
        assert first["evaluation"]["score"] is None
        second = _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/responses",
                json=_response_payload(
                    task_id,
                    response_text="",
                    response_language=None,
                    assistance=["retry"],
                ),
            )
        )
        assert second["evaluation"]["outcome"] == "not_evaluable"
        assert second["evaluation"]["score"] is None
        assert second["task_status"] == "skipped"
        return second

    answer = (
        _correct_answer(candidate) if mode in {"correct", "help"} else _incorrect_answer(candidate)
    )
    overrides: dict[str, object] = {"response_text": answer}
    if mode == "help":
        overrides["assistance"] = ["clarification"]
    elif mode == "partial":
        overrides["partially_communicative"] = True
    response = _json(
        await client.post(
            f"/api/diagnostic/sessions/{session_id}/responses",
            json=_response_payload(task_id, **overrides),
        )
    )
    expected = {
        "correct": ("correct_without_help", 1.0),
        "incorrect": ("incorrect", 0.0),
        "partial": ("partial", 0.4),
        "help": ("correct_with_help", 0.7),
    }[mode]
    assert response["evaluation"]["outcome"] == expected[0]
    assert response["evaluation"]["score"] == expected[1]
    assert response["task_status"] == "evaluated"
    return response


async def _run_active_session(
    client: httpx.AsyncClient,
    harness: EndToEndHarness,
    session_id: int,
    strategy: Callable[[TaskCandidate, int], str],
) -> SessionRun:
    route: list[str] = []
    outcomes: list[str] = []
    while True:
        selection = _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/next-task",
                json=_operation_payload(),
            )
        )
        task = selection["task"]
        if task is None:
            stop = selection["stop"]
            break
        assert len(route) < 20
        candidate = harness.candidate_for_task(int(task["task_id"]))
        route.append(candidate.candidate_id)
        mode = strategy(candidate, len(route) - 1)
        response = await _submit_mode(client, session_id, task, candidate, mode)
        outcomes.append(str(response["evaluation"]["outcome"]))

    completed = _json(
        await client.post(
            f"/api/diagnostic/sessions/{session_id}/complete",
            json=_operation_payload(),
        )
    )
    return SessionRun(
        session_id=session_id,
        route=tuple(route),
        outcomes=tuple(outcomes),
        stop=stop,
        completed=completed,
    )


async def _run_new_session(
    client: httpx.AsyncClient,
    harness: EndToEndHarness,
    strategy: Callable[[TaskCandidate, int], str],
) -> SessionRun:
    session_id = await _create_started_session(client)
    return await _run_active_session(client, harness, session_id, strategy)


def _progress_snapshot(factory: sessionmaker[Session]) -> dict[str, tuple[tuple[object, ...], ...]]:
    snapshot: dict[str, tuple[tuple[object, ...], ...]] = {}
    with factory() as db:
        for model in _NORMAL_PROGRESS_MODELS:
            table = model.__table__
            order = table.c.id if "id" in table.c else next(iter(table.primary_key.columns))
            rows = db.execute(select(*table.c).order_by(order)).all()
            snapshot[table.name] = tuple(tuple(row) for row in rows)
    return snapshot


def _assert_database_integrity(harness: EndToEndHarness) -> None:
    with harness.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005"
        assert connection.exec_driver_sql("PRAGMA quick_check").scalar_one() == "ok"
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []


def _axis_totals(results: list[dict[str, object]]) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"evaluable": 0, "positive": 0, "negative": 0, "insufficient": 0}
    )
    for result in results:
        axis = str(result["axis"])
        if axis not in {item.value for item in CORE_TEXT_AXES}:
            continue
        totals[axis]["evaluable"] += int(result["evidence_count"])
        totals[axis]["positive"] += int(result["positive_evidence_count"])
        totals[axis]["negative"] += int(result["negative_evidence_count"])
        totals[axis]["insufficient"] += int(result["insufficient_evidence_count"])
    return dict(totals)


def _assert_completed_database(
    harness: EndToEndHarness,
    run: SessionRun,
    *,
    evaluable: int,
) -> None:
    with harness.factory() as db:
        session = db.get(DiagnosticSession, run.session_id)
        assert session is not None
        assert session.status == "completed"
        assert session.tasks_presented == 12
        assert session.tasks_evaluable == evaluable
        assert session.completed_at is not None and session.completed_at.tzinfo is not None
        tasks = db.scalars(
            select(DiagnosticTask)
            .where(DiagnosticTask.session_id == run.session_id)
            .order_by(DiagnosticTask.sequence)
        ).all()
        assert [task.sequence for task in tasks] == list(range(1, 13))
        assert len({task.template_id for task in tasks}) == 12
        assert Counter(task.primary_axis for task in tasks) == {
            axis.value: 2 for axis in CORE_TEXT_AXES
        }
        results = db.scalars(
            select(DiagnosticResult).where(DiagnosticResult.session_id == run.session_id)
        ).all()
        assert len(results) == len(_EXPECTED_RESULT_DIMENSIONS)
        assert {result.projection_status for result in results} == {"not_projected"}
        assert all(0 <= result.estimate_confidence <= 1 for result in results)
    _assert_database_integrity(harness)


@pytest.mark.parametrize(
    ("mode", "expected_outcome", "expected_score", "positive", "negative"),
    [
        ("correct", "correct_without_help", 1.0, 2, 0),
        ("incorrect", "incorrect", 0.0, 0, 2),
    ],
)
async def test_real_bank_completes_twelve_correct_or_incorrect_tasks(
    e2e_harness_factory,
    mode,
    expected_outcome,
    expected_score,
    positive,
    negative,
):
    harness = e2e_harness_factory(mode)
    before_progress = _progress_snapshot(harness.factory)
    async with harness.client() as client:
        run = await _run_new_session(client, harness, lambda _candidate, _index: mode)

    assert len(run.route) == len(set(run.route)) == 12
    assert run.outcomes == (expected_outcome,) * 12
    assert run.stop == {
        "should_stop": True,
        "reason": "target_reached",
        "partial": False,
        "detail": "Se alcanzó el objetivo de tareas con muestras en todos los ejes prioritarios.",
    }
    session_public = run.completed["session"]
    assert session_public["state"] == "completed"
    assert session_public["tasks_presented"] == 12
    assert session_public["tasks_evaluable"] == 12
    assert datetime.fromisoformat(session_public["completed_at"]).tzinfo is not None
    totals = _axis_totals(run.completed["results"])
    assert set(totals) == {axis.value for axis in CORE_TEXT_AXES}
    assert all(
        values
        == {
            "evaluable": 2,
            "positive": positive,
            "negative": negative,
            "insufficient": 0,
        }
        for values in totals.values()
    )
    assert sum(values["evaluable"] for values in totals.values()) == 12
    assert all(
        result["estimated_score"] in {expected_score, None} for result in run.completed["results"]
    )
    assert any(result["skill_id"] is None for result in run.completed["results"])
    observed_dimensions = {
        (result["axis"], result["skill_id"]): result["evidence_count"]
        for result in run.completed["results"]
        if result["axis"] in {axis.value for axis in CORE_TEXT_AXES}
    }
    assert observed_dimensions == _EXPECTED_RESULT_DIMENSIONS
    _assert_completed_database(harness, run, evaluable=12)
    assert _progress_snapshot(harness.factory) == before_progress


async def test_mixed_trajectory_is_reproducible_and_keeps_structured_outcomes(
    e2e_harness_factory,
):
    modes_by_axis = {
        "reading_comprehension": "correct",
        "written_production": "incorrect",
        "active_grammar": "partial",
        "receptive_vocabulary": "help",
        "productive_vocabulary": "correct",
        "communication_repair.typed": "incorrect",
    }
    runs: list[SessionRun] = []
    result_sets: list[list[dict[str, object]]] = []
    for label in ("mixed-a", "mixed-b"):
        harness = e2e_harness_factory(label)
        async with harness.client() as client:
            run = await _run_new_session(
                client,
                harness,
                lambda candidate, _index: modes_by_axis[candidate.axis.value],
            )
        runs.append(run)
        result_sets.append(run.completed["results"])
        _assert_completed_database(harness, run, evaluable=12)

    assert runs[0].route == runs[1].route
    assert runs[0].outcomes == runs[1].outcomes
    assert result_sets[0] == result_sets[1]
    assert Counter(runs[0].outcomes) == {
        "correct_without_help": 4,
        "incorrect": 4,
        "partial": 2,
        "correct_with_help": 2,
    }
    assert runs[0].stop["reason"] == "target_reached"
    assert runs[0].stop["partial"] is False


async def test_assistance_reduces_aggregate_confidence(e2e_harness_factory):
    result_sets: dict[str, list[dict[str, object]]] = {}
    runs: dict[str, SessionRun] = {}
    for mode in ("correct", "help"):
        harness = e2e_harness_factory(f"confidence-{mode}")
        before_progress = _progress_snapshot(harness.factory)
        async with harness.client() as client:
            run = await _run_new_session(
                client,
                harness,
                lambda _candidate, _index, selected_mode=mode: selected_mode,
            )
        runs[mode] = run
        result_sets[mode] = run.completed["results"]
        assert run.stop["reason"] == "target_reached"
        assert run.stop["partial"] is False
        assert run.completed["session"]["state"] == "completed"
        with harness.factory() as db:
            persisted = db.scalars(
                select(DiagnosticResult).where(DiagnosticResult.session_id == run.session_id)
            ).all()
            assert len(persisted) == len(_EXPECTED_RESULT_DIMENSIONS)
            assert {result.projection_status for result in persisted} == {"not_projected"}
        assert _progress_snapshot(harness.factory) == before_progress

    confidence_without_help = {
        (row["axis"], row["skill_id"]): row["estimate_confidence"]
        for row in result_sets["correct"]
        if row["evidence_count"]
    }
    confidence_with_help = {
        (row["axis"], row["skill_id"]): row["estimate_confidence"]
        for row in result_sets["help"]
        if row["evidence_count"]
    }
    assert confidence_without_help.keys() == confidence_with_help.keys()
    assert all(
        confidence_with_help[dimension] < confidence_without_help[dimension]
        for dimension in confidence_without_help
    )
    scores_without_help = {
        (row["axis"], row["skill_id"]): row["estimated_score"]
        for row in result_sets["correct"]
        if row["estimated_score"] is not None
    }
    scores_with_help = {
        (row["axis"], row["skill_id"]): row["estimated_score"]
        for row in result_sets["help"]
        if row["estimated_score"] is not None
    }
    assert scores_without_help.keys() == scores_with_help.keys()
    assert set(scores_without_help.values()) == {1.0}
    assert set(scores_with_help.values()) == {0.7}
    assert runs["correct"].outcomes == ("correct_without_help",) * 12
    assert runs["help"].outcomes == ("correct_with_help",) * 12


async def test_not_evaluable_paths_finish_partial_without_inventing_scores(
    e2e_harness_factory,
):
    one_seen_per_axis: set[str] = set()

    def one_not_evaluable(candidate: TaskCandidate, _index: int) -> str:
        axis = candidate.axis.value
        if axis not in one_seen_per_axis:
            one_seen_per_axis.add(axis)
            return "not_evaluable"
        return "correct"

    one_harness = e2e_harness_factory("one-not-evaluable-per-axis")
    one_before = _progress_snapshot(one_harness.factory)
    async with one_harness.client() as client:
        one_run = await _run_new_session(client, one_harness, one_not_evaluable)

    assert len(one_run.route) == 12
    assert one_run.outcomes.count("not_evaluable") == 6
    assert one_run.stop["reason"] == "no_candidates"
    assert one_run.stop["partial"] is True
    assert one_run.completed["session"]["tasks_evaluable"] == 6
    one_totals = _axis_totals(one_run.completed["results"])
    assert all(
        values
        == {
            "evaluable": 1,
            "positive": 1,
            "negative": 0,
            "insufficient": 1,
        }
        for values in one_totals.values()
    )
    with one_harness.factory() as db:
        assert db.scalar(select(func.count(DiagnosticResponse.id))) == 18
    _assert_completed_database(one_harness, one_run, evaluable=6)
    assert _progress_snapshot(one_harness.factory) == one_before

    all_harness = e2e_harness_factory("all-not-evaluable")
    all_before = _progress_snapshot(all_harness.factory)
    async with all_harness.client() as client:
        all_run = await _run_new_session(
            client,
            all_harness,
            lambda _candidate, _index: "not_evaluable",
        )

    assert len(all_run.route) == 12
    assert all_run.outcomes == ("not_evaluable",) * 12
    assert all_run.stop["reason"] == "no_candidates"
    assert all_run.stop["partial"] is True
    assert all_run.completed["session"]["tasks_evaluable"] == 0
    all_totals = _axis_totals(all_run.completed["results"])
    assert all(values["evaluable"] == 0 for values in all_totals.values())
    assert all(values["insufficient"] == 2 for values in all_totals.values())
    assert all(
        result["estimated_score"] is None
        for result in all_run.completed["results"]
        if result["axis"] in {axis.value for axis in CORE_TEXT_AXES}
    )
    with all_harness.factory() as db:
        assert db.scalar(select(func.count(DiagnosticResponse.id))) == 24
    _assert_completed_database(all_harness, all_run, evaluable=0)
    assert _progress_snapshot(all_harness.factory) == all_before


async def test_pause_resume_and_recreated_context_preserve_the_pending_task_and_route(
    e2e_harness_factory,
):
    baseline_harness = e2e_harness_factory("uninterrupted")
    async with baseline_harness.client() as client:
        baseline = await _run_new_session(
            client,
            baseline_harness,
            lambda _candidate, _index: "correct",
        )

    resumed_harness = e2e_harness_factory("resumed")
    async with resumed_harness.client() as client:
        session_id = await _create_started_session(client)
        pending = _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/next-task",
                json=_operation_payload(),
            )
        )["task"]
        assert pending is not None
        pending_template = resumed_harness.template_id_for_task(int(pending["task_id"]))
        paused = _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/pause",
                json=_operation_payload(),
            )
        )
        assert paused["state"] == "paused"
        paused_active_seconds = paused["active_seconds"]

    resumed_harness.reopen()
    async with resumed_harness.client() as client:
        restored = _json(await client.get(f"/api/diagnostic/sessions/{session_id}"))
        assert restored["session"]["state"] == "paused"
        assert restored["session"]["active_seconds"] == paused_active_seconds
        assert restored["current_task"]["task_id"] == pending["task_id"]
        resumed = _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/resume",
                json=_operation_payload(),
            )
        )
        assert resumed["state"] == "active"
        replayed_pending = _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/next-task",
                json=_operation_payload(),
            )
        )["task"]
        assert replayed_pending["task_id"] == pending["task_id"]
        assert resumed_harness.template_id_for_task(int(replayed_pending["task_id"])) == (
            pending_template
        )
        candidate = resumed_harness.candidate_for_task(int(replayed_pending["task_id"]))
        await _submit_mode(client, session_id, replayed_pending, candidate, "correct")
        remainder = await _run_active_session(
            client,
            resumed_harness,
            session_id,
            lambda _candidate, _index: "correct",
        )

    resumed_route = (pending_template, *remainder.route)
    assert resumed_route == baseline.route
    assert remainder.completed["results"] == baseline.completed["results"]
    with resumed_harness.factory() as db:
        tasks = db.scalars(
            select(DiagnosticTask).where(DiagnosticTask.session_id == session_id)
        ).all()
        assert len(tasks) == len({task.template_id for task in tasks}) == 12
        assert db.scalar(select(func.count(DiagnosticResponse.id))) == 12
    _assert_completed_database(resumed_harness, remainder, evaluable=12)


async def test_real_bank_http_idempotency_corrections_and_terminal_states(
    e2e_harness_factory,
):
    harness = e2e_harness_factory("idempotency-correction")
    before_progress = _progress_snapshot(harness.factory)
    async with harness.client() as client:
        request_id = uuid4()
        create = _creation_payload(request_id)
        first_create = _json(await client.post("/api/diagnostic/sessions", json=create), 201)
        replay_create = _json(await client.post("/api/diagnostic/sessions", json=create), 201)
        assert first_create["session_id"] == replay_create["session_id"]
        conflict_create = await client.post(
            "/api/diagnostic/sessions",
            json=_creation_payload(request_id, instruction_language="de"),
        )
        _json(conflict_create, 409)
        session_id = int(first_create["session_id"])

        start_id = uuid4()
        start = _operation_payload(start_id)
        assert (
            _json(await client.post(f"/api/diagnostic/sessions/{session_id}/start", json=start))[
                "state"
            ]
            == "active"
        )
        assert (
            _json(await client.post(f"/api/diagnostic/sessions/{session_id}/start", json=start))[
                "state"
            ]
            == "active"
        )
        _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/start",
                json=_operation_payload(start_id, reason="different"),
            ),
            409,
        )

        selection_id = uuid4()
        selection_payload = _operation_payload(selection_id)
        selected = _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/next-task",
                json=selection_payload,
            )
        )["task"]
        replayed = _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/next-task",
                json=selection_payload,
            )
        )["task"]
        assert selected["task_id"] == replayed["task_id"]
        candidate = harness.candidate_for_task(int(selected["task_id"]))

        submission = _response_payload(
            int(selected["task_id"]),
            response_text=_incorrect_answer(candidate),
        )
        first_response = _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/responses",
                json=submission,
            )
        )
        replay_response = _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/responses",
                json=submission,
            )
        )
        assert first_response["created"] is True
        assert replay_response["created"] is False
        assert first_response["response_id"] == replay_response["response_id"]
        _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/responses",
                json={**submission, "response_text": _correct_answer(candidate)},
            ),
            409,
        )
        _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/responses",
                json={**submission, "evaluation_id": str(uuid4())},
            ),
            409,
        )
        before_correction = _json(
            await client.get(f"/api/diagnostic/sessions/{session_id}/results")
        )
        assert (
            sum(result["negative_evidence_count"] for result in before_correction["results"]) == 1
        )

        correction_id = uuid4()
        correction = {
            "session_id": session_id,
            "evaluation_id": str(correction_id),
            "outcome": "correct_without_help",
            "score": 1.0,
            "polarity": "positive",
            "evaluator_confidence": 1.0,
            "justification": "Corrección E2E estructurada.",
            "reason_codes": ["e2e_manual_correction"],
        }
        correction_url = f"/api/diagnostic/responses/{first_response['response_id']}/corrections"
        first_correction = _json(await client.post(correction_url, json=correction))
        replay_correction = _json(await client.post(correction_url, json=correction))
        assert first_correction["evaluation_revision"] == 2
        assert first_correction["created"] is True
        assert replay_correction["created"] is False
        _json(
            await client.post(
                correction_url,
                json={
                    **correction,
                    "outcome": "partial",
                    "score": 0.4,
                    "polarity": "positive",
                },
            ),
            409,
        )
        after_correction = _json(await client.get(f"/api/diagnostic/sessions/{session_id}/results"))
        assert sum(result["positive_evidence_count"] for result in after_correction["results"]) == 1
        assert sum(result["negative_evidence_count"] for result in after_correction["results"]) == 0

        remainder = await _run_active_session(
            client,
            harness,
            session_id,
            lambda _candidate, _index: "correct",
        )

    assert len(remainder.route) == 11
    assert remainder.stop["reason"] == "target_reached"
    final_totals = _axis_totals(remainder.completed["results"])
    assert all(values["positive"] == 2 for values in final_totals.values())
    assert all(values["negative"] == 0 for values in final_totals.values())
    with harness.factory() as db:
        rows = db.scalars(
            select(DiagnosticResponse)
            .where(DiagnosticResponse.task_id == int(selected["task_id"]))
            .order_by(DiagnosticResponse.evaluation_revision)
        ).all()
        assert len(rows) == 2
        assert rows[1].supersedes_response_id == rows[0].id
        assert db.scalar(select(func.count(DiagnosticTask.id))) == 12
        assert {result.projection_status for result in db.scalars(select(DiagnosticResult))} == {
            "not_projected"
        }
    _assert_completed_database(harness, remainder, evaluable=12)
    assert _progress_snapshot(harness.factory) == before_progress

    abandoned_harness = e2e_harness_factory("abandoned")
    abandoned_before = _progress_snapshot(abandoned_harness.factory)
    async with abandoned_harness.client() as client:
        abandoned_session = await _create_started_session(client)
        open_task = _json(
            await client.post(
                f"/api/diagnostic/sessions/{abandoned_session}/next-task",
                json=_operation_payload(),
            )
        )["task"]
        _json(
            await client.post(
                f"/api/diagnostic/sessions/{abandoned_session}/complete",
                json=_operation_payload(),
            ),
            409,
        )
        abandoned = _json(
            await client.post(
                f"/api/diagnostic/sessions/{abandoned_session}/abandon",
                json=_operation_payload(reason="user_abandoned"),
            )
        )
        assert abandoned["state"] == "abandoned"
        _json(
            await client.post(
                f"/api/diagnostic/sessions/{abandoned_session}/responses",
                json=_response_payload(int(open_task["task_id"]), response_text="Wir"),
            ),
            409,
        )
        _json(
            await client.post(
                f"/api/diagnostic/sessions/{abandoned_session}/resume",
                json=_operation_payload(),
            ),
            409,
        )
        _json(
            await client.post(
                f"/api/diagnostic/sessions/{abandoned_session}/complete",
                json=_operation_payload(),
            ),
            409,
        )
        snapshot = _json(await client.get(f"/api/diagnostic/sessions/{abandoned_session}"))
        assert snapshot["session"]["state"] == "abandoned"
        assert snapshot["stop"]["partial"] is True
    assert _progress_snapshot(abandoned_harness.factory) == abandoned_before


async def test_concurrent_real_bank_mutations_leave_one_coherent_effect(
    e2e_harness_factory,
):
    identical_harness = e2e_harness_factory("concurrent-identical")
    async with identical_harness.client() as client:
        session_id = await _create_started_session(client)
        task = _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/next-task",
                json=_operation_payload(),
            )
        )["task"]
        candidate = identical_harness.candidate_for_task(int(task["task_id"]))
        payload = _response_payload(
            int(task["task_id"]),
            response_text=_correct_answer(candidate),
        )
        first, second = await asyncio.gather(
            client.post(f"/api/diagnostic/sessions/{session_id}/responses", json=payload),
            client.post(f"/api/diagnostic/sessions/{session_id}/responses", json=payload),
        )
        first_body = _json(first)
        second_body = _json(second)
        assert first_body["response_id"] == second_body["response_id"]
        assert sorted([first_body["created"], second_body["created"]]) == [False, True]
    with identical_harness.factory() as db:
        assert db.scalar(select(func.count(DiagnosticResponse.id))) == 1

    conflicting_harness = e2e_harness_factory("concurrent-conflicting")
    async with conflicting_harness.client() as client:
        session_id = await _create_started_session(client)
        task = _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/next-task",
                json=_operation_payload(),
            )
        )["task"]
        candidate = conflicting_harness.candidate_for_task(int(task["task_id"]))
        first_payload = _response_payload(
            int(task["task_id"]),
            response_text=_correct_answer(candidate),
        )
        second_payload = _response_payload(
            int(task["task_id"]),
            response_text=_incorrect_answer(candidate),
        )
        first, second = await asyncio.gather(
            client.post(
                f"/api/diagnostic/sessions/{session_id}/responses",
                json=first_payload,
            ),
            client.post(
                f"/api/diagnostic/sessions/{session_id}/responses",
                json=second_payload,
            ),
        )
        assert sorted([first.status_code, second.status_code]) in ([200, 409], [200, 503])
        _assert_public_payload(first.json())
        _assert_public_payload(second.json())
        assert "traceback" not in (first.text + second.text).casefold()
    with conflicting_harness.factory() as db:
        assert db.scalar(select(func.count(DiagnosticResponse.id))) == 1
        session = db.get(DiagnosticSession, session_id)
        assert session is not None and session.tasks_evaluable == 1

    race_harness = e2e_harness_factory("response-pause-race")
    async with race_harness.client() as client:
        session_id = await _create_started_session(client)
        task = _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/next-task",
                json=_operation_payload(),
            )
        )["task"]
        candidate = race_harness.candidate_for_task(int(task["task_id"]))
        response, pause = await asyncio.gather(
            client.post(
                f"/api/diagnostic/sessions/{session_id}/responses",
                json=_response_payload(
                    int(task["task_id"]),
                    response_text=_correct_answer(candidate),
                ),
            ),
            client.post(
                f"/api/diagnostic/sessions/{session_id}/pause",
                json=_operation_payload(),
            ),
        )
        assert response.status_code in {200, 409, 503}
        assert pause.status_code in {200, 503}
        assert 200 in {response.status_code, pause.status_code}
        _assert_public_payload(response.json())
        _assert_public_payload(pause.json())
        snapshot = _json(await client.get(f"/api/diagnostic/sessions/{session_id}"))
        assert snapshot["session"]["state"] in {"active", "paused"}
    with race_harness.factory() as db:
        response_count = db.scalar(select(func.count(DiagnosticResponse.id)))
        assert response_count in {0, 1}
        task_row = db.get(DiagnosticTask, int(task["task_id"]))
        assert task_row is not None
        if response_count == 1:
            assert task_row.status == "evaluated"
        else:
            assert task_row.status == "presented"
    _assert_database_integrity(race_harness)


async def test_draft_requires_explicit_injection_and_production_remains_unavailable(
    e2e_harness_factory,
):
    harness = e2e_harness_factory("draft-boundary")
    async with harness.client(inject_draft=False) as production_client:
        unavailable = _json(
            await production_client.post(
                "/api/diagnostic/sessions",
                json=_creation_payload(),
            ),
            503,
        )
        assert unavailable["detail"] == (
            "El banco diagnóstico versionado no está configurado en esta instalación."
        )
        assert "lm_studio" not in str(unavailable).casefold()

    async with harness.client(inject_draft=True) as editorial_client:
        created = _json(
            await editorial_client.post(
                "/api/diagnostic/sessions",
                json=_creation_payload(),
            ),
            201,
        )
        assert created["state"] == "created"

    assert len(harness.candidates) == 12
    with harness.factory() as db:
        assert db.scalar(select(func.count(DiagnosticSession.id))) == 1
    _assert_database_integrity(harness)


async def test_synthetic_v2_option_runs_end_to_end_without_projection(
    e2e_harness_factory,
):
    harness = e2e_harness_factory("synthetic-option-v2")
    provider = SyntheticOptionProvider()
    before_progress = _progress_snapshot(harness.factory)
    evaluation_id = uuid4()
    submission_id = uuid4()
    async with harness.client(provider=provider) as client:
        session_id = await _create_started_session(client)
        selection = _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/next-task",
                json=_operation_payload(),
            )
        )
        task = selection["task"]
        assert task["response_type"] == "single_choice"
        assert task["answer_contract"] == "option-id.v1"
        assert task["options"] == [
            {"id": "opt_k4m2", "label": "Alpha"},
            {"id": "opt_p7q9", "label": "Beta"},
        ]
        payload = {
            "task_id": task["task_id"],
            "evaluation_id": str(evaluation_id),
            "submission_id": str(submission_id),
            "answer": {"kind": "single_choice", "selected_option_id": "opt_k4m2"},
            "instruction_state": "understood",
        }
        first = _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/responses",
                json=payload,
            )
        )
        replay = _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/responses",
                json=payload,
            )
        )
        assert first["evaluation"]["outcome"] == "correct_without_help"
        assert first["created"] is True
        assert replay["created"] is False
        results = _json(await client.get(f"/api/diagnostic/sessions/{session_id}/results"))
        reading = next(
            result for result in results["results"] if result["axis"] == "reading_comprehension"
        )
        assert reading["evidence_count"] == 1
        assert reading["coverage_status"] == "insufficient"

    harness.reopen()
    async with harness.client(provider=provider) as client:
        restored = _json(await client.get(f"/api/diagnostic/sessions/{session_id}"))
        assert restored["session"]["tasks_evaluable"] == 1
        exhausted = _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/next-task",
                json=_operation_payload(),
            )
        )
        assert exhausted["task"] is None
        assert exhausted["stop"]["reason"] == "no_candidates"
        completed = _json(
            await client.post(
                f"/api/diagnostic/sessions/{session_id}/complete",
                json=_operation_payload(),
            )
        )
        assert completed["session"]["state"] == "completed"

    with harness.factory() as db:
        response = db.scalar(
            select(DiagnosticResponse)
            .join(DiagnosticTask)
            .where(DiagnosticTask.session_id == session_id)
        )
        assert response is not None
        assert response.response_text == "opt_k4m2"
        assert response.rubric["submission_encoding"] == "option-id.v1"
        assert {
            result.projection_status
            for result in db.scalars(
                select(DiagnosticResult).where(DiagnosticResult.session_id == session_id)
            )
        } == {"not_projected"}
    assert _progress_snapshot(harness.factory) == before_progress
    _assert_database_integrity(harness)
