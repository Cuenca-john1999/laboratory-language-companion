from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from deutschos_api.core.config import Settings
from deutschos_api.educational_library.dependencies import (
    get_library_editorial,
    get_library_teacher,
)
from deutschos_api.educational_library.editorial import LibraryEditorialService
from deutschos_api.educational_library.schemas import CoreSourceAssignmentRequest
from deutschos_api.educational_library.service import EducationalLibraryService, utc_text
from deutschos_api.main import app
from deutschos_api.models import (
    SkillEvidence,
    StudentSkill,
    StudyEvent,
    StudyPracticalStatus,
    StudyQuestion,
    StudySectionState,
    StudySession,
)
from deutschos_api.study.missions import build_mission, build_plan, resolve_mission_type
from deutschos_api.study.schemas import (
    StudyNoteWrite,
    StudyPositionUpdate,
    StudyQuestionStatusUpdate,
    StudyQuestionWrite,
    StudySessionCreate,
    StudyTransitionRequest,
    WorkbookLinkCreate,
    WorkbookLinkReview,
    WorkbookLinkUpdate,
)
from deutschos_api.study.service import GuidedStudyService


@pytest.fixture
def study_editorial(tmp_path: Path) -> LibraryEditorialService:
    materials = tmp_path / "materials"
    materials.mkdir()
    settings = Settings(
        database_url="sqlite://",
        educational_materials_dir=materials,
        educational_library_runtime_dir=tmp_path / "library-runtime",
        educational_library_scan_on_startup=False,
        educational_library_embedding_model="",
        ollama_model="unused",
    )
    theory_name = "Herder Gramatica Alemana Hispanohablantes.md"
    workbook_name = "Herder Ejercicios y Soluciones.md"
    (materials / theory_name).write_text("# Manual\n\nAlemán básico.", encoding="utf-8")
    (materials / workbook_name).write_text("# Ejercicios\n\nPráctica.", encoding="utf-8")
    library = EducationalLibraryService(settings)
    library.scan()
    editorial = LibraryEditorialService(library.database)
    candidates = {item.suggested_role.value: item.source for item in editorial.candidates()}
    theory = candidates["core_theory"]
    workbook = candidates["core_workbook"]
    editorial.assign_core(
        theory.id,
        CoreSourceAssignmentRequest(
            operation_id="study-assign-theory",
            pedagogical_role="core_theory",
            display_alias="Herder · Gramática alemana para hispanohablantes",
            related_source_id=workbook.id,
        ),
    )
    editorial.assign_core(
        workbook.id,
        CoreSourceAssignmentRequest(
            operation_id="study-assign-workbook",
            pedagogical_role="core_workbook",
            display_alias="Herder · Ejercicios y soluciones",
            related_source_id=theory.id,
        ),
    )
    with library.database.transaction(immediate=True) as connection:
        version_id = connection.execute(
            "SELECT current_version_id FROM sources WHERE id=?", (theory.id,)
        ).fetchone()[0]
        for key, title, topic, start, end, editorial_status in (
            ("topic-001", "Pronombres personales", "Pronombres", 4, 6, "system_suggested"),
            ("topic-002", "El acusativo", "Akkusativ", 7, 10, "user_confirmed"),
            ("topic-003", "Verbos modales", "Modalverben", 11, 14, "system_suggested"),
            ("topic-hidden", "Oculta", "Oculta", 15, 16, "rejected"),
        ):
            connection.execute(
                "INSERT INTO editorial_sections(source_version_id,stable_key,title,page_start,"
                "page_end,topic,content_role,derivation_method,provenance_confidence,"
                "editorial_status,created_at,updated_at) VALUES (?,?,?,?,?,?,"
                "'theory','test-fixture',1,?,?,?)",
                (
                    version_id,
                    key,
                    title,
                    start,
                    end,
                    topic,
                    editorial_status,
                    utc_text(),
                    utc_text(),
                ),
            )
    return editorial


def _service(factory, editorial) -> GuidedStudyService:
    return GuidedStudyService(factory(), editorial)


def test_route_search_and_deterministic_missions(db_session_factory, study_editorial):
    service = _service(db_session_factory, study_editorial)
    path = service.path()
    assert [item.stable_key for item in path.sections] == [
        "topic-001",
        "topic-002",
        "topic-003",
    ]
    assert all(item.practical_status == "not_started" for item in path.sections)
    assert service.path(query="akkusativ").sections[0].title == "El acusativo"
    assert service.recommendation(path=path).section_stable_key == "topic-001"

    mission_types = ["laboratory", "frozen_city", "underwater_exploration", "space_mission"]
    examples = [
        build_mission(kind, concept="Akkusativ", objective="Comprender.") for kind in mission_types
    ]
    assert [item["example_de"] for item in examples] == [
        "Die Technikerin untersucht die Probe.",
        "Die Stadt muss den Generator reparieren.",
        "Die Forschungsstation liegt unter dem Meer.",
        "Wir prüfen gemeinsam das Messgerät.",
    ]
    assert all(item["original_content"] for item in examples)
    assert len(build_plan(planned_minutes=20, objective="x")["steps"]) == 4
    assert len(build_plan(planned_minutes=60, objective="x")["steps"]) == 6
    assert resolve_mission_type(
        "mixed", preferred="standard", stable_seed="same"
    ) == resolve_mission_type("mixed", preferred="laboratory", stable_seed="same")


