from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from deutschos_api.db.session import get_db
from deutschos_api.educational_library.dependencies import (
    get_library_knowledge,
    get_library_search,
    get_library_service,
    get_library_teacher,
)
from deutschos_api.educational_library.knowledge import EducationalKnowledgeService
from deutschos_api.educational_library.schemas import (
    ChunkRead,
    GroundedGenerationRead,
    GroundedGenerationRequest,
    InventoryReport,
    JobRead,
    KnowledgeGenerationRequest,
    KnowledgeKind,
    KnowledgeReviewRequest,
    KnowledgeStatus,
    KnowledgeUnitRead,
    LibraryBusyError,
    LibraryContractError,
    LibraryNotFoundError,
    LibraryProviderUnavailableError,
    LibrarySummary,
    ScanRequest,
    SearchResponse,
    SourceKind,
    SourceRead,
    SourceStatus,
    SourceUpdateRequest,
    SourceVersionRead,
    TeacherAskRequest,
    TeacherConversationSummary,
    TeacherQueryRead,
)
from deutschos_api.educational_library.search import EducationalSearchService
from deutschos_api.educational_library.service import EducationalLibraryService
from deutschos_api.educational_library.teacher import (
    EducationalTeacherService,
    learner_context_from_db,
)
from deutschos_api.providers.base import ModelProvider
from deutschos_api.providers.dependencies import get_model_provider

router = APIRouter(prefix="/api/library", tags=["educational-library"])


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, LibraryNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, LibraryBusyError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, LibraryContractError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "La consulta no cumple el contrato de la biblioteca.",
        )
    if isinstance(exc, LibraryProviderUnavailableError):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "La capacidad local solicitada no está disponible.",
        )
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "La biblioteca educativa local no está disponible.",
    )


async def _ollama_available(provider: ModelProvider) -> bool:
    try:
        return await provider.health_check()
    except Exception:
        return False


@router.get("/status", response_model=LibrarySummary)
async def library_status(
    service: EducationalLibraryService = Depends(get_library_service),
    provider: ModelProvider = Depends(get_model_provider),
) -> LibrarySummary:
    try:
        return service.summary(ollama_available=await _ollama_available(provider))
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/inventory", response_model=InventoryReport)
def inventory(service: EducationalLibraryService = Depends(get_library_service)) -> InventoryReport:
    try:
        return service.latest_inventory()
    except Exception as exc:
        raise _translate(exc) from exc


def _run_scan(service: EducationalLibraryService, job_id: str, process_documents: bool) -> None:
    service.scan(process_documents=process_documents, job_id=job_id)


