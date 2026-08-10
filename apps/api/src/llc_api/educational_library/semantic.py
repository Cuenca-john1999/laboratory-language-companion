from __future__ import annotations

from .schemas import JobRead, JobState, LibraryProviderUnavailableError, SemanticIndexRequest
from .search import EducationalSearchService
from .service import EducationalLibraryService


class SemanticIndexCoordinator:
    def __init__(
        self,
        service: EducationalLibraryService,
        search: EducationalSearchService,
    ):
        self.service = service
        self.search = search

    def create(self, request: SemanticIndexRequest) -> JobRead:
        if self.search.embedding_provider is None:
            raise LibraryProviderUnavailableError(
                "No hay un modelo local de embeddings configurado."
            )
        return self.service.create_job(
            "semantic_index",
            payload=request.model_dump(mode="json"),
            priority=50 if request.core_first else 0,
        )

    async def run(self, job_id: str, request: SemanticIndexRequest) -> None:
        _, model_digest = await self.search._provider_metadata()
        total = self.search.embedding_candidate_count(model_digest=model_digest)
        self.service._start_job(job_id, total)

        def checkpoint(current: int, current_total: int) -> bool:
            total_for_job = max(total, current_total, current)
            if total_for_job != total:
                with self.service.database.transaction(immediate=True) as connection:
                    connection.execute(
                        "UPDATE processing_jobs SET progress_total=? WHERE id=?",
                        (total_for_job, job_id),
                    )
            return self.service._checkpoint_job(job_id, current, str(current))

        try:
            await self.search.index_embeddings(
                batch_size=request.batch_size,
                limit=request.limit,
                core_first=request.core_first,
                checkpoint=checkpoint,
            )
            job = self.service.get_job(job_id)
            final = (
                JobState.CANCELLED
                if job.cancel_requested
                else JobState.PAUSED
                if job.state == JobState.PAUSED
                else JobState.COMPLETED
            )
            if final != JobState.PAUSED:
                self.service._finish_job(job_id, final)
        except Exception as exc:
            self.service._finish_job(job_id, JobState.FAILED, type(exc).__name__)
            raise
