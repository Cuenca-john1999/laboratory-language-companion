from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from deutschos_api.api.diagnostic import router as diagnostic_router
from deutschos_api.api.learning import router as learning_router
from deutschos_api.api.routes import router
from deutschos_api.core.config import get_settings
from deutschos_api.core.version import APPLICATION_VERSION

settings = get_settings()
settings.ensure_data_directory()

app = FastAPI(title="DeutschOS API", version=APPLICATION_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(learning_router)
app.include_router(diagnostic_router)