def test_session_state_machine_restart_idempotency_and_no_projection(
    db_session_factory, study_editorial
):
    service = _service(db_session_factory, study_editorial)
    before = (
        service.db.scalar(select(func.count(StudentSkill.id))),
        service.db.scalar(select(func.count(SkillEvidence.id))),
    )
    request = StudySessionCreate(
        operation_id="start-session-0001",
        section_id=2,
        planned_minutes=30,
        mission_type="laboratory",
        start_origin="concept_search",
    )
    session = service.start(request)
    assert session.status == "active"
    assert session.current_pdf_page == 7
    assert session.mission.type == "laboratory"
    persisted_row = service.db.get(StudySession, session.id)
    persisted_row.resumed_at = persisted_row.resumed_at - timedelta(minutes=2)
    service.db.commit()
    assert service.start(request).id == session.id
    with pytest.raises(Exception, match="activa"):
        service.start(
            StudySessionCreate(
                operation_id="start-session-0002",
                section_id=1,
            )
        )

    moved = service.update_position(
        session.id,
        StudyPositionUpdate(
            operation_id="move-session-0001",
            current_pdf_page=9,
            printed_page_label="7",
            checklist=[{"id": "read", "completed": True}],
        ),
    )
    assert moved.current_pdf_page == 9
    paused = service.transition(
        session.id,
        StudyTransitionRequest(operation_id="pause-session-0001", action="pause"),
    )
    assert paused.status == "paused"
    assert paused.active_seconds >= 120

    service.db.close()
    restarted = _service(db_session_factory, study_editorial)
    persisted = restarted.get_session(session.id)
    assert persisted.status == "paused"
    assert persisted.current_pdf_page == 9
    resumed = restarted.transition(
        session.id,
        StudyTransitionRequest(operation_id="resume-session-0001", action="resume"),
    )
    assert resumed.status == "active"
    resumed_row = restarted.db.get(StudySession, session.id)
    resumed_row.resumed_at = resumed_row.resumed_at - timedelta(minutes=1)
    restarted.db.commit()
    completed = restarted.transition(
        session.id,
        StudyTransitionRequest(
            operation_id="complete-session-0001",
            action="complete",
            subjective_result="needs_review",
            final_pdf_page=9,
            next_action="Repasar la explicación",
        ),
    )
    assert completed.status == "completed"
    assert completed.active_seconds >= 180
    state = restarted.db.scalar(select(StudySectionState))
    assert state.practical_status == StudyPracticalStatus.NEEDS_REVIEW
    assert restarted.recommendation().reason == "Esta sección está marcada para repaso."
    after = (
        restarted.db.scalar(select(func.count(StudentSkill.id))),
        restarted.db.scalar(select(func.count(SkillEvidence.id))),
    )
    assert after == before
    with pytest.raises(Exception, match="cerrada"):
        restarted.transition(
            session.id,
            StudyTransitionRequest(operation_id="resume-session-0002", action="resume"),
        )


def test_only_one_concurrent_session_becomes_active(file_db_session_factory, study_editorial):
    def start(section_id: int) -> str:
        with file_db_session_factory() as db:
            service = GuidedStudyService(db, study_editorial)
            try:
                return service.start(
                    StudySessionCreate(
                        operation_id=f"concurrent-start-{section_id}",
                        section_id=section_id,
                    )
                ).status.value
            except Exception:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(start, (1, 2)))
    assert sorted(outcomes) == ["active", "conflict"]
    with file_db_session_factory() as db:
        assert (
            db.scalar(select(func.count(StudySession.id)).where(StudySession.status == "active"))
            == 1
        )


