from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from llc_api.educational_library.cloud_knowledge.models import (
    CloudExtractionCreate,
    CloudExtractionRunRead,
    CloudProviderDescriptor,
    ValidationReport,
)
from llc_api.educational_library.cloud_knowledge.provider import CloudProviderError
from llc_api.educational_library.cloud_knowledge.service import CloudKnowledgeExtractionService
from llc_api.educational_library.dependencies import get_cloud_knowledge_extraction
from llc_api.educational_library.schemas import LibraryContractError, LibraryNotFoundError

router = APIRouter(prefix="/api/library/cloud-knowledge", tags=["cloud-knowledge"])


@router.get("/providers", response_model=list[CloudProviderDescriptor])
def list_cloud_providers(
    service: CloudKnowledgeExtractionService = Depends(get_cloud_knowledge_extraction),
) -> list[CloudProviderDescriptor]:
    return service.providers()


@router.post(
    "/runs",
    response_model=CloudExtractionRunRead,
    status_code=status.HTTP_201_CREATED,
)
def create_cloud_extraction(
    request: CloudExtractionCreate,
    service: CloudKnowledgeExtractionService = Depends(get_cloud_knowledge_extraction),
) -> CloudExtractionRunRead:
    try:
        return service.create_run(request)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/runs/{run_id}", response_model=CloudExtractionRunRead)
def get_cloud_extraction(
    run_id: str,
    service: CloudKnowledgeExtractionService = Depends(get_cloud_knowledge_extraction),
) -> CloudExtractionRunRead:
    try:
        return service.get_run(run_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/runs/{run_id}/execute", response_model=CloudExtractionRunRead)
def execute_cloud_extraction(
    run_id: str,
    service: CloudKnowledgeExtractionService = Depends(get_cloud_knowledge_extraction),
) -> CloudExtractionRunRead:
    try:
        return service.execute(run_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/runs/{run_id}/validation", response_model=ValidationReport)
def get_cloud_validation(
    run_id: str,
    service: CloudKnowledgeExtractionService = Depends(get_cloud_knowledge_extraction),
) -> ValidationReport:
    try:
        return service.validation_report(run_id)
    except Exception as exc:
        raise _http_error(exc) from exc


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LibraryNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, CloudProviderError):
        status_code = 422 if exc.state.value == "invalid_request" else 503
        return HTTPException(
            status_code=status_code,
            detail={"state": exc.state.value, "code": exc.code, "message": exc.safe_message},
        )
    if isinstance(exc, LibraryContractError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=500, detail="Cloud extraction could not be completed safely.")
