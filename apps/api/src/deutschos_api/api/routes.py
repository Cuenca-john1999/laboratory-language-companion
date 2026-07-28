import json
from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from deutschos_api.core.config import Settings, get_settings
from deutschos_api.core.model_roles import (
    DeutschOSModelRole,
    model_for_role,
)
from deutschos_api.core.time import utc_now
from deutschos_api.core.version import APPLICATION_VERSION, SCHEMA_REVISION
from deutschos_api.db.session import get_db
from deutschos_api.models import LearningSession, Mistake, StudentProfile
from deutschos_api.providers.base import (
    ModelNotFoundError,
    ModelProvider,
    ProviderResponseError,
    ProviderUnavailableError,
)
from deutschos_api.providers.dependencies import get_model_provider
from deutschos_api.schemas.api import (
    ChatRequest,
    ChatResponse,
    DashboardResponse,
    HealthResponse,
    MistakeRead,
    ModelsResponse,
    SessionRead,
    TeacherRolesResponse,
    TeacherRoleStatus,
)
from deutschos_api.schemas.profile import ProfileRead, ProfileUpdate
from deutschos_api.services.chat import (
    CorruptLearningDataError,
    MissingProfileError,
    build_teacher_messages,
    decode_json_field,
)

router = APIRouter()
PRIVATE_CHAT_SUMMARY = "Conversación local completada; contenido no almacenado."


