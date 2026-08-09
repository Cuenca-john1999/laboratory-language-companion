from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from deutschos_api.db.session import get_db
from deutschos_api.educational_library.canonical_route import CanonicalRouteService
from deutschos_api.educational_library.comparisons import DocumentComparisonService
from deutschos_api.educational_library.dependencies import (
    get_canonical_route,
    get_document_comparisons,
    get_document_intelligence,
    get_document_runs,
    get_library_editorial,
    get_library_knowledge,
    get_library_memory,
    get_library_model_router,
    get_library_search,
    get_library_service,
    get_library_teacher,
    get_structured_extraction,
)
from deutschos_api.educational_library.document_intelligence import DocumentIntelligenceService
from deutschos_api.educational_library.editorial import LibraryEditorialService
from deutschos_api.educational_library.knowledge import EducationalKnowledgeService
from deutschos_api.educational_library.memory import PedagogicalMemoryService
from deutschos_api.educational_library.routing import LibraryModelRouter
from deutschos_api.educational_library.runs import DocumentRunService
from deutschos_api.educational_library.schemas import (
    CanonicalLegacyMappingRead,
    CanonicalRouteAuditRead,
    CanonicalRouteRevertRequest,
    CanonicalRouteStatusRead,
    CanonicalTopicRead,
    CanonicalTopicReviewRequest,
    ChunkRead,
    ComparisonEventRead,
    ConceptAliasCreate,
    ConceptRelationCreate,
    CoreSourceAssignmentRequest,
    CoreSourcePairRead,
    CorrespondenceAdjust,
    CorrespondenceDecision,
    CorrespondenceList,
    CorrespondenceRead,
    CoverageSnapshotRead,
    DocumentInventoryResult,
    DocumentPageBlockRead,
    DocumentPageList,
    DocumentPageRepeatRequest,
    DocumentRunCreate,
    DocumentRunDetail,
    DocumentRunEventRead,
    DocumentRunIssueRead,
    DocumentRunPassCreate,
    DocumentRunRead,
    DocumentRunStageRead,
    DocumentStageRetryRequest,
    DocumentVersionRead,
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
    LaboratorySourceDetail,
    LaboratorySourceRead,
    LaboratorySummary,
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
    NoEquivalentRequest,
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
    ProposedHierarchyNode,
    QueryMemoryRead,
    ScanRequest,
    SearchResponse,
    SemanticIndexRequest,
    SourceKind,
    SourceRead,
    SourceStatus,
    SourceUpdateRequest,
    SourceVersionRead,
    StructureCandidateList,
    StructureCandidateRead,
    TeacherAskRequest,
    TeacherConversationSummary,
    TeacherFailureReason,
    TeacherQueryRead,
    TeacherStreamEvent,
    TransferPlanRead,
    VersionComparisonCreate,
    VersionComparisonRead,
)
from deutschos_api.educational_library.search import EducationalSearchService
from deutschos_api.educational_library.semantic import SemanticIndexCoordinator
from deutschos_api.educational_library.service import EducationalLibraryService
from deutschos_api.educational_library.structured_extraction import StructuredExtractionService
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


async def _lm_studio_available(provider: ModelProvider) -> bool:
    try:
        return await provider.health_check()
    except Exception:
        return False


async def _installed_models(provider: ModelProvider) -> list[str]:
    try:
        return [model.name for model in await provider.list_models()]
    except Exception:
        return []


@router.get("/laboratory/summary", response_model=LaboratorySummary)
def laboratory_summary(
    service: EducationalLibraryService = Depends(get_library_service),
) -> LaboratorySummary:
    try:
        return service.laboratory_summary()
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/laboratory/sources", response_model=list[LaboratorySourceRead])
def laboratory_sources(
    filter_name: Literal[
        "all", "active", "candidates", "needs_ocr", "errors", "without_content", "missing"
    ] = Query(default="all", alias="filter"),
    service: EducationalLibraryService = Depends(get_library_service),
) -> list[LaboratorySourceRead]:
    try:
        return service.laboratory_sources(filter_name=filter_name)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/laboratory/sources/{source_id}", response_model=LaboratorySourceDetail)
