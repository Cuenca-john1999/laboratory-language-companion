import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from llc_api.api.diagnostic import router as diagnostic_router
from llc_api.api.learning import router as learning_router
from llc_api.api.library import router as library_router
from llc_api.api.routes import router
from llc_api.api.study import router as study_router
from llc_api.core.config import get_settings
from llc_api.core.version import APPLICATION_VERSION

settings = get_settings()
settings.ensure_data_directory()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from llc_api.educational_library.service import EducationalLibraryService

    EducationalLibraryService(settings)
    task = None
    if settings.educational_library_scan_on_startup:
        from llc_api.educational_library.lifecycle import library_polling_loop

        task = asyncio.create_task(library_polling_loop(settings))
    yield
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="LLC API",
    description=(
        "Laboratory Language Companion — local-first language learning for "
        "laboratory and life-science professionals."
    ),
    version=APPLICATION_VERSION,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(learning_router)
app.include_router(diagnostic_router)
app.include_router(library_router)
app.include_router(study_router)