@router.post("/scan", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
def start_scan(
    request: ScanRequest,
    background: BackgroundTasks,
    service: EducationalLibraryService = Depends(get_library_service),
) -> JobRead:
    try:
        job = service.create_job("scan", payload={"process_documents": request.process_documents})
        background.add_task(_run_scan, service, job.id, request.process_documents)
        return job
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/process", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
def process_pending(
    background: BackgroundTasks,
    service: EducationalLibraryService = Depends(get_library_service),
) -> JobRead:
    try:
        job = service.create_job("scan", payload={"process_documents": True})
        background.add_task(_run_scan, service, job.id, True)
        return job
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/jobs", response_model=list[JobRead])
def jobs(
    limit: int = Query(default=50, ge=1, le=100),
    service: EducationalLibraryService = Depends(get_library_service),
) -> list[JobRead]:
    try:
        return service.list_jobs(limit=limit)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/jobs/{job_id}", response_model=JobRead)
def job(job_id: str, service: EducationalLibraryService = Depends(get_library_service)) -> JobRead:
    try:
        return service.get_job(job_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/jobs/{job_id}/cancel", response_model=JobRead)
def cancel_job(
    job_id: str, service: EducationalLibraryService = Depends(get_library_service)
) -> JobRead:
    try:
        return service.cancel_job(job_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/jobs/{job_id}/pause", response_model=JobRead)
def pause_job(
    job_id: str, service: EducationalLibraryService = Depends(get_library_service)
) -> JobRead:
    try:
        return service.pause_job(job_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/jobs/{job_id}/retry", response_model=JobRead)
def retry_job(
    job_id: str,
    background: BackgroundTasks,
    service: EducationalLibraryService = Depends(get_library_service),
) -> JobRead:
    try:
        payload = service.job_payload(job_id)
        retried = service.retry_job(job_id)
        background.add_task(
            _run_scan,
            service,
            retried.id,
            bool(payload.get("process_documents", True)),
        )
        return retried
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/sources", response_model=list[SourceRead])
def sources(
    source_status: SourceStatus | None = Query(default=None, alias="status"),
    kind: SourceKind | None = Query(default=None),
    query: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    service: EducationalLibraryService = Depends(get_library_service),
) -> list[SourceRead]:
    try:
        return service.list_sources(
            status=source_status,
            kind=kind,
            query=query,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/sources/{source_id}", response_model=SourceRead)
def source(
    source_id: str, service: EducationalLibraryService = Depends(get_library_service)
) -> SourceRead:
    try:
        return service.get_source(source_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/sources/{source_id}/versions", response_model=list[SourceVersionRead])
def source_versions(
    source_id: str, service: EducationalLibraryService = Depends(get_library_service)
) -> list[SourceVersionRead]:
    try:
        return service.source_versions(source_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/sources/{source_id}/chunks", response_model=list[ChunkRead])
def source_chunks(
    source_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    service: EducationalLibraryService = Depends(get_library_service),
) -> list[ChunkRead]:
    try:
        return service.source_chunks(source_id, limit=limit, offset=offset)
    except Exception as exc:
        raise _translate(exc) from exc


@router.put("/sources/{source_id}", response_model=SourceRead)
def update_source(
    source_id: str,
    update: SourceUpdateRequest,
    service: EducationalLibraryService = Depends(get_library_service),
) -> SourceRead:
    try:
        return service.update_source(source_id, update)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/sources/{source_id}/exclude", response_model=SourceRead)
def exclude_source(
    source_id: str, service: EducationalLibraryService = Depends(get_library_service)
) -> SourceRead:
    try:
        return service.exclude_source(source_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/sources/{source_id}/reprocess", response_model=SourceRead)
def reprocess_source(
    source_id: str, service: EducationalLibraryService = Depends(get_library_service)
) -> SourceRead:
    try:
        return service.reprocess_source(source_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/search", response_model=SearchResponse)
async def search(
    query: str = Query(min_length=2, max_length=1_000),
    mode: Literal["lexical", "semantic", "hybrid"] = "lexical",
    language: str | None = Query(default=None, max_length=20),
    level: str | None = Query(default=None, max_length=20),
    source_id: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=50),
    search_service: EducationalSearchService = Depends(get_library_search),
) -> SearchResponse:
    try:
        return await search_service.search(
            query,
            mode=mode,
            language=language,
            level=level,
            source_id=source_id,
            limit=limit,
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/ask", response_model=TeacherQueryRead)
async def ask_library(
    request: TeacherAskRequest,
    teacher: EducationalTeacherService = Depends(get_library_teacher),
    db: Session = Depends(get_db),
) -> TeacherQueryRead:
    try:
        return await teacher.ask(request, learner=learner_context_from_db(db))
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/queries/{query_id}", response_model=TeacherQueryRead)
def teacher_query(
    query_id: str,
    teacher: EducationalTeacherService = Depends(get_library_teacher),
) -> TeacherQueryRead:
    try:
        return teacher.get_query(query_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/conversations", response_model=list[TeacherConversationSummary])
def teacher_conversations(
    limit: int = Query(default=20, ge=1, le=50),
    teacher: EducationalTeacherService = Depends(get_library_teacher),
) -> list[TeacherConversationSummary]:
    try:
        return teacher.list_conversations(limit=limit)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/conversations/{conversation_id}", response_model=list[TeacherQueryRead])
def teacher_conversation(
    conversation_id: str,
    teacher: EducationalTeacherService = Depends(get_library_teacher),
) -> list[TeacherQueryRead]:
    try:
        return teacher.conversation_queries(conversation_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_teacher_conversation(
    conversation_id: str,
    teacher: EducationalTeacherService = Depends(get_library_teacher),
) -> Response:
    try:
        teacher.delete_conversation(conversation_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/knowledge", response_model=list[KnowledgeUnitRead])
def knowledge_units(
    unit_status: KnowledgeStatus | None = Query(default=None, alias="status"),
    kind: KnowledgeKind | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    knowledge: EducationalKnowledgeService = Depends(get_library_knowledge),
) -> list[KnowledgeUnitRead]:
    try:
        return knowledge.list_knowledge(status=unit_status, kind=kind, limit=limit, offset=offset)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/knowledge/{unit_id}", response_model=KnowledgeUnitRead)
def knowledge_unit(
    unit_id: str,
    knowledge: EducationalKnowledgeService = Depends(get_library_knowledge),
) -> KnowledgeUnitRead:
    try:
        return knowledge.get_knowledge(unit_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/knowledge/generate", response_model=KnowledgeUnitRead)
async def generate_knowledge(
    request: KnowledgeGenerationRequest,
    knowledge: EducationalKnowledgeService = Depends(get_library_knowledge),
) -> KnowledgeUnitRead:
    try:
        return await knowledge.generate_knowledge(request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/knowledge/{unit_id}/review", response_model=KnowledgeUnitRead)
def review_knowledge(
    unit_id: str,
    request: KnowledgeReviewRequest,
    knowledge: EducationalKnowledgeService = Depends(get_library_knowledge),
) -> KnowledgeUnitRead:
    try:
        return knowledge.review(unit_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/grounded/generate", response_model=GroundedGenerationRead)
async def grounded_generate(
    request: GroundedGenerationRequest,
    knowledge: EducationalKnowledgeService = Depends(get_library_knowledge),
) -> GroundedGenerationRead:
    try:
        return await knowledge.grounded_generate(request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/grounded/{draft_id}", response_model=GroundedGenerationRead)
def grounded_draft(
    draft_id: str,
    knowledge: EducationalKnowledgeService = Depends(get_library_knowledge),
) -> GroundedGenerationRead:
    try:
        return knowledge.get_grounded_draft(draft_id)
    except Exception as exc:
        raise _translate(exc) from exc