def laboratory_source(
    source_id: str,
    service: EducationalLibraryService = Depends(get_library_service),
) -> LaboratorySourceDetail:
    try:
        return service.laboratory_source(source_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/laboratory/sources/{source_id}/versions",
    response_model=list[DocumentVersionRead],
)
def laboratory_source_versions(
    source_id: str,
    service: EducationalLibraryService = Depends(get_library_service),
) -> list[DocumentVersionRead]:
    try:
        return service.document_versions(source_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/laboratory/versions/{version_id}", response_model=DocumentVersionRead)
def laboratory_version(
    version_id: int,
    service: EducationalLibraryService = Depends(get_library_service),
) -> DocumentVersionRead:
    try:
        return service.document_version(version_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/laboratory/comparisons",
    response_model=list[VersionComparisonRead],
)
def list_version_comparisons(
    state: str | None = None,
    source_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    service: DocumentComparisonService = Depends(get_document_comparisons),
) -> list[VersionComparisonRead]:
    try:
        return service.list(state=state, source_id=source_id, limit=limit)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/comparisons",
    response_model=VersionComparisonRead,
    status_code=status.HTTP_201_CREATED,
)
def create_version_comparison(
    request: VersionComparisonCreate,
    service: DocumentComparisonService = Depends(get_document_comparisons),
) -> VersionComparisonRead:
    try:
        return service.create(request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/laboratory/comparisons/{comparison_id}",
    response_model=VersionComparisonRead,
)
def version_comparison_detail(
    comparison_id: str,
    service: DocumentComparisonService = Depends(get_document_comparisons),
) -> VersionComparisonRead:
    try:
        return service.get(comparison_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/comparisons/{comparison_id}/execute",
    response_model=VersionComparisonRead,
)
def execute_version_comparison(
    comparison_id: str,
    service: DocumentComparisonService = Depends(get_document_comparisons),
) -> VersionComparisonRead:
    try:
        return service.execute(comparison_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/laboratory/comparisons/{comparison_id}/metrics",
    response_model=VersionComparisonRead,
)
def version_comparison_metrics(
    comparison_id: str,
    service: DocumentComparisonService = Depends(get_document_comparisons),
) -> VersionComparisonRead:
    try:
        return service.get(comparison_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/laboratory/comparisons/{comparison_id}/correspondences",
    response_model=CorrespondenceList,
)
def list_page_correspondences(
    comparison_id: str,
    confidence: str | None = None,
    review_state: str | None = None,
    relation_type: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    service: DocumentComparisonService = Depends(get_document_comparisons),
) -> CorrespondenceList:
    try:
        return service.correspondences(
            comparison_id,
            confidence=confidence,
            review_state=review_state,
            relation_type=relation_type,
            page=page,
            page_size=page_size,
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/laboratory/comparisons/{comparison_id}/correspondences/{correspondence_id}",
    response_model=CorrespondenceRead,
)
def page_correspondence_detail(
    comparison_id: str,
    correspondence_id: str,
    service: DocumentComparisonService = Depends(get_document_comparisons),
) -> CorrespondenceRead:
    try:
        result = service.correspondence(correspondence_id)
        if result.comparison_id != comparison_id:
            raise LibraryNotFoundError("La correspondencia no pertenece a la comparación.")
        return result
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/laboratory/comparisons/{comparison_id}/correspondences/{correspondence_id}/text-difference",
    response_model=CorrespondenceRead,
)
def page_correspondence_text_difference(
    comparison_id: str,
    correspondence_id: str,
    service: DocumentComparisonService = Depends(get_document_comparisons),
) -> CorrespondenceRead:
    return page_correspondence_detail(comparison_id, correspondence_id, service)


@router.get(
    "/laboratory/comparisons/{comparison_id}/correspondences/{correspondence_id}/evidence",
    response_model=CorrespondenceRead,
)
def page_correspondence_evidence(
    comparison_id: str,
    correspondence_id: str,
    service: DocumentComparisonService = Depends(get_document_comparisons),
) -> CorrespondenceRead:
    return page_correspondence_detail(comparison_id, correspondence_id, service)


