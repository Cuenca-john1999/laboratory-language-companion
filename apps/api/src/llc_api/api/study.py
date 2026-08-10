from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from llc_api.db.session import get_db
from llc_api.educational_library.dependencies import get_library_teacher
from llc_api.educational_library.schemas import (
    StudyTeacherContext,
    TeacherAskRequest,
    TeacherQueryRead,
)
from llc_api.educational_library.teacher import (
    EducationalTeacherService,
    learner_context_from_db,
)
from llc_api.models import StudyMissionType, StudySessionStatus
from llc_api.study.dependencies import get_study_service
from llc_api.study.exceptions import (
    StudyConflictError,
    StudyContractError,
    StudyLibraryUnavailableError,
    StudyNotFoundError,
)
from llc_api.study.schemas import (
    StudyDashboardRead,
    StudyDataRead,
    StudyDeleteRequest,
    StudyNoteRead,
    StudyNoteWrite,
    StudyPathRead,
    StudyPositionUpdate,
    StudyPreferenceRead,
    StudyPreferenceUpdate,
    StudyQuestionRead,
    StudyQuestionStatusUpdate,
    StudyQuestionWrite,
    StudyQuickActionRequest,
    StudySectionRead,
    StudySectionStateUpdate,
    StudySessionBulkDeleteRequest,
    StudySessionClearRequest,
    StudySessionCreate,
    StudySessionDeleteRead,
    StudySessionRead,
    StudyTransitionRequest,
    WorkbookLinkCreate,
    WorkbookLinkRead,
    WorkbookLinkReview,
    WorkbookLinkUpdate,
)
from llc_api.study.service import GuidedStudyService

router = APIRouter(prefix="/api/study", tags=["guided-study"])
logger = logging.getLogger(__name__)


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, StudyNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, StudyConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, StudyContractError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    if isinstance(exc, StudyLibraryUnavailableError):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    logger.exception("local Study request failed: %s", type(exc).__name__)
    return HTTPException(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "No se pudieron leer los datos locales de estudio.",
    )


@router.get("/dashboard", response_model=StudyDashboardRead)
def dashboard(service: GuidedStudyService = Depends(get_study_service)) -> StudyDashboardRead:
    try:
        return service.dashboard()
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/path", response_model=StudyPathRead)
def path(
    query: str | None = Query(default=None, max_length=200),
    service: GuidedStudyService = Depends(get_study_service),
) -> StudyPathRead:
    try:
        return service.path(query=query)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/sections/{section_id}", response_model=StudySectionRead)
