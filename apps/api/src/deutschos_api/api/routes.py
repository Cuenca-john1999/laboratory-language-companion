import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime
from uuid import UUID, uuid4

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
    EmptyVisibleContentError,
    ModelNotFoundError,
    ModelProvider,
    ProviderResponseError,
    ProviderStreamEvent,
    ProviderUnavailableError,
    has_visible_content,
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
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
PRIVATE_CHAT_SUMMARY = "Conversación local completada; contenido no almacenado."
EMPTY_RESPONSE_RETRY_INSTRUCTION = (
    "Responde directamente con la respuesta final visible. "
    "No consumas la salida en razonamiento interno. No menciones este reintento."
)
EMPTY_RESPONSE_ERROR = (
    "El profesor no pudo generar una respuesta visible. Puedes volver a intentarlo."
)
CONTINUATION_INSTRUCTION = (
    "Continúa exactamente desde el punto donde terminó la respuesta anterior. "
    "No repitas lo ya escrito, no vuelvas a introducir el tema y no menciones "
    "que se trata de una continuación. Completa primero cualquier frase, lista, "
    "tabla, bloque de código o fórmula que haya quedado incompleta."
)
MAX_PROVIDER_CALLS = 3
MAX_AUTOMATIC_CONTINUATIONS = 1
MAX_OVERLAP_CHARACTERS = 512
MIN_OVERLAP_CHARACTERS = 4


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


def retry_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        *messages,
        {"role": "system", "content": EMPTY_RESPONSE_RETRY_INSTRUCTION},
    ]


def continuation_messages(
    messages: list[dict[str, str]],
    partial_response: str,
    *,
    retry_empty: bool = False,
) -> list[dict[str, str]]:
    continuation = [
        *messages,
        {"role": "assistant", "content": partial_response},
        {"role": "system", "content": CONTINUATION_INSTRUCTION},
    ]
    return retry_messages(continuation) if retry_empty else continuation


def exact_overlap_size(previous: str, continuation: str) -> int:
    """Find a conservative exact suffix/prefix overlap without rewriting text."""
    limit = min(len(previous), len(continuation), MAX_OVERLAP_CHARACTERS)
    for size in range(limit, MIN_OVERLAP_CHARACTERS - 1, -1):
        if previous[-size:] == continuation[:size]:
            return size
    return 0


def overlap_may_extend(previous: str, continuation_prefix: str) -> bool:
    """Return whether a longer exact overlap can still match future characters."""
    limit = min(len(previous), MAX_OVERLAP_CHARACTERS)
    first_size = max(len(continuation_prefix) + 1, MIN_OVERLAP_CHARACTERS)
    return any(
        previous[-size:].startswith(continuation_prefix) for size in range(first_size, limit + 1)
    )


def generation_log(
    event: str,
    *,
    request_id: UUID,
    model: str,
    role: str,
    attempt: int,
    started_at: float,
    visible_characters: int = 0,
    reasoning_present: bool = False,
    finish_reason: str | None = None,
    usage: dict[str, int] | None = None,
    logical_generation_id: UUID | None = None,
    segment: int | None = None,
    overlap_characters: int = 0,
) -> None:
    metadata = {
        "event": event,
        "request_id": str(request_id),
        "model": model,
        "role": role,
        "attempt": attempt,
        "duration_ms": round((time.monotonic() - started_at) * 1000),
        "visible_characters": visible_characters,
        "reasoning_present": reasoning_present,
        "finish_reason": finish_reason,
        "usage": usage or {},
        "logical_generation_id": (
            str(logical_generation_id) if logical_generation_id is not None else None
        ),
        "segment": segment,
        "overlap_characters": overlap_characters,
    }
    logger.info(
        json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
        extra={"generation": metadata},
    )


