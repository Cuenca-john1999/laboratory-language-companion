from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from deutschos_api.db.session import get_db
from deutschos_api.educational_library.dependencies import (
    get_document_intelligence,
    get_library_editorial,
    get_library_knowledge,
    get_library_memory,
    get_library_model_router,
    get_library_search,
    get_library_service,
    get_library_teacher,
)
from deutschos_api.educational_library.document_intelligence import DocumentIntelligenceService
from deutschos_api.educational_library.editorial import LibraryEditorialService
from deutschos_api.educational_library.knowledge import EducationalKnowledgeService
from deutschos_api.educational_library.memory import PedagogicalMemoryService
from deutschos_api.educational_library.routing import LibraryModelRouter
from deutschos_api.educational_library.schemas import (
    ChunkRead,
    ConceptAliasCreate,
    ConceptRelationCreate,
    CoreSourceAssignmentRequest,
    CoreSourcePairRead,
    EditorialSectionLinkRequest,
    EditorialSectionRead,
    EditorialSectionUpdate,
    EvidenceLocationCreate,
    EvidenceLocationRead,
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
    LibraryTeacherError,
    LocationFeedbackRequest,
    MemoryAuditRead,
    MemoryFeedbackRead,
    MemoryFeedbackRequest,
    MemoryRevertRequest,
    MemoryReviewQueueItem,
    MemoryReviewRequest,
    ModelRoutingRead,
    PageMappingRead,
    PageMappingUpdate,
    PageQualityRead,
    PageReprocessRequest,
    PageVariantRead,
    PageVariantReviewRequest,
    PedagogicalConceptCreate,
    PedagogicalConceptRead,
    PedagogicalConceptSummary,
    PedagogicalMemoryImportRead,
    PedagogicalMemoryImportRequest,
    PedagogicalMemoryStatus,
    PedagogicalMemorySummary,
    QueryMemoryRead,
    ScanRequest,
    SearchResponse,
    SemanticIndexRequest,
    SourceKind,
    SourceRead,
    SourceStatus,
    SourceUpdateRequest,
    SourceVersionRead,
    TeacherAskRequest,
    TeacherConversationSummary,
    TeacherFailureReason,
    TeacherQueryRead,
    TeacherStreamEvent,
)
from deutschos_api.educational_library.search import EducationalSearchService
from deutschos_api.educational_library.semantic import SemanticIndexCoordinator
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
    if isinstance(exc, LibraryTeacherError):
        status_code = (
            status.HTTP_504_GATEWAY_TIMEOUT
            if exc.reason == TeacherFailureReason.TIMEOUT
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        return HTTPException(
            status_code,
            {"code": exc.reason.value, "message": str(exc)},
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


async def _installed_models(provider: ModelProvider) -> list[str]:
    try:
        return [model.name for model in await provider.list_models()]
    except Exception:
        return []


@router.get("/status", response_model=LibrarySummary)
async def library_status(
    service: EducationalLibraryService = Depends(get_library_service),
    provider: ModelProvider = Depends(get_model_provider),
) -> LibrarySummary:
    try:
        installed = await _installed_models(provider)
        return service.summary(
            ollama_available=bool(installed) or await _ollama_available(provider),
            installed_models=installed,
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/models/roles", response_model=ModelRoutingRead)
async def library_model_roles(
    router_service: LibraryModelRouter = Depends(get_library_model_router),
) -> ModelRoutingRead:
    return await router_service.status()


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


@router.get("/core", response_model=CoreSourcePairRead)
def core_sources(
    editorial: LibraryEditorialService = Depends(get_library_editorial),
) -> CoreSourcePairRead:
    try:
        return editorial.core_pair()
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/sources/{source_id}/core", response_model=SourceRead)
def assign_core_source(
    source_id: str,
    request: CoreSourceAssignmentRequest,
    editorial: LibraryEditorialService = Depends(get_library_editorial),
) -> SourceRead:
    try:
        return editorial.assign_core(source_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/sources/{source_id}/sections", response_model=list[EditorialSectionRead])
def editorial_sections(
    source_id: str,
    editorial: LibraryEditorialService = Depends(get_library_editorial),
) -> list[EditorialSectionRead]:
    try:
        return editorial.list_sections(source_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/sources/{source_id}/sections/build", response_model=list[EditorialSectionRead])
def build_editorial_sections(
    source_id: str,
    editorial: LibraryEditorialService = Depends(get_library_editorial),
) -> list[EditorialSectionRead]:
    try:
        return editorial.build_section_index(source_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.put("/sections/{section_id}", response_model=EditorialSectionRead)
def update_editorial_section(
    section_id: int,
    request: EditorialSectionUpdate,
    editorial: LibraryEditorialService = Depends(get_library_editorial),
) -> EditorialSectionRead:
    try:
        return editorial.update_section(section_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/sections/{section_id}/links", response_model=EditorialSectionRead)
def link_editorial_section(
    section_id: int,
    request: EditorialSectionLinkRequest,
    editorial: LibraryEditorialService = Depends(get_library_editorial),
) -> EditorialSectionRead:
    try:
        return editorial.link_section(section_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/sources/{source_id}/pages/quality", response_model=list[PageQualityRead])
def page_quality(
    source_id: str,
    intelligence: DocumentIntelligenceService = Depends(get_document_intelligence),
) -> list[PageQualityRead]:
    try:
        return intelligence.list_quality(source_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/sources/{source_id}/pages/analyze", response_model=list[PageQualityRead])
def analyze_pages(
    source_id: str,
    intelligence: DocumentIntelligenceService = Depends(get_document_intelligence),
) -> list[PageQualityRead]:
    try:
        return intelligence.analyze_source(source_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/sources/{source_id}/pages/{page_number}/variants",
    response_model=list[PageVariantRead],
)
def page_variants(
    source_id: str,
    page_number: int,
    intelligence: DocumentIntelligenceService = Depends(get_document_intelligence),
) -> list[PageVariantRead]:
    try:
        return intelligence.list_variants(source_id, page_number)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/sources/{source_id}/pages/{page_number}/reprocess",
    response_model=PageVariantRead,
)
async def reprocess_page(
    source_id: str,
    page_number: int,
    request: PageReprocessRequest,
    intelligence: DocumentIntelligenceService = Depends(get_document_intelligence),
) -> PageVariantRead:
    try:
        if request.method == "pdftotext":
            return intelligence.reprocess_pdftotext(source_id, page_number)
        if request.method == "vision":
            return await intelligence.reprocess_vision(source_id, page_number)
        raise LibraryProviderUnavailableError("No hay un motor OCR local compatible instalado.")
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/sources/{source_id}/pages/{page_number}/review",
    response_model=PageQualityRead,
)
def review_page_variant(
    source_id: str,
    page_number: int,
    request: PageVariantReviewRequest,
    intelligence: DocumentIntelligenceService = Depends(get_document_intelligence),
) -> PageQualityRead:
    try:
        return intelligence.review_variant(source_id, page_number, request.variant_id)
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


@router.post("/semantic/index", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
def index_semantic_library(
    request: SemanticIndexRequest,
    background: BackgroundTasks,
    service: EducationalLibraryService = Depends(get_library_service),
    search_service: EducationalSearchService = Depends(get_library_search),
) -> JobRead:
    try:
        coordinator = SemanticIndexCoordinator(service, search_service)
        job = coordinator.create(request)
        background.add_task(coordinator.run, job.id, request)
        return job
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


def _sse(event: TeacherStreamEvent) -> str:
    payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
    return f"event: {event.event}\ndata: {payload}\n\n"


@router.post("/ask/stream")
async def ask_library_stream(
    request: TeacherAskRequest,
    teacher: EducationalTeacherService = Depends(get_library_teacher),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    learner = learner_context_from_db(db)

    async def events():
        yield _sse(TeacherStreamEvent(event="accepted", message="Consulta aceptada."))
        yield _sse(
            TeacherStreamEvent(
                event="planning",
                message="Preparando la búsqueda y verificando la biblioteca local.",
            )
        )
        task = asyncio.create_task(teacher.ask(request, learner=learner))
        try:
            result = await task
            yield _sse(
                TeacherStreamEvent(
                    event="verified",
                    message="Respuesta verificada contra las fuentes recuperadas.",
                    query=result,
                )
            )
        except asyncio.CancelledError:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            teacher.persist_cancelled(request)
            raise
        except LibraryTeacherError as exc:
            yield _sse(
                TeacherStreamEvent(
                    event="error",
                    message=str(exc),
                    failure_reason=exc.reason,
                )
            )
        except Exception:
            yield _sse(
                TeacherStreamEvent(
                    event="error",
                    message="La consulta local no pudo completarse de forma segura.",
                    failure_reason=TeacherFailureReason.GENERATION_FAILURE,
                )
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


@router.get("/memory/status", response_model=PedagogicalMemorySummary)
def pedagogical_memory_status(
    memory: PedagogicalMemoryService = Depends(get_library_memory),
) -> PedagogicalMemorySummary:
    try:
        return memory.summary()
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/memory/concepts", response_model=list[PedagogicalConceptSummary])
def pedagogical_concepts(
    query: str | None = Query(default=None, max_length=200),
    memory_status: PedagogicalMemoryStatus | None = Query(default=None, alias="status"),
    category: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    memory: PedagogicalMemoryService = Depends(get_library_memory),
) -> list[PedagogicalConceptSummary]:
    try:
        return memory.list_concepts(
            query=query,
            status=memory_status,
            category=category,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/memory/concepts",
    response_model=PedagogicalConceptRead,
    status_code=status.HTTP_201_CREATED,
)
def create_pedagogical_concept(
    request: PedagogicalConceptCreate,
    memory: PedagogicalMemoryService = Depends(get_library_memory),
) -> PedagogicalConceptRead:
    try:
        return memory.create_concept(request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/memory/concepts/{concept_id}", response_model=PedagogicalConceptRead)
def pedagogical_concept(
    concept_id: str,
    memory: PedagogicalMemoryService = Depends(get_library_memory),
) -> PedagogicalConceptRead:
    try:
        return memory.get_concept(concept_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/memory/concepts/{concept_id}/aliases", response_model=PedagogicalConceptRead)
def add_pedagogical_alias(
    concept_id: str,
    request: ConceptAliasCreate,
    memory: PedagogicalMemoryService = Depends(get_library_memory),
) -> PedagogicalConceptRead:
    try:
        return memory.add_alias(concept_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/memory/concepts/{concept_id}/relations", response_model=PedagogicalConceptRead)
def add_pedagogical_relation(
    concept_id: str,
    request: ConceptRelationCreate,
    memory: PedagogicalMemoryService = Depends(get_library_memory),
) -> PedagogicalConceptRead:
    try:
        return memory.add_relation(concept_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/memory/locations", response_model=list[EvidenceLocationRead])
def pedagogical_locations(
    concept_id: str | None = Query(default=None, max_length=100),
    memory_status: PedagogicalMemoryStatus | None = Query(default=None, alias="status"),
    source_id: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    memory: PedagogicalMemoryService = Depends(get_library_memory),
) -> list[EvidenceLocationRead]:
    try:
        return memory.list_locations(
            concept_id=concept_id,
            status=memory_status,
            source_id=source_id,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/memory/locations",
    response_model=EvidenceLocationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_pedagogical_location(
    request: EvidenceLocationCreate,
    memory: PedagogicalMemoryService = Depends(get_library_memory),
) -> EvidenceLocationRead:
    try:
        return memory.create_location(request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/memory/locations/{location_id}", response_model=EvidenceLocationRead)
def pedagogical_location(
    location_id: str,
    memory: PedagogicalMemoryService = Depends(get_library_memory),
) -> EvidenceLocationRead:
    try:
        return memory.get_location(location_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.put(
    "/memory/source-versions/{source_version_id}/pages/{pdf_page_number}",
    response_model=PageMappingRead,
)
def update_page_mapping(
    source_version_id: int,
    pdf_page_number: int,
    request: PageMappingUpdate,
    memory: PedagogicalMemoryService = Depends(get_library_memory),
) -> PageMappingRead:
    try:
        if request.pdf_page_number != pdf_page_number:
            raise LibraryContractError("La página del cuerpo no coincide con la ruta.")
        return memory.upsert_page_mapping(source_version_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/memory/source-versions/{source_version_id}/pages/{pdf_page_number}",
    response_model=PageMappingRead,
)
def page_mapping(
    source_version_id: int,
    pdf_page_number: int,
    memory: PedagogicalMemoryService = Depends(get_library_memory),
) -> PageMappingRead:
    try:
        return memory.get_page_mapping(source_version_id, pdf_page_number)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/queries/{query_id}/feedback", response_model=MemoryFeedbackRead)
def teacher_response_feedback(
    query_id: str,
    request: MemoryFeedbackRequest,
    memory: PedagogicalMemoryService = Depends(get_library_memory),
) -> MemoryFeedbackRead:
    try:
        return memory.feedback_response(query_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/queries/{query_id}/locations/{location_id}/feedback",
    response_model=MemoryFeedbackRead,
)
def teacher_location_feedback(
    query_id: str,
    location_id: str,
    request: LocationFeedbackRequest,
    memory: PedagogicalMemoryService = Depends(get_library_memory),
) -> MemoryFeedbackRead:
    try:
        return memory.feedback_location(query_id, location_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/queries/{query_id}/memory", response_model=QueryMemoryRead)
def teacher_query_memory(
    query_id: str,
    memory: PedagogicalMemoryService = Depends(get_library_memory),
) -> QueryMemoryRead:
    try:
        return memory.query_memory(query_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/memory/review-queue", response_model=list[MemoryReviewQueueItem])
def pedagogical_memory_review_queue(
    limit: int = Query(default=10, ge=1, le=100),
    target_type: Literal["concept", "location"] | None = Query(default=None),
    memory_status: PedagogicalMemoryStatus | None = Query(default=None, alias="status"),
    herder_only: bool = Query(default=False),
    recently_used_only: bool = Query(default=False),
    current_query_id: str | None = Query(default=None, max_length=100),
    memory: PedagogicalMemoryService = Depends(get_library_memory),
) -> list[MemoryReviewQueueItem]:
    try:
        return memory.review_queue(
            limit,
            target_type=target_type,
            status=memory_status,
            herder_only=herder_only,
            recently_used_only=recently_used_only,
            current_query_id=current_query_id,
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/memory/{target_type}/{target_id}/review", response_model=MemoryFeedbackRead)
def review_pedagogical_memory(
    target_type: Literal["concept", "location", "relation"],
    target_id: str,
    request: MemoryReviewRequest,
    memory: PedagogicalMemoryService = Depends(get_library_memory),
) -> MemoryFeedbackRead:
    try:
        return memory.review_target(target_type, target_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/memory/reviews/{review_id}/revert", response_model=MemoryFeedbackRead)
def revert_pedagogical_review(
    review_id: str,
    request: MemoryRevertRequest,
    memory: PedagogicalMemoryService = Depends(get_library_memory),
) -> MemoryFeedbackRead:
    try:
        return memory.revert(review_id, request.operation_id, request.comment)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/memory/audit", response_model=list[MemoryAuditRead])
def pedagogical_memory_audit(
    target_type: str | None = Query(default=None, max_length=50),
    target_id: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=200),
    memory: PedagogicalMemoryService = Depends(get_library_memory),
) -> list[MemoryAuditRead]:
    try:
        return memory.audit(target_type=target_type, target_id=target_id, limit=limit)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/memory/import", response_model=PedagogicalMemoryImportRead)
def import_pedagogical_memory(
    request: PedagogicalMemoryImportRequest,
    memory: PedagogicalMemoryService = Depends(get_library_memory),
) -> PedagogicalMemoryImportRead:
    try:
        return memory.import_existing(request)
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
