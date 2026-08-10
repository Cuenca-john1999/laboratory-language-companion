from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from llc_api.core.config import Settings
from llc_api.educational_library.dependencies import (
    get_library_editorial,
    get_library_teacher,
)
from llc_api.educational_library.editorial import LibraryEditorialService
from llc_api.educational_library.schemas import CoreSourceAssignmentRequest
from llc_api.educational_library.service import (
    EducationalLibraryService,
    json_dump,
    utc_text,
)
from llc_api.main import app
from llc_api.models import (
    SkillEvidence,
    StudentSkill,
    StudyEvent,
    StudyNote,
    StudyPracticalStatus,
    StudyPreference,
    StudyQuestion,
    StudySectionState,
    StudySession,
)
from llc_api.providers.dependencies import get_model_provider
from llc_api.study.dependencies import get_study_service
from llc_api.study.missions import build_mission, build_plan, resolve_mission_type
from llc_api.study.schemas import (
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
from llc_api.study.service import GuidedStudyService


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
        lm_studio_model="unused",
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


def _activate_canonical_route(editorial: LibraryEditorialService) -> None:
    validation = {
        "valid": True,
        "theme_count": 51,
        "missing_theme_numbers": [],
        "duplicate_theme_numbers": [],
        "sequence_monotonic": True,
        "printed_pages_monotonic": True,
        "canonical_checks": {},
        "errors": [],
        "warnings": [],
    }
    with editorial.database.transaction(immediate=True) as connection:
        source = connection.execute(
            "SELECT * FROM sources WHERE pedagogical_role='core_theory' AND user_selected_core=1"
        ).fetchone()
        connection.execute(
            "INSERT INTO canonical_route_imports(id,source_id,source_version_id,route_version,"
            "reference_name,reference_sha256,reference_size_bytes,reference_page_count,model,"
            "parser_version,status,editorial_status,origin,active,validation_json,warnings_json,"
            "statistics_json,imported_at,validated_at) VALUES ('study-canonical',?,?,?,?,?,?,?,?,"
            "?,'validated','system_verified','user_reference_index',1,?,'[]','{}',?,?)",
            (
                source["id"],
                source["current_version_id"],
                1,
                "Herder_Index_verified.pdf",
                "a" * 64,
                100,
                17,
                "fixture",
                "fixture.v1",
                json_dump(validation),
                utc_text(),
                utc_text(),
            ),
        )
        titles = {
            9: ("El pretérito perfecto", "Das Perfekt"),
            22: ("La declinación del sustantivo", "Die Deklination des Substantivs"),
            28: ("Los pronombres personales", "Personalpronomen"),
            40: ("La negación", "Die Negation"),
            51: ("Oraciones subordinadas finales", "Finalsätze"),
        }
        for number in range(1, 52):
            title_es, title_de = titles.get(number, (f"Tema limpio {number}", None))
            cursor = connection.execute(
                "INSERT INTO canonical_topics(import_id,source_id,source_version_id,stable_key,"
                "theme_number,title_es,title_de,printed_start,printed_end,printed_end_origin,"
                "reference_pdf_page,reference_visual_region,editorial_status,origin,confidence,"
                "created_at,updated_at) VALUES ('study-canonical',?,?,?,?,?,?,?,?,"
                "'calculated',?,'unknown','system_verified','user_reference_index',1,?,?)",
                (
                    source["id"],
                    source["current_version_id"],
                    f"herder:{source['id']}:v{source['current_version_id']}:theme:{number:02d}",
                    number,
                    title_es,
                    title_de,
                    26 + number,
                    26 + number,
                    min(17, (number - 1) // 3 + 1),
                    utc_text(),
                    utc_text(),
                ),
            )
            if number == 22:
                connection.execute(
                    "INSERT INTO canonical_outline_nodes(canonical_topic_id,sort_key,"
                    "hierarchy_level,local_number,title_es,title_de,printed_page,"
                    "reference_pdf_page,visual_region,parse_status,manual_pdf_page,"
                    "manual_scan_layout,manual_region,editorial_status,origin,confidence,"
                    "raw_visible_text,created_at,updated_at) VALUES (?,1,'subsection','2',"
                    "'El acusativo (complemento directo)','Akkusativ',162,7,'unknown',"
                    "'verified',89,'double_page','unknown','user_confirmed',"
                    "'user_reference_index',1,'El acusativo',?,?)",
                    (cursor.lastrowid, utc_text(), utc_text()),
                )


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


def test_canonical_route_replaces_legacy_display_without_deleting_it(
    db_session_factory, study_editorial
):
    _activate_canonical_route(study_editorial)
    service = _service(db_session_factory, study_editorial)
    path = service.path()
    assert len(path.sections) == 51
    assert [item.theme_number for item in path.sections] == list(range(1, 52))
    assert path.sections[8].title_de == "Das Perfekt"
    accusative = service.path(query="Akkusativ").sections[0]
    assert accusative.theme_number == 22
    assert accusative.outline[0].manual_pdf_page == 89
    assert accusative.outline[0].manual_scan_layout == "double_page"
    assert accusative.outline[0].manual_region == "unknown"
    before = (
        service.db.scalar(select(func.count(StudentSkill.id))),
        service.db.scalar(select(func.count(SkillEvidence.id))),
    )
    session = service.start(
        StudySessionCreate(
            operation_id="canonical-study-start",
            section_id=accusative.id,
        )
    )
    assert session.section_title == "La declinación del sustantivo"
    assert (
        service.db.scalar(select(func.count(StudentSkill.id))),
        service.db.scalar(select(func.count(SkillEvidence.id))),
    ) == before
    with study_editorial.database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM editorial_sections").fetchone()[0] == 4
    with study_editorial.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE canonical_topics SET editorial_status='rejected' "
            "WHERE import_id='study-canonical' AND theme_number=1"
        )
    refreshed = service.path()
    assert len(refreshed.sections) == 51
    assert refreshed.sections[0].editorial_status == "rejected"


@pytest.mark.anyio
async def test_local_study_reads_are_empty_ai_independent_and_side_effect_free(
    client, db_session_factory, study_editorial
):
    _activate_canonical_route(study_editorial)

    def unexpected_ai_dependency():
        raise AssertionError("local Study reads must not initialize an AI provider")

    app.dependency_overrides[get_library_editorial] = lambda: study_editorial
    app.dependency_overrides[get_library_teacher] = unexpected_ai_dependency
    app.dependency_overrides[get_model_provider] = unexpected_ai_dependency
    with db_session_factory() as db:
        before = (
            db.scalar(select(func.count(StudySession.id))),
            db.scalar(select(func.count(StudyPreference.profile_id))),
            db.scalar(select(func.count(StudentSkill.id))),
            db.scalar(select(func.count(SkillEvidence.id))),
        )

    route = await client.get("/api/study/path")
    dashboard = await client.get("/api/study/dashboard")
    history = await client.get("/api/study/history")

    assert route.status_code == 200
    assert len(route.json()["sections"]) == 51
    assert dashboard.status_code == 200
    assert dashboard.json()["total_sections"] == 51
    assert dashboard.json()["active_session"] is None
    assert dashboard.json()["recent_sessions"] == []
    assert dashboard.json()["open_questions"] == 0
    assert history.status_code == 200
    assert history.json() == []
    with db_session_factory() as db:
        after = (
            db.scalar(select(func.count(StudySession.id))),
            db.scalar(select(func.count(StudyPreference.profile_id))),
            db.scalar(select(func.count(StudentSkill.id))),
            db.scalar(select(func.count(SkillEvidence.id))),
        )
    assert after == before == (0, 0, 0, 0)


@pytest.mark.anyio
async def test_new_sessions_persist_the_complete_mission_contract(
    client, db_session_factory, study_editorial
):
    service = _service(db_session_factory, study_editorial)
    created = service.start(
        StudySessionCreate(operation_id="current-contract-session", section_id=1)
    )
    assert created.mission.verifiable_task
    with db_session_factory() as db:
        persisted = db.get(StudySession, created.id)
        assert persisted.mission["verifiable_task"] == created.mission.verifiable_task

    app.dependency_overrides[get_library_editorial] = lambda: study_editorial

    dashboard = await client.get("/api/study/dashboard")
    history = await client.get("/api/study/history")

    assert dashboard.status_code == 200
    assert history.status_code == 200
    assert history.json()[0]["mission"]["verifiable_task"] == created.mission.verifiable_task


@pytest.mark.anyio
async def test_real_study_database_error_is_logged_and_not_returned_as_empty(client, caplog):
    class BrokenStudyService:
        def history(self, **_filters):
            raise OperationalError(
                "SELECT study_sessions",
                {},
                RuntimeError("/private/runtime/database.sqlite3"),
            )

    app.dependency_overrides[get_study_service] = lambda: BrokenStudyService()
    caplog.set_level(logging.ERROR, logger="llc_api.api.study")

    response = await client.get("/api/study/history")

    assert response.status_code == 500
    assert response.json() == {"detail": "No se pudieron leer los datos locales de estudio."}
    assert "private/runtime" not in response.text
    assert "OperationalError" in caplog.text


def test_exact_legacy_mapping_surfaces_personal_state_without_rewriting_it(
    db_session_factory, study_editorial
):
    service = _service(db_session_factory, study_editorial)
    session = service.start(
        StudySessionCreate(operation_id="legacy-personal-session", section_id=1)
    )
    note = service.create_note(
        StudyNoteWrite(
            operation_id="legacy-personal-note",
            session_id=session.id,
            section_stable_key="topic-001",
            text="Dato personal conservado.",
        )
    )
    service.create_question(
        StudyQuestionWrite(
            operation_id="legacy-personal-question",
            session_id=session.id,
            section_stable_key="topic-001",
            question="¿Se conserva esta duda?",
        )
    )
    link = service.create_workbook_link(
        WorkbookLinkCreate(
            operation_id="legacy-personal-workbook",
            theory_source_id=service.path().source_id,
            theory_section_stable_key="topic-001",
            workbook_pdf_page=8,
        )
    )
    _activate_canonical_route(study_editorial)
    with study_editorial.database.transaction(immediate=True) as connection:
        legacy_id = connection.execute(
            "SELECT id FROM editorial_sections WHERE stable_key='topic-001'"
        ).fetchone()[0]
        canonical_id = connection.execute(
            "SELECT id FROM canonical_topics WHERE import_id='study-canonical' AND theme_number=1"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO canonical_legacy_mappings(import_id,legacy_section_id,"
            "canonical_topic_id,mapping_status,score,evidence_json,reason,created_at,updated_at) "
            "VALUES ('study-canonical',?,?,'exact',1,'[]','fixture',?,?)",
            (legacy_id, canonical_id, utc_text(), utc_text()),
        )

    canonical = service.path().sections[0]
    assert canonical.theme_number == 1
    assert canonical.practical_status == "in_progress"
    assert canonical.last_session_id == session.id
    assert canonical.open_questions == 1
    assert canonical.workbook_link.id == link.id
    assert service.get_session(session.id).section_title == "Tema limpio 1"
    assert service.list_notes(session_id=session.id)[0].id == note.id


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


def test_session_deletion_rolls_back_if_a_transaction_step_fails(
    db_session_factory, study_editorial
):
    service = _service(db_session_factory, study_editorial)
    first = service.start(
        StudySessionCreate(
            operation_id="rollback-session-one",
            section_id=1,
            activate=False,
        )
    )
    second = service.start(
        StudySessionCreate(
            operation_id="rollback-session-two",
            section_id=2,
            activate=False,
        )
    )

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("forced audit failure")

    service._record_event = fail_event
    with pytest.raises(RuntimeError, match="forced audit failure"):
        service.delete_sessions(
            [first.id, second.id],
            "rollback-delete-many",
        )

    assert service.db.scalar(select(func.count(StudySession.id))) == 2


@pytest.mark.anyio
async def test_session_management_is_atomic_and_preserves_learning_memory(
    client, db_session_factory, study_editorial
):
    _activate_canonical_route(study_editorial)
    app.dependency_overrides[get_library_editorial] = lambda: study_editorial
    service = _service(db_session_factory, study_editorial)
    first = service.start(
        StudySessionCreate(
            operation_id="manage-session-one",
            section_id=1,
            activate=False,
        )
    )
    second = service.start(
        StudySessionCreate(
            operation_id="manage-session-two",
            section_id=2,
            activate=False,
        )
    )
    service.create_note(
        StudyNoteWrite(
            operation_id="manage-note-one",
            session_id=first.id,
            text="Conservar esta nota.",
        )
    )
    service.create_question(
        StudyQuestionWrite(
            operation_id="manage-question-two",
            session_id=second.id,
            question="¿Se conserva esta duda?",
        )
    )
    service.db.close()

    attempt = await client.post(
        "/api/learning/attempts",
        json={
            "submission_id": "17b829be-28c7-44dd-bd38-fba167249295",
            "skill_code": "grammar.personal_pronouns",
            "source": "manual_assessment",
            "exercise_type": "recognition",
            "prompt": "Selecciona el pronombre.",
            "student_answer": "sie",
            "expected_answer": "sie",
            "outcome": "correct_without_help",
            "feedback": "Correcto.",
            "corrects_submission_id": None,
        },
    )
    assert attempt.status_code == 201

    overview = await client.get("/api/study/data")
    assert overview.status_code == 200
    assert overview.json()["total_sessions"] == 2
    assert len(overview.json()["sessions"]) == 2
    assert overview.json()["memory"] == {
        "route_topics": 51,
        "started_topics": 0,
        "student_skills": 1,
        "skill_evidence": 1,
        "saved_notes": 1,
        "saved_questions": 1,
        "active_session_id": None,
        "preferences_persisted": True,
        "mission_preference": "automatic",
    }

    missing_id = "e46aeb0a-0947-460b-869a-3c4360df863b"
    missing = await client.request(
        "DELETE",
        f"/api/study/sessions/{missing_id}",
        json={"operation_id": "missing-session-delete", "confirmation": "BORRAR"},
    )
    assert missing.status_code == 404

    partial = await client.request(
        "DELETE",
        "/api/study/sessions",
        json={
            "operation_id": "atomic-session-delete",
            "confirmation": "BORRAR",
            "session_ids": [first.id, missing_id],
        },
    )
    assert partial.status_code == 404
    with db_session_factory() as db:
        assert db.scalar(select(func.count(StudySession.id))) == 2

    deleted = await client.request(
        "DELETE",
        "/api/study/sessions",
        json={
            "operation_id": "selected-session-delete",
            "confirmation": "BORRAR",
            "session_ids": [first.id, second.id],
        },
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted_sessions": 2}
    with db_session_factory() as db:
        assert db.scalar(select(func.count(StudySession.id))) == 0
        assert db.scalar(select(func.count(StudentSkill.id))) == 1
        assert db.scalar(select(func.count(SkillEvidence.id))) == 1
        notes = db.scalars(select(StudyNote)).all()
        questions = db.scalars(select(StudyQuestion)).all()
        assert len(notes) == len(questions) == 1
        assert notes[0].session_id is None
        assert questions[0].session_id is None

    empty = await client.get("/api/study/data")
    assert empty.status_code == 200
    assert empty.json()["sessions"] == []
    assert empty.json()["memory"]["student_skills"] == 1
    assert empty.json()["memory"]["skill_evidence"] == 1
    assert empty.json()["memory"]["saved_notes"] == 1
    assert empty.json()["memory"]["saved_questions"] == 1


@pytest.mark.anyio
async def test_clear_session_history_is_safe_when_empty(
    client, db_session_factory, study_editorial
):
    app.dependency_overrides[get_library_editorial] = lambda: study_editorial
    service = _service(db_session_factory, study_editorial)
    service.start(
        StudySessionCreate(
            operation_id="clear-session-one",
            section_id=1,
            activate=False,
        )
    )
    service.start(
        StudySessionCreate(
            operation_id="clear-session-two",
            section_id=2,
            activate=False,
        )
    )
    service.db.close()

    cleared = await client.request(
        "DELETE",
        "/api/study/sessions/all",
        json={
            "operation_id": "clear-session-history",
            "confirmation": "VACIAR HISTORIAL",
        },
    )
    assert cleared.status_code == 200
    assert cleared.json() == {"deleted_sessions": 2}

    empty = await client.request(
        "DELETE",
        "/api/study/sessions/all",
        json={
            "operation_id": "clear-empty-history",
            "confirmation": "VACIAR HISTORIAL",
        },
    )
    assert empty.status_code == 200
    assert empty.json() == {"deleted_sessions": 0}
    with db_session_factory() as db:
        assert db.scalar(select(func.count(StudySession.id))) == 0