async def provider_stream_events(
    model_provider: ModelProvider,
    model: str,
    messages: list[dict[str, str]],
) -> AsyncIterator[ProviderStreamEvent]:
    event_stream = getattr(model_provider, "stream_chat_events", None)
    if callable(event_stream):
        async for event in event_stream(model, messages):
            yield event
        return
    async for content in model_provider.stream_chat(model, messages):
        yield ProviderStreamEvent(content=content)


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
    answer = ""
    recovery = False
    for attempt in (1, 2):
        attempt_request_id = payload.request_id if attempt == 1 else uuid4()
        generation_started_at = time.monotonic()
        generation_log(
            "generation_started",
            request_id=attempt_request_id,
            model=model,
            role=payload.role,
            attempt=attempt,
            started_at=generation_started_at,
        )
        if attempt == 2:
            generation_log(
                "retry_started",
                request_id=attempt_request_id,
                model=model,
                role=payload.role,
                attempt=attempt,
                started_at=generation_started_at,
            )
        try:
            answer = await model_provider.chat(
                model, messages if attempt == 1 else retry_messages(messages)
            )
            if not has_visible_content(answer):
                raise EmptyVisibleContentError()
        except EmptyVisibleContentError as exc:
            generation_log(
                "empty_visible_content_detected",
                request_id=attempt_request_id,
                model=model,
                role=payload.role,
                attempt=attempt,
                started_at=generation_started_at,
                reasoning_present=exc.reasoning_present,
                finish_reason=exc.finish_reason,
                usage=exc.usage,
            )
            if attempt == 1:
                recovery = True
                continue
            generation_log(
                "retry_failed",
                request_id=attempt_request_id,
                model=model,
                role=payload.role,
                attempt=attempt,
                started_at=generation_started_at,
                reasoning_present=exc.reasoning_present,
                finish_reason=exc.finish_reason,
                usage=exc.usage,
            )
            raise HTTPException(status_code=502, detail=EMPTY_RESPONSE_ERROR) from exc
        except ModelNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ProviderUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ProviderResponseError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        generation_log(
            "visible_content_started",
            request_id=attempt_request_id,
            model=model,
            role=payload.role,
            attempt=attempt,
            started_at=generation_started_at,
            visible_characters=len(answer),
        )
        if recovery:
            generation_log(
                "retry_succeeded",
                request_id=attempt_request_id,
                model=model,
                role=payload.role,
                attempt=attempt,
                started_at=generation_started_at,
                visible_characters=len(answer),
            )
        break
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
        logical_generation_id = payload.logical_generation_id
        total_started_at = time.monotonic()
        provider_call_count = 0
        empty_retry_count = 0
        automatic_continuation_count = payload.automatic_continuation_count
        manual_continuation_count = payload.manual_continuation_count
        segment_count = payload.prior_segment_count
        accumulated_content = payload.continuation_from or ""
        aggregate_usage: dict[str, int] = {}
        aggregate_reasoning_present = False
        phase = "continuation" if payload.manual_continuation else "initial"
        if payload.manual_continuation:
            manual_continuation_count += 1
        recovery: str | None = None
        retrying_empty_segment = False

        while provider_call_count < MAX_PROVIDER_CALLS:
            provider_call_count += 1
            attempt_request_id = payload.request_id if provider_call_count == 1 else uuid4()
            segment_started_at = time.monotonic()
            continuation_phase = phase == "continuation"
            prospective_segment = segment_count + 1
            visible_started = False
            buffered_content = ""
            segment_content = ""
            continuation_prefix = ""
            overlap_resolved = not continuation_phase
            overlap_characters = 0
            reasoning_present = False
            finish_reason: str | None = None
            usage: dict[str, int] = {}
            generation_log(
                "generation_segment_started",
                request_id=attempt_request_id,
                model=model,
                role=payload.role,
                attempt=provider_call_count,
                started_at=segment_started_at,
                visible_characters=len(accumulated_content),
                logical_generation_id=logical_generation_id,
                segment=prospective_segment,
            )
            if continuation_phase and not retrying_empty_segment:
                generation_log(
                    (
                        "manual_continuation_started"
                        if payload.manual_continuation
                        else "automatic_continuation_started"
                    ),
                    request_id=attempt_request_id,
                    model=model,
                    role=payload.role,
                    attempt=provider_call_count,
                    started_at=segment_started_at,
                    visible_characters=len(accumulated_content),
                    logical_generation_id=logical_generation_id,
                    segment=prospective_segment,
                )
            try:
                if continuation_phase:
                    attempt_messages = continuation_messages(
                        messages,
                        accumulated_content,
                        retry_empty=retrying_empty_segment,
                    )
                else:
                    attempt_messages = (
                        retry_messages(messages) if retrying_empty_segment else messages
                    )
                async for event in provider_stream_events(model_provider, model, attempt_messages):
                    reasoning_present = reasoning_present or event.reasoning_present
                    finish_reason = event.finish_reason or finish_reason
                    usage.update(event.usage)
                    if not event.content:
                        continue

                    if continuation_phase and not overlap_resolved:
                        continuation_prefix += event.content
                        if len(continuation_prefix) < MAX_OVERLAP_CHARACTERS and overlap_may_extend(
                            accumulated_content, continuation_prefix
                        ):
                            continue
                        overlap_characters = exact_overlap_size(
                            accumulated_content, continuation_prefix
                        )
                        overlap_resolved = True
                        buffered_content += continuation_prefix[overlap_characters:]
                        continuation_prefix = ""
                        if overlap_characters:
                            generation_log(
                                "overlap_removed",
                                request_id=attempt_request_id,
                                model=model,
                                role=payload.role,
                                attempt=provider_call_count,
                                started_at=segment_started_at,
                                visible_characters=len(accumulated_content),
                                reasoning_present=reasoning_present,
                                logical_generation_id=logical_generation_id,
                                segment=prospective_segment,
                                overlap_characters=overlap_characters,
                            )
                    else:
                        buffered_content += event.content

                    if not visible_started and has_visible_content(buffered_content):
                        visible_started = True
                        segment_content += buffered_content
                        generation_log(
                            "visible_content_started",
                            request_id=attempt_request_id,
                            model=model,
                            role=payload.role,
                            attempt=provider_call_count,
                            started_at=segment_started_at,
                            visible_characters=(len(accumulated_content) + len(segment_content)),
                            reasoning_present=reasoning_present,
                            logical_generation_id=logical_generation_id,
                            segment=prospective_segment,
                        )
                        yield ndjson_event("token", content=buffered_content)
                        buffered_content = ""
                    elif visible_started and buffered_content:
                        segment_content += buffered_content
                        yield ndjson_event("token", content=buffered_content)
                        buffered_content = ""
            except asyncio.CancelledError:
                generation_log(
                    ("continuation_cancelled" if continuation_phase else "generation_cancelled"),
                    request_id=attempt_request_id,
                    model=model,
                    role=payload.role,
                    attempt=provider_call_count,
                    started_at=segment_started_at,
                    visible_characters=len(accumulated_content) + len(segment_content),
                    reasoning_present=reasoning_present,
                    finish_reason=finish_reason,
                    usage=usage,
                    logical_generation_id=logical_generation_id,
                    segment=prospective_segment,
                )
                raise
            except (ModelNotFoundError, ProviderUnavailableError, ProviderResponseError) as exc:
                yield ndjson_event("error", detail=str(exc))
                return

            for key, value in usage.items():
                aggregate_usage[key] = aggregate_usage.get(key, 0) + value
            aggregate_reasoning_present = aggregate_reasoning_present or reasoning_present

            if continuation_phase and not overlap_resolved:
                overlap_characters = exact_overlap_size(accumulated_content, continuation_prefix)
                buffered_content += continuation_prefix[overlap_characters:]
                if overlap_characters:
                    generation_log(
                        "overlap_removed",
                        request_id=attempt_request_id,
                        model=model,
                        role=payload.role,
                        attempt=provider_call_count,
                        started_at=segment_started_at,
                        visible_characters=len(accumulated_content),
                        reasoning_present=reasoning_present,
                        logical_generation_id=logical_generation_id,
                        segment=prospective_segment,
                        overlap_characters=overlap_characters,
                    )
                if has_visible_content(buffered_content):
                    visible_started = True
                    segment_content += buffered_content
                    generation_log(
                        "visible_content_started",
                        request_id=attempt_request_id,
                        model=model,
                        role=payload.role,
                        attempt=provider_call_count,
                        started_at=segment_started_at,
                        visible_characters=(len(accumulated_content) + len(segment_content)),
                        reasoning_present=reasoning_present,
                        logical_generation_id=logical_generation_id,
                        segment=prospective_segment,
                    )
                    yield ndjson_event("token", content=buffered_content)

            if visible_started:
                segment_count += 1
                accumulated_content += segment_content
                if retrying_empty_segment:
                    generation_log(
                        "retry_succeeded",
                        request_id=attempt_request_id,
                        model=model,
                        role=payload.role,
                        attempt=provider_call_count,
                        started_at=segment_started_at,
                        visible_characters=len(accumulated_content),
                        reasoning_present=aggregate_reasoning_present,
                        finish_reason=finish_reason,
                        usage=aggregate_usage,
                        logical_generation_id=logical_generation_id,
                        segment=segment_count,
                    )
                retrying_empty_segment = False

                if finish_reason == "length":
                    generation_log(
                        "truncation_detected",
                        request_id=attempt_request_id,
                        model=model,
                        role=payload.role,
                        attempt=provider_call_count,
                        started_at=segment_started_at,
                        visible_characters=len(accumulated_content),
                        reasoning_present=aggregate_reasoning_present,
                        finish_reason=finish_reason,
                        usage=aggregate_usage,
                        logical_generation_id=logical_generation_id,
                        segment=segment_count,
                    )
                    can_continue_automatically = (
                        not payload.manual_continuation
                        and phase == "initial"
                        and automatic_continuation_count < MAX_AUTOMATIC_CONTINUATIONS
                        and provider_call_count < MAX_PROVIDER_CALLS
                    )
                    if can_continue_automatically:
                        automatic_continuation_count += 1
                        phase = "continuation"
                        try:
                            yield ndjson_event("continuation", active=True)
                        except (asyncio.CancelledError, GeneratorExit):
                            generation_log(
                                "continuation_cancelled",
                                request_id=attempt_request_id,
                                model=model,
                                role=payload.role,
                                attempt=provider_call_count,
                                started_at=segment_started_at,
                                visible_characters=len(accumulated_content),
                                reasoning_present=reasoning_present,
                                finish_reason=finish_reason,
                                usage=aggregate_usage,
                                logical_generation_id=logical_generation_id,
                                segment=segment_count,
                            )
                            raise
                        continue

                    generation_log(
                        "continuation_limit_reached",
                        request_id=attempt_request_id,
                        model=model,
                        role=payload.role,
                        attempt=provider_call_count,
                        started_at=segment_started_at,
                        visible_characters=len(accumulated_content),
                        reasoning_present=reasoning_present,
                        finish_reason=finish_reason,
                        usage=aggregate_usage,
                        logical_generation_id=logical_generation_id,
                        segment=segment_count,
                    )
                    if continuation_phase:
                        yield ndjson_event("continuation", active=False)
                    saved_session = record_chat_session(db, model, started_at, learning_session)
                    generation_log(
                        "generation_completed",
                        request_id=attempt_request_id,
                        model=model,
                        role=payload.role,
                        attempt=provider_call_count,
                        started_at=total_started_at,
                        visible_characters=len(accumulated_content),
                        reasoning_present=aggregate_reasoning_present,
                        finish_reason=finish_reason,
                        usage=aggregate_usage,
                        logical_generation_id=logical_generation_id,
                        segment=segment_count,
                    )
                    yield ndjson_event(
                        "done",
                        model=model,
                        session_id=saved_session.id,
                        attempt_count=provider_call_count,
                        recovery=recovery,
                        finish_reason=finish_reason,
                        logical_generation_id=str(logical_generation_id),
                        segment_count=segment_count,
                        automatic_continuation_count=automatic_continuation_count,
                        manual_continuation_count=manual_continuation_count,
                        visible_character_count=len(accumulated_content),
                        continuation_available=True,
                    )
                    return

                if continuation_phase:
                    yield ndjson_event("continuation", active=False)
                    if not payload.manual_continuation:
                        generation_log(
                            "automatic_continuation_succeeded",
                            request_id=attempt_request_id,
                            model=model,
                            role=payload.role,
                            attempt=provider_call_count,
                            started_at=segment_started_at,
                            visible_characters=len(accumulated_content),
                            reasoning_present=reasoning_present,
                            finish_reason=finish_reason,
                            usage=usage,
                            logical_generation_id=logical_generation_id,
                            segment=segment_count,
                        )
                saved_session = record_chat_session(db, model, started_at, learning_session)
                generation_log(
                    "generation_completed",
                    request_id=attempt_request_id,
                    model=model,
                    role=payload.role,
                    attempt=provider_call_count,
                    started_at=total_started_at,
                    visible_characters=len(accumulated_content),
                    reasoning_present=aggregate_reasoning_present,
                    finish_reason=finish_reason,
                    usage=aggregate_usage,
                    logical_generation_id=logical_generation_id,
                    segment=segment_count,
                )
                yield ndjson_event(
                    "done",
                    model=model,
                    session_id=saved_session.id,
                    attempt_count=provider_call_count,
                    recovery=recovery,
                    finish_reason=finish_reason,
                    logical_generation_id=str(logical_generation_id),
                    segment_count=segment_count,
                    automatic_continuation_count=automatic_continuation_count,
                    manual_continuation_count=manual_continuation_count,
                    visible_character_count=len(accumulated_content),
                    continuation_available=False,
                )
                return

            generation_log(
                "empty_visible_content_detected",
                request_id=attempt_request_id,
                model=model,
                role=payload.role,
                attempt=provider_call_count,
                started_at=segment_started_at,
                visible_characters=len(accumulated_content),
                reasoning_present=reasoning_present,
                finish_reason=finish_reason,
                usage=usage,
                logical_generation_id=logical_generation_id,
                segment=prospective_segment,
            )
            if empty_retry_count < 1 and provider_call_count < MAX_PROVIDER_CALLS:
                empty_retry_count += 1
                recovery = "empty_visible_content"
                retrying_empty_segment = True
                continue

            if accumulated_content:
                if continuation_phase:
                    yield ndjson_event("continuation", active=False)
                saved_session = record_chat_session(db, model, started_at, learning_session)
                generation_log(
                    "generation_completed",
                    request_id=attempt_request_id,
                    model=model,
                    role=payload.role,
                    attempt=provider_call_count,
                    started_at=total_started_at,
                    visible_characters=len(accumulated_content),
                    reasoning_present=aggregate_reasoning_present,
                    finish_reason=finish_reason,
                    usage=aggregate_usage,
                    logical_generation_id=logical_generation_id,
                    segment=segment_count,
                )
                yield ndjson_event(
                    "done",
                    model=model,
                    session_id=saved_session.id,
                    attempt_count=provider_call_count,
                    recovery=recovery,
                    finish_reason=finish_reason,
                    logical_generation_id=str(logical_generation_id),
                    segment_count=segment_count,
                    automatic_continuation_count=automatic_continuation_count,
                    manual_continuation_count=manual_continuation_count,
                    visible_character_count=len(accumulated_content),
                    continuation_available=True,
                )
                return

            generation_log(
                "retry_failed",
                request_id=attempt_request_id,
                model=model,
                role=payload.role,
                attempt=provider_call_count,
                started_at=segment_started_at,
                reasoning_present=aggregate_reasoning_present,
                finish_reason=finish_reason,
                usage=aggregate_usage,
                logical_generation_id=logical_generation_id,
                segment=prospective_segment,
            )
            yield ndjson_event("error", detail=EMPTY_RESPONSE_ERROR, retryable=True)
            return

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@router.get("/api/mistakes", response_model=list[MistakeRead])
def mistakes(db: Session = Depends(get_db)) -> list[MistakeRead]:
    rows = db.scalars(select(Mistake).order_by(Mistake.last_seen_at.desc())).all()
    return [MistakeRead.model_validate(row) for row in rows]


@router.get("/api/sessions", response_model=list[SessionRead])
def sessions(db: Session = Depends(get_db)) -> list[SessionRead]:
    rows = db.scalars(select(LearningSession).order_by(LearningSession.started_at.desc())).all()
    return [SessionRead.model_validate(row) for row in rows]
