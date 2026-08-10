from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from llc_api.core.config import Settings, get_settings
from llc_api.db.session import get_db
from llc_api.learning_engine.schemas import (
    AttemptCreate,
    AttemptReceipt,
    CurriculumRead,
    DailyPlanCreate,
    DailyPlanRead,
    LearningSkillRead,
    ReviewsRead,
)
from llc_api.learning_engine.service import (
    CorrectionConflictError,
    CurriculumUnavailableError,
    DailyPlanNotFoundError,
    IdempotencyConflictError,
    IncompatibleExerciseTypeError,
    LearningEngineBusyError,
    LearningEngineError,
    MissingProfileError,
    UnknownEvidenceError,
    UnknownSkillError,
    create_daily_plan,
    get_curriculum,
    get_today_plan,
    list_due_reviews,
    list_skills,
    record_attempt,
)

router = APIRouter(prefix="/api/learning", tags=["learning-engine"])


def _service_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail=str(exc))


@router.get("/curriculum", response_model=CurriculumRead)
def curriculum(db: Session = Depends(get_db)) -> CurriculumRead:
    try:
        return get_curriculum(db)
    except CurriculumUnavailableError as exc:
        raise _service_unavailable(exc) from exc


@router.get("/today", response_model=DailyPlanRead)
def today(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DailyPlanRead:
    try:
        return get_today_plan(db, timezone_name=settings.timezone)
    except DailyPlanNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LearningEngineError as exc:
        raise _service_unavailable(exc) from exc


@router.post(
    "/daily-plan",
    response_model=DailyPlanRead,
    status_code=status.HTTP_201_CREATED,
)
def daily_plan(
    payload: DailyPlanCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DailyPlanRead:
    try:
        return create_daily_plan(
            db,
            available_minutes=payload.available_minutes,
            motivation=payload.motivation,
            timezone_name=settings.timezone,
        )
    except MissingProfileError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LearningEngineBusyError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
            headers={"Retry-After": "1"},
        ) from exc
    except CurriculumUnavailableError as exc:
        raise _service_unavailable(exc) from exc
    except LearningEngineError as exc:
        raise _service_unavailable(exc) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="El plan entró en conflicto con el estado local; vuelve a intentarlo.",
        ) from exc


@router.get("/reviews", response_model=ReviewsRead)
def reviews(db: Session = Depends(get_db)) -> ReviewsRead:
    try:
        return list_due_reviews(db)
    except CurriculumUnavailableError as exc:
        raise _service_unavailable(exc) from exc


@router.get("/skills", response_model=list[LearningSkillRead])
def skills(db: Session = Depends(get_db)) -> list[LearningSkillRead]:
    try:
        return list_skills(db)
    except CurriculumUnavailableError as exc:
        raise _service_unavailable(exc) from exc


@router.post(
    "/attempts",
    response_model=AttemptReceipt,
    status_code=status.HTTP_201_CREATED,
)
def evaluated_attempt(
    payload: AttemptCreate,
    response: Response,
    db: Session = Depends(get_db),
) -> AttemptReceipt:
    try:
        receipt = record_attempt(db, payload)
    except (UnknownSkillError, UnknownEvidenceError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IncompatibleExerciseTypeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (IdempotencyConflictError, CorrectionConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LearningEngineBusyError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
            headers={"Retry-After": "1"},
        ) from exc
    except CurriculumUnavailableError as exc:
        raise _service_unavailable(exc) from exc
    except LearningEngineError as exc:
        raise _service_unavailable(exc) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="El intento evaluado entró en conflicto con evidencia existente.",
        ) from exc
    response.status_code = status.HTTP_201_CREATED if receipt.created else status.HTTP_200_OK
    return receipt