def test_notes_questions_workbook_audit_and_privacy(db_session_factory, study_editorial):
    service = _service(db_session_factory, study_editorial)
    session = service.start(StudySessionCreate(operation_id="study-data-start", section_id=1))
    note = service.create_note(
        StudyNoteWrite(
            operation_id="study-note-create",
            session_id=session.id,
            section_stable_key="topic-001",
            pdf_page=4,
            text="Recordar la diferencia; esto no se envía automáticamente.",
        )
    )
    assert service.list_notes(session_id=session.id)[0].text == note.text
    question = service.create_question(
        StudyQuestionWrite(
            operation_id="study-question-create",
            session_id=session.id,
            section_stable_key="topic-001",
            pdf_page=4,
            question="¿Cuándo se usa ihr?",
        )
    )
    clarified = service.update_question(
        question.id,
        StudyQuestionStatusUpdate(operation_id="study-question-clarify", status="clarified"),
    )
    assert clarified.status == "clarified"
    reopened = service.update_question(
        question.id,
        StudyQuestionStatusUpdate(operation_id="study-question-reopen", status="open"),
    )
    assert reopened.status == "open"

    link = service.create_workbook_link(
        WorkbookLinkCreate(
            operation_id="study-workbook-create",
            theory_source_id=service.path().source_id,
            theory_section_stable_key="topic-001",
            workbook_pdf_page=8,
            printed_page_label="6",
            exercise_start="1",
            exercise_end="4",
            region="left",
        )
    )
    assert link.status == "candidate"
    unknown = service.review_workbook_link(
        link.id,
        WorkbookLinkReview(operation_id="study-workbook-unknown", action="unknown"),
    )
    assert unknown.status == "candidate"
    corrected = service.update_workbook_link(
        link.id,
        WorkbookLinkUpdate(
            operation_id="study-workbook-correct",
            workbook_pdf_page=9,
            exercise_start="2",
            exercise_end="5",
            region="right",
        ),
    )
    assert corrected.workbook_pdf_page == 9
    assert corrected.status == "candidate"
    confirmed = service.review_workbook_link(
        link.id,
        WorkbookLinkReview(operation_id="study-workbook-confirm", action="confirm"),
    )
    assert confirmed.status == "user_confirmed"
    reverted = service.review_workbook_link(
        link.id,
        WorkbookLinkReview(operation_id="study-workbook-revert", action="revert"),
    )
    assert reverted.status == "candidate"
    assert service.db.scalar(select(func.count(StudyEvent.id))) >= 8


@pytest.mark.anyio
async def test_study_http_contract_and_idempotency(client, study_editorial):
    app.dependency_overrides[get_library_editorial] = lambda: study_editorial
    route = await client.get("/api/study/path")
    assert route.status_code == 200
    assert len(route.json()["sections"]) == 3
    payload = {
        "operation_id": "http-session-create",
        "kind": "guided",
        "section_id": 2,
        "planned_minutes": 30,
        "mission_type": "laboratory",
        "start_origin": "section_picker",
        "activate": True,
    }
    first = await client.post("/api/study/sessions", json=payload)
    assert first.status_code == 201, first.text
    repeated = await client.post("/api/study/sessions", json=payload)
    assert repeated.status_code == 201
    assert repeated.json()["id"] == first.json()["id"]
    conflict = await client.post("/api/study/sessions", json={**payload, "section_id": 1})
    assert conflict.status_code == 409
    session_id = first.json()["id"]
    note = await client.post(
        "/api/study/notes",
        json={
            "operation_id": "http-note-create",
            "session_id": session_id,
            "text": "Privada",
        },
    )
    assert note.status_code == 201
    public_session = (await client.get(f"/api/study/sessions/{session_id}")).json()
    assert "notes" not in public_session
    assert "correct_answer" not in str(public_session).casefold()
    paused = await client.post(
        f"/api/study/sessions/{session_id}/transition",
        json={"operation_id": "http-session-pause", "action": "pause"},
    )
    assert paused.status_code == 200
    invalid = await client.put(
        f"/api/study/sessions/{session_id}/position",
        json={"operation_id": "http-position-invalid", "current_pdf_page": 999},
    )
    assert invalid.status_code == 422

    class UnavailableTeacher:
        async def ask(self, *_args, **_kwargs):
            raise RuntimeError("private provider detail")

    app.dependency_overrides[get_library_teacher] = lambda: UnavailableTeacher()
    teacher_failure = await client.post(
        f"/api/study/sessions/{session_id}/teacher",
        json={"action": "explain", "include_note_ids": []},
    )
    assert teacher_failure.status_code == 503
    assert "private provider detail" not in teacher_failure.text
    assert (await client.get(f"/api/study/sessions/{session_id}")).json()["status"] == "paused"
    app.dependency_overrides.pop(get_library_teacher, None)
    app.dependency_overrides.pop(get_library_editorial, None)


def test_delete_all_requires_explicit_service_call_and_keeps_progress(
    db_session_factory, study_editorial
):
    service = _service(db_session_factory, study_editorial)
    service.start(StudySessionCreate(operation_id="delete-all-session", section_id=1))
    service.create_question(
        StudyQuestionWrite(operation_id="delete-all-question", question="¿Por qué?")
    )
    before = (
        service.db.scalar(select(func.count(StudentSkill.id))),
        service.db.scalar(select(func.count(SkillEvidence.id))),
    )
    deleted = service.delete_all("delete-all-confirmed")
    assert deleted.deleted_sessions == 1
    assert service.db.scalar(select(func.count(StudySession.id))) == 0
    assert service.db.scalar(select(func.count(StudyQuestion.id))) == 0
    assert (
        service.db.scalar(select(func.count(StudentSkill.id))),
        service.db.scalar(select(func.count(SkillEvidence.id))),
    ) == before
