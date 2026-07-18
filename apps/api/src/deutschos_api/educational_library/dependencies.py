from fastapi import Depends

from deutschos_api.core.config import Settings, get_settings
from deutschos_api.providers.base import ModelProvider
from deutschos_api.providers.dependencies import get_model_provider

from .cache import LibraryCache
from .document_intelligence import DocumentIntelligenceService
from .editorial import LibraryEditorialService
from .knowledge import EducationalKnowledgeService
from .memory import PedagogicalMemoryService
from .routing import LibraryModelRouter, ModelRoutingPolicy
from .search import EducationalSearchService, OllamaEmbeddingProvider
from .service import EducationalLibraryService
from .teacher import EducationalTeacherService, TeacherLimits


def get_library_service(settings: Settings = Depends(get_settings)) -> EducationalLibraryService:
    return EducationalLibraryService(settings)


def get_library_search(
    service: EducationalLibraryService = Depends(get_library_service),
    settings: Settings = Depends(get_settings),
) -> EducationalSearchService:
    embedding_model = settings.educational_library_embedding_model.strip()
    provider = (
        OllamaEmbeddingProvider(
            settings.ollama_base_url,
            embedding_model,
            timeout=settings.educational_library_embedding_timeout_seconds,
        )
        if embedding_model
        else None
    )
    return EducationalSearchService(
        service.database,
        provider,
        cache=LibraryCache(
            service.database, ttl_seconds=settings.educational_library_cache_ttl_seconds
        ),
    )


def get_library_model_router(
    model_provider: ModelProvider = Depends(get_model_provider),
    settings: Settings = Depends(get_settings),
) -> LibraryModelRouter:
    return LibraryModelRouter(
        model_provider,
        ModelRoutingPolicy(
            planner=settings.educational_library_planner_model,
            embedding=settings.educational_library_embedding_model,
            teacher=settings.educational_library_teacher_model,
            fallback=settings.educational_library_fallback_model,
            vision=settings.educational_library_vision_model,
            repair=settings.educational_library_repair_model,
            planner_timeout=settings.educational_library_planner_timeout_seconds,
            teacher_timeout=settings.educational_library_teacher_timeout_seconds,
        ),
    )


def get_library_editorial(
    service: EducationalLibraryService = Depends(get_library_service),
) -> LibraryEditorialService:
    return LibraryEditorialService(service.database)


def get_library_memory(
    service: EducationalLibraryService = Depends(get_library_service),
) -> PedagogicalMemoryService:
    return PedagogicalMemoryService(service.database)


def get_document_intelligence(
    service: EducationalLibraryService = Depends(get_library_service),
    settings: Settings = Depends(get_settings),
) -> DocumentIntelligenceService:
    return DocumentIntelligenceService(service.database, settings)


def get_library_knowledge(
    service: EducationalLibraryService = Depends(get_library_service),
    search: EducationalSearchService = Depends(get_library_search),
    model_provider: ModelProvider = Depends(get_model_provider),
    settings: Settings = Depends(get_settings),
) -> EducationalKnowledgeService:
    return EducationalKnowledgeService(
        service.database,
        search,
        model_provider,
        default_model=settings.ollama_model,
    )


def get_library_teacher(
    service: EducationalLibraryService = Depends(get_library_service),
    search: EducationalSearchService = Depends(get_library_search),
    model_provider: ModelProvider = Depends(get_model_provider),
    settings: Settings = Depends(get_settings),
    router: LibraryModelRouter = Depends(get_library_model_router),
) -> EducationalTeacherService:
    return EducationalTeacherService(
        service.database,
        search,
        model_provider,
        default_model=settings.ollama_model,
        model_router=router,
        limits=TeacherLimits(
            max_search_queries=settings.educational_library_teacher_max_search_queries,
            max_sources=settings.educational_library_teacher_max_sources,
            max_chunks=settings.educational_library_teacher_max_chunks,
            max_chunks_per_source=settings.educational_library_teacher_max_chunks_per_source,
            max_context_characters=settings.educational_library_teacher_max_context_characters,
        ),
    )