def serialize_profile(row: StudentProfile) -> ProfileRead:
    try:
        return ProfileRead(
            id=row.id,
            preferred_name=row.preferred_name,
            native_language=row.native_language,
            additional_languages=decode_json_field(
                row.additional_languages, "additional_languages"
            ),
            current_location=row.current_location,
            professional_background=row.professional_background,
            learning_goals=decode_json_field(row.learning_goals, "learning_goals"),
            interests=decode_json_field(row.interests, "interests"),
            learning_preferences=decode_json_field(
                row.learning_preferences, "learning_preferences"
            ),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
    except CorruptLearningDataError as exc:
        raise HTTPException(
            status_code=500,
            detail="El perfil contiene datos estructurados inválidos; no se modificaron.",
        ) from exc


def teacher_messages(db: Session, payload: ChatRequest) -> list[dict[str, str]]:
    try:
        return build_teacher_messages(db, payload.message, payload.history)
    except MissingProfileError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CorruptLearningDataError as exc:
        raise HTTPException(
            status_code=500,
            detail="El contexto pedagógico local está dañado; no se inició la conversación.",
        ) from exc


def existing_chat_session(db: Session, session_id: int | None) -> LearningSession | None:
    if session_id is None:
        return None
    learning_session = db.get(LearningSession, session_id)
    if learning_session is None:
        raise HTTPException(status_code=404, detail="La sesión de conversación no existe.")
    if learning_session.session_type != "teacher_chat":
        raise HTTPException(status_code=409, detail="La sesión no es una conversación de profesor.")
    return learning_session


def record_chat_session(
    db: Session,
    model: str,
    request_started_at: datetime,
    learning_session: LearningSession | None,
) -> LearningSession:
    now = utc_now()
    if learning_session is None:
        learning_session = LearningSession(
            session_type="teacher_chat",
            started_at=request_started_at,
            model_used=model,
            summary=PRIVATE_CHAT_SUMMARY,
        )
        db.add(learning_session)
    learning_session.completed_at = now
    learning_session.duration_seconds = max(
        0, int((now - learning_session.started_at).total_seconds())
    )
    learning_session.model_used = model
    learning_session.summary = PRIVATE_CHAT_SUMMARY
    db.commit()
    db.refresh(learning_session)
    return learning_session


def ndjson_event(event_type: str, **payload: object) -> str:
    return json.dumps({"type": event_type, **payload}, ensure_ascii=False) + "\n"


async def ensure_model_available(model_provider: ModelProvider, model: str) -> None:
    try:
        installed_models = await model_provider.list_models()
    except ProviderUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ProviderResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if model not in {item.name for item in installed_models}:
        raise HTTPException(
            status_code=404,
            detail=f"El modelo local '{model}' no está instalado en LM Studio.",
        )


@router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse:
    try:
        schema_revision = db.scalar(text("SELECT version_num FROM alembic_version"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="La base local no está migrada o no está disponible.",
        ) from exc
    if schema_revision != SCHEMA_REVISION:
        raise HTTPException(
            status_code=503,
            detail=(
                f"La base local está en la revisión {schema_revision or 'desconocida'}; "
                f"se requiere {SCHEMA_REVISION}."
            ),
        )
    return HealthResponse(
        status="ok",
        service="deutschos-api",
        version=APPLICATION_VERSION,
        schema_revision=SCHEMA_REVISION,
    )


@router.get("/api/models", response_model=ModelsResponse)
async def models(
    model_provider: ModelProvider = Depends(get_model_provider),
) -> ModelsResponse:
    try:
        return ModelsResponse(
            provider="lm_studio", available=True, models=await model_provider.list_models()
        )
    except (ProviderUnavailableError, ProviderResponseError) as exc:
        return ModelsResponse(provider="lm_studio", available=False, models=[], error=str(exc))


@router.get("/api/teacher/roles", response_model=TeacherRolesResponse)
async def teacher_roles(
    model_provider: ModelProvider = Depends(get_model_provider),
) -> TeacherRolesResponse:
    roles = (DeutschOSModelRole.TEACHER, DeutschOSModelRole.DEEP_TEACHER)
    try:
        installed = {item.name for item in await model_provider.list_models()}
        return TeacherRolesResponse(
            provider="lm_studio",
            available=True,
            roles=[
                TeacherRoleStatus(role=role, available=model_for_role(role) in installed)
                for role in roles
            ],
        )
    except (ProviderUnavailableError, ProviderResponseError) as exc:
        return TeacherRolesResponse(
            provider="lm_studio",
            available=False,
            roles=[TeacherRoleStatus(role=role, available=False) for role in roles],
            error=str(exc),
        )


@router.get("/api/profile", response_model=ProfileRead)
def get_profile(db: Session = Depends(get_db)) -> ProfileRead:
    row = db.scalar(select(StudentProfile).limit(1))
    if not row:
        raise HTTPException(404, "No hay un perfil configurado. Ejecuta las migraciones.")
    return serialize_profile(row)


@router.put("/api/profile", response_model=ProfileRead)
def update_profile(payload: ProfileUpdate, db: Session = Depends(get_db)) -> ProfileRead:
    row = db.scalar(select(StudentProfile).limit(1))
    if not row:
        row = StudentProfile(
            preferred_name=payload.preferred_name, native_language=payload.native_language
        )
        db.add(row)
    for field in (
        "preferred_name",
        "native_language",
        "current_location",
        "professional_background",
    ):
        setattr(row, field, getattr(payload, field))
    for field in ("additional_languages", "learning_goals", "interests", "learning_preferences"):
        setattr(row, field, json.dumps(getattr(payload, field), ensure_ascii=False))
    db.commit()
    db.refresh(row)
    return serialize_profile(row)


@router.get("/api/dashboard", response_model=DashboardResponse)
async def dashboard(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    model_provider: ModelProvider = Depends(get_model_provider),
) -> DashboardResponse:
    profile = db.scalar(select(StudentProfile).limit(1))
    sessions = db.scalars(
        select(LearningSession).order_by(LearningSession.started_at.desc()).limit(5)
    ).all()
    pending = (
        db.scalar(
            select(func.count(Mistake.id)).where(
                Mistake.status.not_in(["mastered", "ignored"]),
                or_(Mistake.next_review_at.is_(None), Mistake.next_review_at <= utc_now()),
            )
        )
        or 0
    )
    try:
        goals = decode_json_field(profile.learning_goals, "learning_goals") if profile else []
    except CorruptLearningDataError as exc:
        raise HTTPException(
            status_code=500, detail="Los objetivos del perfil contienen datos inválidos."
        ) from exc
    try:
        installed_models = await model_provider.list_models()
        lm_studio_available = True
    except (ProviderUnavailableError, ProviderResponseError):
        installed_models = []
        lm_studio_available = False
    installed_names = {item.name for item in installed_models}
    configured_model = (
        settings.lm_studio_model if settings.lm_studio_model in installed_names else None
    )
    return DashboardResponse(
        preferred_name=profile.preferred_name if profile else "Estudiante",
        immediate_goal=goals[0]
        if isinstance(goals, list) and goals
        else "Configura tu primer objetivo",
        current_model=configured_model,
        lm_studio_available=lm_studio_available,
        pending_reviews=pending,
        recent_sessions=[SessionRead.model_validate(s) for s in sessions],
    )


@router.post("/api/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    model_provider: ModelProvider = Depends(get_model_provider),
) -> ChatResponse:
    model = model_for_role(DeutschOSModelRole(payload.role))
    messages = teacher_messages(db, payload)
    learning_session = existing_chat_session(db, payload.session_id)
    if (
        learning_session is not None
        and learning_session.model_used is not None
        and learning_session.model_used != model
    ):
        raise HTTPException(
            status_code=409,
            detail="Una conversación existente no puede cambiar de modelo.",
        )
    started_at = learning_session.started_at if learning_session else utc_now()
    await ensure_model_available(model_provider, model)
    try:
        answer = await model_provider.chat(model, messages)
    except ModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProviderUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ProviderResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    learning_session = record_chat_session(db, model, started_at, learning_session)
    return ChatResponse(response=answer, model=model, session_id=learning_session.id)


@router.post("/api/chat/stream")
async def stream_chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    model_provider: ModelProvider = Depends(get_model_provider),
) -> StreamingResponse:
    model = model_for_role(DeutschOSModelRole(payload.role))
    messages = teacher_messages(db, payload)
    learning_session = existing_chat_session(db, payload.session_id)
    if (
        learning_session is not None
        and learning_session.model_used is not None
        and learning_session.model_used != model
    ):
        raise HTTPException(
            status_code=409,
            detail="Una conversación existente no puede cambiar de modelo.",
        )
    started_at = learning_session.started_at if learning_session else utc_now()
    await ensure_model_available(model_provider, model)

    async def generate() -> AsyncIterator[str]:
        try:
            async for chunk in model_provider.stream_chat(model, messages):
                yield ndjson_event("token", content=chunk)
        except (ModelNotFoundError, ProviderUnavailableError, ProviderResponseError) as exc:
            yield ndjson_event("error", detail=str(exc))
            return
        saved_session = record_chat_session(db, model, started_at, learning_session)
        yield ndjson_event("done", model=model, session_id=saved_session.id)

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@router.get("/api/mistakes", response_model=list[MistakeRead])
def mistakes(db: Session = Depends(get_db)) -> list[MistakeRead]:
    rows = db.scalars(select(Mistake).order_by(Mistake.last_seen_at.desc())).all()
    return [MistakeRead.model_validate(row) for row in rows]


@router.get("/api/sessions", response_model=list[SessionRead])
def sessions(db: Session = Depends(get_db)) -> list[SessionRead]:
    rows = db.scalars(select(LearningSession).order_by(LearningSession.started_at.desc())).all()
    return [SessionRead.model_validate(row) for row in rows]