@router.get(
    "/laboratory/comparisons/{comparison_id}/correspondences/{correspondence_id}/"
    "thumbnail/{side}/{position}",
    response_class=Response,
)
def page_correspondence_thumbnail(
    comparison_id: str,
    correspondence_id: str,
    side: Literal["base", "target"],
    position: int,
    service: DocumentComparisonService = Depends(get_document_comparisons),
) -> Response:
    try:
        relation = service.correspondence(correspondence_id)
        if relation.comparison_id != comparison_id:
            raise LibraryNotFoundError("La correspondencia no pertenece a la comparación.")
        svg = service.technical_thumbnail(correspondence_id, side, position)
        return Response(
            content=svg,
            media_type="image/svg+xml",
            headers={"Cache-Control": "private, max-age=3600"},
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/comparisons/{comparison_id}/correspondences/{correspondence_id}/confirm",
    response_model=CorrespondenceRead,
)
def confirm_page_correspondence(
    comparison_id: str,
    correspondence_id: str,
    request: CorrespondenceDecision,
    service: DocumentComparisonService = Depends(get_document_comparisons),
) -> CorrespondenceRead:
    try:
        existing = service.correspondence(correspondence_id)
        if existing.comparison_id != comparison_id:
            raise LibraryNotFoundError("La correspondencia no pertenece a la comparación.")
        result = service.decide(correspondence_id, "confirm", request)
        return result
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/comparisons/{comparison_id}/correspondences/{correspondence_id}/reject",
    response_model=CorrespondenceRead,
)
def reject_page_correspondence(
    comparison_id: str,
    correspondence_id: str,
    request: CorrespondenceDecision,
    service: DocumentComparisonService = Depends(get_document_comparisons),
) -> CorrespondenceRead:
    try:
        existing = service.correspondence(correspondence_id)
        if existing.comparison_id != comparison_id:
            raise LibraryNotFoundError("La correspondencia no pertenece a la comparación.")
        result = service.decide(correspondence_id, "reject", request)
        return result
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/comparisons/{comparison_id}/correspondences/{correspondence_id}/adjust",
    response_model=CorrespondenceRead,
)
def adjust_page_correspondence(
    comparison_id: str,
    correspondence_id: str,
    request: CorrespondenceAdjust,
    service: DocumentComparisonService = Depends(get_document_comparisons),
) -> CorrespondenceRead:
    try:
        existing = service.correspondence(correspondence_id)
        if existing.comparison_id != comparison_id:
            raise LibraryNotFoundError("La correspondencia no pertenece a la comparación.")
        return service.adjust(correspondence_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/comparisons/{comparison_id}/no-equivalent",
    response_model=CorrespondenceRead,
)
def mark_page_without_equivalent(
    comparison_id: str,
    request: NoEquivalentRequest,
    service: DocumentComparisonService = Depends(get_document_comparisons),
) -> CorrespondenceRead:
    try:
        return service.mark_no_equivalent(comparison_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/laboratory/comparisons/{comparison_id}/transfer-plan",
    response_model=TransferPlanRead,
)
def version_comparison_transfer_plan(
    comparison_id: str,
    service: DocumentComparisonService = Depends(get_document_comparisons),
) -> TransferPlanRead:
    try:
        return service.transfer_plan(comparison_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/laboratory/comparisons/{comparison_id}/events",
    response_model=list[ComparisonEventRead],
)
def version_comparison_events(
    comparison_id: str,
    service: DocumentComparisonService = Depends(get_document_comparisons),
) -> list[ComparisonEventRead]:
    try:
        return service.events(comparison_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/comparisons/{comparison_id}/events/{event_id}/revert",
    response_model=ComparisonEventRead,
)
def revert_version_comparison_event(
    comparison_id: str,
    event_id: str,
    request: CorrespondenceDecision,
    service: DocumentComparisonService = Depends(get_document_comparisons),
) -> ComparisonEventRead:
    try:
        return service.revert_event(comparison_id, event_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/comparisons/{comparison_id}/cancel",
    response_model=VersionComparisonRead,
)
def cancel_version_comparison(
    comparison_id: str,
    service: DocumentComparisonService = Depends(get_document_comparisons),
) -> VersionComparisonRead:
    try:
        return service.cancel(comparison_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/comparisons/{comparison_id}/recalculate",
    response_model=VersionComparisonRead,
)
def recalculate_version_comparison(
    comparison_id: str,
    service: DocumentComparisonService = Depends(get_document_comparisons),
) -> VersionComparisonRead:
    try:
        return service.execute(comparison_id, recalculate=True)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/laboratory/inventory", response_model=DocumentInventoryResult)