def section(
    section_id: int, service: GuidedStudyService = Depends(get_study_service)
) -> StudySectionRead:
    try:
        return service.section(section_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.put("/sections/{section_id}/state", response_model=StudySectionRead)
def update_section_state(
    section_id: int,
    request: StudySectionStateUpdate,
    service: GuidedStudyService = Depends(get_study_service),
) -> StudySectionRead:
    try:
        return service.update_section_state(section_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/sessions", response_model=StudySessionRead, status_code=status.HTTP_201_CREATED)
def start_session(
    request: StudySessionCreate,
    service: GuidedStudyService = Depends(get_study_service),
) -> StudySessionRead:
    try:
        return service.start(request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.delete("/sessions", response_model=StudySessionDeleteRead)
def delete_sessions(
    request: StudySessionBulkDeleteRequest,
    service: GuidedStudyService = Depends(get_study_service),
) -> StudySessionDeleteRead:
    try:
        return service.delete_sessions(
            [str(session_id) for session_id in request.session_ids],
            request.operation_id,
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.delete("/sessions/all", response_model=StudySessionDeleteRead)
def clear_sessions(
    request: StudySessionClearRequest,
    service: GuidedStudyService = Depends(get_study_service),
) -> StudySessionDeleteRead:
    try:
        return service.clear_sessions(request.operation_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/sessions/{session_id}", response_model=StudySessionRead)
def get_session(
    session_id: str, service: GuidedStudyService = Depends(get_study_service)
) -> StudySessionRead:
    try:
        return service.get_session(session_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/sessions/{session_id}/transition", response_model=StudySessionRead)
def transition_session(
    session_id: str,
    request: StudyTransitionRequest,
    service: GuidedStudyService = Depends(get_study_service),
) -> StudySessionRead:
    try:
        return service.transition(session_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.put("/sessions/{session_id}/position", response_model=StudySessionRead)
def update_position(
    session_id: str,
    request: StudyPositionUpdate,
    service: GuidedStudyService = Depends(get_study_service),
) -> StudySessionRead:
    try:
        return service.update_position(session_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


_QUICK_ACTION_QUESTIONS = {
    "explain": "Explícame esta parte de forma breve y clara.",
    "summarize": "Resume brevemente la regla de esta sección.",
    "another_example": "Dame otro ejemplo alemán natural de esta regla.",
    "compare_spanish": "Compara esta regla con el español de España.",
    "why_form": "¿Por qué se usa esta forma aquí?",
    "laboratory_example": "Dame un ejemplo original de laboratorio para esta regla.",
    "thematic_example": "Dame un ejemplo original adaptado a la temática de la sesión.",
    "locate_rule": "Localiza esta regla en la fuente principal.",
    "not_understood": "No lo he entendido. Explícalo de otra manera y con un ejemplo sencillo.",
}


@router.post("/sessions/{session_id}/teacher", response_model=TeacherQueryRead)
async def ask_teacher_in_session(
    session_id: str,
    request: StudyQuickActionRequest,
    service: GuidedStudyService = Depends(get_study_service),
    teacher: EducationalTeacherService = Depends(get_library_teacher),
    db: Session = Depends(get_db),
) -> TeacherQueryRead:
    try:
        session = service.get_session(session_id)
        notes_by_id = {note.id: note.text for note in service.list_notes(session_id=session_id)}
        if any(note_id not in notes_by_id for note_id in request.include_note_ids):
            raise StudyContractError("Una nota seleccionada no pertenece a esta sesión.")
        selected_notes = [notes_by_id[note_id] for note_id in request.include_note_ids]
        question = request.question or _QUICK_ACTION_QUESTIONS[request.action]
        teacher_request = TeacherAskRequest(
            question=question,
            conversation_id=request.conversation_id,
            source_id=session.source_id,
            study_context=StudyTeacherContext(
                session_id=session.id,
                section_title=session.section_title,
                concept_name=session.concept_name,
                source_name=session.source_name,
                pdf_page_start=session.pdf_page_start,
                pdf_page_end=session.pdf_page_end,
                current_pdf_page=session.current_pdf_page,
                printed_page_label=session.printed_page_label,
                mission_type=session.mission.type.value,
                selected_notes=selected_notes,
            ),
        )
        return await teacher.ask(teacher_request, learner=learner_context_from_db(db))
    except Exception as exc:
        if isinstance(exc, (StudyNotFoundError, StudyContractError, StudyConflictError)):
            raise _translate(exc) from exc
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "El profesor local no está disponible; tu sesión continúa intacta.",
        ) from exc


@router.get("/history", response_model=list[StudySessionRead])
def history(
    session_status: StudySessionStatus | None = Query(default=None, alias="status"),
    mission_type: StudyMissionType | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    service: GuidedStudyService = Depends(get_study_service),
) -> list[StudySessionRead]:
    try:
        return service.history(status=session_status, mission_type=mission_type, limit=limit)
    except Exception as exc:
        raise _translate(exc) from exc


@router.delete("/sessions/{session_id}", response_model=StudySessionDeleteRead)
def delete_session(
    session_id: UUID,
    request: StudyDeleteRequest,
    service: GuidedStudyService = Depends(get_study_service),
) -> StudySessionDeleteRead:
    try:
        return service.delete_session(str(session_id), request.operation_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/notes", response_model=list[StudyNoteRead])
def notes(
    session_id: str | None = Query(default=None, max_length=36),
    service: GuidedStudyService = Depends(get_study_service),
) -> list[StudyNoteRead]:
    return service.list_notes(session_id=session_id)


@router.post("/notes", response_model=StudyNoteRead, status_code=status.HTTP_201_CREATED)
def create_note(
    request: StudyNoteWrite,
    service: GuidedStudyService = Depends(get_study_service),
) -> StudyNoteRead:
    try:
        return service.create_note(request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.put("/notes/{note_id}", response_model=StudyNoteRead)
def update_note(
    note_id: str,
    request: StudyNoteWrite,
    service: GuidedStudyService = Depends(get_study_service),
) -> StudyNoteRead:
    try:
        return service.update_note(note_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    note_id: str,
    request: StudyDeleteRequest,
    service: GuidedStudyService = Depends(get_study_service),
) -> Response:
    try:
        service.delete_note(note_id, request.operation_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/questions", response_model=list[StudyQuestionRead])
def questions(
    question_status: str | None = Query(default=None, alias="status", max_length=20),
    session_id: str | None = Query(default=None, max_length=36),
    service: GuidedStudyService = Depends(get_study_service),
) -> list[StudyQuestionRead]:
    return service.list_questions(status=question_status, session_id=session_id)


@router.post("/questions", response_model=StudyQuestionRead, status_code=status.HTTP_201_CREATED)
def create_question(
    request: StudyQuestionWrite,
    service: GuidedStudyService = Depends(get_study_service),
) -> StudyQuestionRead:
    try:
        return service.create_question(request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.put("/questions/{question_id}", response_model=StudyQuestionRead)
def update_question(
    question_id: str,
    request: StudyQuestionStatusUpdate,
    service: GuidedStudyService = Depends(get_study_service),
) -> StudyQuestionRead:
    try:
        return service.update_question(question_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_question(
    question_id: str,
    request: StudyDeleteRequest,
    service: GuidedStudyService = Depends(get_study_service),
) -> Response:
    try:
        service.delete_question(question_id, request.operation_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/workbook-links", response_model=list[WorkbookLinkRead])
def workbook_links(
    section_stable_key: str | None = Query(default=None, max_length=100),
    service: GuidedStudyService = Depends(get_study_service),
) -> list[WorkbookLinkRead]:
    return service.list_workbook_links(section_stable_key=section_stable_key)


@router.post(
    "/workbook-links", response_model=WorkbookLinkRead, status_code=status.HTTP_201_CREATED
)
def create_workbook_link(
    request: WorkbookLinkCreate,
    service: GuidedStudyService = Depends(get_study_service),
) -> WorkbookLinkRead:
    try:
        return service.create_workbook_link(request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/workbook-links/{link_id}/review", response_model=WorkbookLinkRead)
def review_workbook_link(
    link_id: str,
    request: WorkbookLinkReview,
    service: GuidedStudyService = Depends(get_study_service),
) -> WorkbookLinkRead:
    try:
        return service.review_workbook_link(link_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.put("/workbook-links/{link_id}", response_model=WorkbookLinkRead)
def update_workbook_link(
    link_id: str,
    request: WorkbookLinkUpdate,
    service: GuidedStudyService = Depends(get_study_service),
) -> WorkbookLinkRead:
    try:
        return service.update_workbook_link(link_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/preferences", response_model=StudyPreferenceRead)
def preferences(
    service: GuidedStudyService = Depends(get_study_service),
) -> StudyPreferenceRead:
    return service.preferences()


@router.put("/preferences", response_model=StudyPreferenceRead)
def update_preferences(
    request: StudyPreferenceUpdate,
    service: GuidedStudyService = Depends(get_study_service),
) -> StudyPreferenceRead:
    try:
        return service.update_preferences(request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/data", response_model=StudyDataRead)
def study_data(
    service: GuidedStudyService = Depends(get_study_service),
) -> StudyDataRead:
    try:
        return service.data_overview()
    except Exception as exc:
        raise _translate(exc) from exc
