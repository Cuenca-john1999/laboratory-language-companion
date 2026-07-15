from fastapi import Depends

from deutschos_api.core.config import Settings, get_settings
from deutschos_api.providers.base import ModelProvider
from deutschos_api.providers.dependencies import get_model_provider

from .knowledge import EducationalKnowledgeService
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
        OllamaEmbeddingProvider(settings.ollama_base_url, embedding_model)
        if embedding_model
        else None
    )
    return EducationalSearchService(service.database, provider)


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
) -> EducationalTeacherService:
    return EducationalTeacherService(
        service.database,
        search,
        model_provider,
        default_model=settings.ollama_model,
        limits=TeacherLimits(
            max_search_queries=settings.educational_library_teacher_max_search_queries,
            max_sources=settings.educational_library_teacher_max_sources,
            max_chunks=settings.educational_library_teacher_max_chunks,
            max_chunks_per_source=settings.educational_library_teacher_max_chunks_per_source,
            max_context_characters=settings.educational_library_teacher_max_context_characters,
        ),
    )