def run_document_inventory(
    service: EducationalLibraryService = Depends(get_library_service),
) -> DocumentInventoryResult:
    try:
        return service.detect_document_changes()
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/laboratory/inventory/latest", response_model=DocumentInventoryResult)
def latest_document_inventory(
    service: EducationalLibraryService = Depends(get_library_service),
) -> DocumentInventoryResult:
    try:
        return service.latest_document_inventory()
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/laboratory/runs", response_model=list[DocumentRunRead])
def list_document_runs(
    source_id: str | None = None,
    source_version_id: int | None = Query(default=None, gt=0),
    state: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    service: DocumentRunService = Depends(get_document_runs),
) -> list[DocumentRunRead]:
    try:
        return service.list_runs(
            source_id=source_id,
            source_version_id=source_version_id,
            state=state,
            limit=limit,
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/runs",
    response_model=DocumentRunDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_document_run(
    request: DocumentRunCreate,
    service: DocumentRunService = Depends(get_document_runs),
) -> DocumentRunDetail:
    try:
        return service.create_run(request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/extraction/runs",
    response_model=DocumentRunDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_structured_extraction_run(
    source_version_id: int = Query(gt=0),
    service: StructuredExtractionService = Depends(get_structured_extraction),
) -> DocumentRunDetail:
    try:
        return service.create_run(source_version_id, actor="laboratory")
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/laboratory/runs/{run_id}", response_model=DocumentRunDetail)
def document_run_detail(
    run_id: str,
    service: DocumentRunService = Depends(get_document_runs),
) -> DocumentRunDetail:
    try:
        return service.get_run(run_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/laboratory/runs/{run_id}/stages",
    response_model=list[DocumentRunStageRead],
)
def document_run_stages(
    run_id: str,
    service: DocumentRunService = Depends(get_document_runs),
) -> list[DocumentRunStageRead]:
    try:
        return service.stages(run_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/laboratory/runs/{run_id}/coverage",
    response_model=list[CoverageSnapshotRead],
)
def document_run_coverage(
    run_id: str,
    service: DocumentRunService = Depends(get_document_runs),
) -> list[CoverageSnapshotRead]:
    try:
        return service.coverage(run_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/laboratory/runs/{run_id}/pages", response_model=DocumentPageList)
def document_run_pages(
    run_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    filter_name: Literal[
        "all",
        "pending",
        "completed",
        "issues",
        "failed",
        "needs_review",
        "without_text",
    ] = Query(default="all", alias="filter"),
    service: DocumentRunService = Depends(get_document_runs),
) -> DocumentPageList:
    try:
        return service.pages(
            run_id,
            page=page,
            page_size=page_size,
            filter_name=filter_name,
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/laboratory/runs/{run_id}/issues",
    response_model=list[DocumentRunIssueRead],
)
def document_run_issues(
    run_id: str,
    service: DocumentRunService = Depends(get_document_runs),
) -> list[DocumentRunIssueRead]:
    try:
        return service.issues(run_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/laboratory/runs/{run_id}/events",
    response_model=list[DocumentRunEventRead],
)
def document_run_events(
    run_id: str,
    service: DocumentRunService = Depends(get_document_runs),
) -> list[DocumentRunEventRead]:
    try:
        return service.events(run_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/laboratory/runs/{run_id}/preflight", response_model=DocumentRunDetail)
def execute_document_preflight(
    run_id: str,
    service: DocumentRunService = Depends(get_document_runs),
) -> DocumentRunDetail:
    try:
        return service.run_preflight(run_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/runs/{run_id}/pdf-preflight",
    response_model=DocumentRunDetail,
)
def execute_structured_pdf_preflight(
    run_id: str,
    service: StructuredExtractionService = Depends(get_structured_extraction),
) -> DocumentRunDetail:
    try:
        return service.preflight(run_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/runs/{run_id}/pages/materialize",
    response_model=DocumentRunDetail,
)
def materialize_document_pages(
    run_id: str,
    service: StructuredExtractionService = Depends(get_structured_extraction),
) -> DocumentRunDetail:
    try:
        return service.materialize_pages(run_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/runs/{run_id}/text/extract-embedded",
    response_model=DocumentRunDetail,
)
def extract_document_embedded_text(
    run_id: str,
    service: StructuredExtractionService = Depends(get_structured_extraction),
) -> DocumentRunDetail:
    try:
        return service.extract_embedded_text(run_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/runs/{run_id}/layout/analyze",
    response_model=DocumentRunDetail,
)
def analyze_document_layout(
    run_id: str,
    service: StructuredExtractionService = Depends(get_structured_extraction),
) -> DocumentRunDetail:
    try:
        return service.analyze_layout(run_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/runs/{run_id}/candidates/extract",
    response_model=DocumentRunDetail,
)
def extract_document_structure_candidates(
    run_id: str,
    service: StructuredExtractionService = Depends(get_structured_extraction),
) -> DocumentRunDetail:
    try:
        return service.extract_candidates(run_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/runs/{run_id}/structured-coverage/reconcile",
    response_model=DocumentRunDetail,
)
def reconcile_structured_document_coverage(
    run_id: str,
    service: StructuredExtractionService = Depends(get_structured_extraction),
) -> DocumentRunDetail:
    try:
        return service.reconcile_coverage(run_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/runs/{run_id}/version-comparison",
    response_model=DocumentRunDetail,
)
def compare_structured_document_version(
    run_id: str,
    service: StructuredExtractionService = Depends(get_structured_extraction),
) -> DocumentRunDetail:
    try:
        return service.compare_version(run_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/runs/{run_id}/pages/repeat",
    response_model=DocumentRunDetail,
    status_code=status.HTTP_201_CREATED,
)
def repeat_structured_document_pages(
    run_id: str,
    request: DocumentPageRepeatRequest,
    service: StructuredExtractionService = Depends(get_structured_extraction),
) -> DocumentRunDetail:
    try:
        return service.repeat_pages(run_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/laboratory/runs/{run_id}/pages/{pdf_page_number}/blocks",
    response_model=list[DocumentPageBlockRead],
)
def structured_document_page_blocks(
    run_id: str,
    pdf_page_number: int,
    service: StructuredExtractionService = Depends(get_structured_extraction),
) -> list[DocumentPageBlockRead]:
    try:
        return service.blocks(run_id, pdf_page_number)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/laboratory/extraction/candidates", response_model=StructureCandidateList)
def list_structure_candidates(
    source_version_id: int | None = Query(default=None, gt=0),
    run_id: str | None = None,
    pdf_page_number: int | None = Query(default=None, gt=0),
    candidate_type: str | None = None,
    candidate_status: str | None = Query(default=None, alias="status"),
    topic: str | None = None,
    level: str | None = None,
    min_confidence: float | None = Query(default=None, ge=0, le=1),
    with_issue: bool | None = None,
    without_parent: bool | None = None,
    ambiguous: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    service: StructuredExtractionService = Depends(get_structured_extraction),
) -> StructureCandidateList:
    try:
        return service.candidates(
            source_version_id=source_version_id,
            run_id=run_id,
            pdf_page_number=pdf_page_number,
            candidate_type=candidate_type,
            status=candidate_status,
            topic=topic,
            level=level,
            min_confidence=min_confidence,
            with_issue=with_issue,
            without_parent=without_parent,
            ambiguous=ambiguous,
            page=page,
            page_size=page_size,
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/laboratory/extraction/candidates/{candidate_id}",
    response_model=StructureCandidateRead,
)
def structure_candidate_detail(
    candidate_id: str,
    service: StructuredExtractionService = Depends(get_structured_extraction),
) -> StructureCandidateRead:
    try:
        return service.candidate(candidate_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/laboratory/extraction/versions/{source_version_id}/hierarchy",
    response_model=list[ProposedHierarchyNode],
)
def proposed_document_hierarchy(
    source_version_id: int,
    service: StructuredExtractionService = Depends(get_structured_extraction),
) -> list[ProposedHierarchyNode]:
    try:
        return service.hierarchy(source_version_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get(
    "/laboratory/extraction/versions/{source_version_id}/pages/{pdf_page_number}/thumbnail",
    response_class=FileResponse,
)
def structured_document_thumbnail(
    source_version_id: int,
    pdf_page_number: int,
    service: StructuredExtractionService = Depends(get_structured_extraction),
) -> FileResponse:
    try:
        path = service.thumbnail(source_version_id, pdf_page_number)
        return FileResponse(path, media_type="image/png", filename=path.name)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/runs/{run_id}/coverage/reconcile",
    response_model=DocumentRunDetail,
)
def reconcile_document_coverage(
    run_id: str,
    service: DocumentRunService = Depends(get_document_runs),
) -> DocumentRunDetail:
    try:
        return service.reconcile_coverage(run_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/laboratory/runs/{run_id}/pause", response_model=DocumentRunDetail)
def pause_document_run(
    run_id: str,
    service: DocumentRunService = Depends(get_document_runs),
) -> DocumentRunDetail:
    try:
        return service.pause(run_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/laboratory/runs/{run_id}/resume", response_model=DocumentRunDetail)
def resume_document_run(
    run_id: str,
    service: DocumentRunService = Depends(get_document_runs),
) -> DocumentRunDetail:
    try:
        return service.resume(run_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/laboratory/runs/{run_id}/cancel", response_model=DocumentRunDetail)
def cancel_document_run(
    run_id: str,
    service: DocumentRunService = Depends(get_document_runs),
) -> DocumentRunDetail:
    try:
        return service.cancel(run_id)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/runs/{run_id}/stages/{stage_name}/retry",
    response_model=DocumentRunDetail,
)
def retry_document_stage(
    run_id: str,
    stage_name: str,
    request: DocumentStageRetryRequest,
    service: DocumentRunService = Depends(get_document_runs),
) -> DocumentRunDetail:
    try:
        return service.retry_stage(
            run_id,
            stage_name,
            failed_pages_only=request.failed_pages_only,
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post(
    "/laboratory/runs/{run_id}/passes",
    response_model=DocumentRunDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_document_run_pass(
    run_id: str,
    request: DocumentRunPassCreate,
    service: DocumentRunService = Depends(get_document_runs),
) -> DocumentRunDetail:
    try:
        return service.create_pass(run_id, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/status", response_model=LibrarySummary)
async def library_status(
    service: EducationalLibraryService = Depends(get_library_service),
    provider: ModelProvider = Depends(get_model_provider),
) -> LibrarySummary:
    try:
        installed = await _installed_models(provider)
        return service.summary(
            lm_studio_available=bool(installed) or await _lm_studio_available(provider),
            installed_models=installed,
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/canonical-route/status", response_model=CanonicalRouteStatusRead)
def canonical_route_status(
    service: CanonicalRouteService = Depends(get_canonical_route),
) -> CanonicalRouteStatusRead:
    try:
        return service.status()
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/canonical-route/topics", response_model=list[CanonicalTopicRead])
def canonical_route_topics(
    query: str | None = Query(default=None, max_length=200),
    service: CanonicalRouteService = Depends(get_canonical_route),
) -> list[CanonicalTopicRead]:
    try:
        return service.list_topics(query)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/canonical-route/topics/{theme_number}", response_model=CanonicalTopicRead)
def canonical_route_topic(
    theme_number: int,
    service: CanonicalRouteService = Depends(get_canonical_route),
) -> CanonicalTopicRead:
    try:
        return service.topic(theme_number)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/canonical-route/legacy-mappings", response_model=list[CanonicalLegacyMappingRead])
def canonical_route_mappings(
    mapping_status: Literal["exact", "probable", "ambiguous", "unmatched", "rejected"]
    | None = Query(default=None, alias="status"),
    service: CanonicalRouteService = Depends(get_canonical_route),
) -> list[CanonicalLegacyMappingRead]:
    try:
        return service.mappings(mapping_status)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/canonical-route/topics/{theme_number}/review", response_model=CanonicalTopicRead)
def review_canonical_route_topic(
    theme_number: int,
    request: CanonicalTopicReviewRequest,
    service: CanonicalRouteService = Depends(get_canonical_route),
) -> CanonicalTopicRead:
    try:
        return service.review_topic(theme_number, request)
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/canonical-route/audits", response_model=list[CanonicalRouteAuditRead])
def canonical_route_audits(
    limit: int = Query(default=100, ge=1, le=500),
    service: CanonicalRouteService = Depends(get_canonical_route),
) -> list[CanonicalRouteAuditRead]:
    try:
        return service.audits(limit)
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/canonical-route/revert", response_model=CanonicalRouteAuditRead)
def revert_canonical_route_review(
    request: CanonicalRouteRevertRequest,
    service: CanonicalRouteService = Depends(get_canonical_route),
) -> CanonicalRouteAuditRead:
    try:
        return service.revert(request)
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
