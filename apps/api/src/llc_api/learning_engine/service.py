"""Database orchestration for the deterministic Learning Engine.

The pure modules in this package own every pedagogical decision.  This module
only loads auditable local state, applies those rules, and commits their result
atomically.  It deliberately has no model-provider dependency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, aliased, joinedload, selectinload

from llc_api.core.time import local_date, utc_now
from llc_api.learning_engine.curriculum import (
    CURRICULUM,
    CURRICULUM_VERSION,
    SKILLS_BY_CODE,
    MasteryCriteria,
    unmet_prerequisites,
)
from llc_api.learning_engine.curriculum import (
    CurriculumSkill as CurriculumDefinition,
)
from llc_api.learning_engine.planner import (
    SkillProgress,
    build_daily_plan,
)
from llc_api.learning_engine.reviews import (
    ReviewCandidate,
    calculate_review_schedule,
    rank_due_reviews,
)
from llc_api.learning_engine.schemas import (
    AttemptCreate,
    AttemptReceipt,
    CurriculumRead,
    CurriculumSkillRead,
    DailyPlanBlockRead,
    DailyPlanRead,
    LearningSkillRead,
    MasteryCriteriaRead,
    ReviewRead,
    ReviewsRead,
    StudentSkillStateRead,
)
from llc_api.learning_engine.scoring import (
    ENGINE_VERSION,
    AttemptOutcome,
    calculate_mastery_update,
    is_internally_mastered,
    score_for_outcome,
)
from llc_api.models import (
    Curriculum,
    CurriculumSkill,
    DailyPlan,
    DailyPlanBlock,
    ExerciseAttempt,
    LearningSession,
    Skill,
    SkillEvidence,
    SkillPrerequisite,
    StudentProfile,
    StudentSkill,
)


class LearningEngineError(RuntimeError):
    """Base class for expected service failures."""


class LearningEngineBusyError(LearningEngineError):
    """Raised when another local write holds SQLite beyond its busy timeout."""


class CurriculumUnavailableError(LearningEngineError):
    pass


class UnknownSkillError(LookupError):
    pass


class UnknownEvidenceError(LookupError):
    pass


class IncompatibleExerciseTypeError(LearningEngineError):
    pass


class IdempotencyConflictError(LearningEngineError):
    pass


class CorrectionConflictError(LearningEngineError):
    pass


class MissingProfileError(LearningEngineError):
    pass


class DailyPlanNotFoundError(LookupError):
    pass


def _is_sqlite_lock_error(exc: OperationalError) -> bool:
    message = str(exc.orig if exc.orig is not None else exc).lower()
    return "database is locked" in message or "database is busy" in message


def _begin_serialized_write(db: Session) -> None:
    """Acquire SQLite's reserved write lock before reading mutable engine state.

    SQLite transactions otherwise start deferred: two requests can read the same
    StudentSkill, append distinct immutable evidence, and then overwrite the
    aggregate in turn.  BEGIN IMMEDIATE makes the whole read/recompute/write
    cycle single-writer while still allowing normal readers.
    """

    connection = db.connection()
    if connection.dialect.name != "sqlite":  # Defensive; settings currently reject this.
        return
    raw_connection = connection.connection.driver_connection
    try:
        if getattr(raw_connection, "in_transaction", False):
            # A caller may already own a transaction. This no-op write upgrades a
            # deferred transaction before this service performs any of its reads.
            connection.execute(text("UPDATE student_profiles SET id = id WHERE 0"))
        else:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
    except OperationalError as exc:
        db.rollback()
        if _is_sqlite_lock_error(exc):
            raise LearningEngineBusyError(
                "La base local está ocupada por otra actualización; vuelve a intentarlo."
            ) from exc
        raise


@dataclass(frozen=True, slots=True)
class _CurriculumRow:
    definition: CurriculumDefinition
    skill: Skill
    membership: CurriculumSkill


@dataclass(frozen=True, slots=True)
class _Aggregate:
    estimated_mastery: float = 0.0
    confidence: float = 0.0
    evidence_count: int = 0
    last_outcome: AttemptOutcome | None = None
    unassisted_streak: int = 0
    lapse_count: int = 0
    last_practised_at: datetime | None = None
    next_review_at: datetime | None = None
    mastered_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class _Observation:
    outcome: AttemptOutcome
    observed_at: datetime
    stable_order: int


def _load_curriculum(db: Session) -> tuple[Curriculum, tuple[_CurriculumRow, ...]]:
    curriculum = db.scalar(
        select(Curriculum)
        .where(Curriculum.is_active.is_(True))
        .order_by(Curriculum.created_at.desc(), Curriculum.version.desc())
        .limit(1)
    )
    if curriculum is None:
        raise CurriculumUnavailableError(
            "No hay un currículo activo. Aplica las migraciones locales pendientes."
        )
    if curriculum.version != CURRICULUM_VERSION:
        raise CurriculumUnavailableError(
            "El currículo activo de la base no coincide con la versión del Learning Engine."
        )

    stored = db.execute(
        select(Skill, CurriculumSkill)
        .join(CurriculumSkill, CurriculumSkill.skill_id == Skill.id)
        .where(CurriculumSkill.curriculum_version == curriculum.version)
        .order_by(CurriculumSkill.curriculum_order, Skill.code)
    ).all()
    stored_by_code = {skill.code: (skill, membership) for skill, membership in stored}
    expected_codes = set(SKILLS_BY_CODE)
    if set(stored_by_code) != expected_codes:
        raise CurriculumUnavailableError(
            "El currículo persistido está incompleto o no coincide con su definición versionada."
        )

    prerequisite_skill = aliased(Skill)
    persisted_prerequisites: dict[str, list[str]] = {}
    for skill_code, prerequisite_code in db.execute(
        select(Skill.code, prerequisite_skill.code)
        .select_from(SkillPrerequisite)
        .join(Skill, Skill.id == SkillPrerequisite.skill_id)
        .join(
            prerequisite_skill,
            prerequisite_skill.id == SkillPrerequisite.prerequisite_skill_id,
        )
        .where(SkillPrerequisite.curriculum_version == curriculum.version)
        .order_by(Skill.code, SkillPrerequisite.prerequisite_order)
    ):
        persisted_prerequisites.setdefault(skill_code, []).append(prerequisite_code)

    rows: list[_CurriculumRow] = []
    for definition in CURRICULUM:
        skill, membership = stored_by_code[definition.code]
        if membership.curriculum_order != definition.curriculum_order:
            raise CurriculumUnavailableError(
                f"El orden persistido de '{definition.code}' no coincide con el currículo."
            )
        persisted_metadata = (
            skill.name,
            skill.category,
            skill.description,
            skill.cefr_level,
            membership.cefr_reference,
            membership.difficulty,
            membership.min_mastery,
            membership.min_confidence,
            membership.min_evidence,
            membership.unassisted_streak,
            tuple(_exercise_types(membership)),
            tuple(persisted_prerequisites.get(skill.code, [])),
        )
        expected_metadata = (
            definition.name,
            definition.category,
            definition.description,
            definition.cefr_reference,
            definition.cefr_reference,
            definition.difficulty,
            definition.mastery_criteria.min_mastery,
            definition.mastery_criteria.min_confidence,
            definition.mastery_criteria.min_evidence,
            definition.mastery_criteria.unassisted_streak,
            definition.exercise_types,
            definition.prerequisite_codes,
        )
        if persisted_metadata != expected_metadata:
            raise CurriculumUnavailableError(
                f"La definición persistida de '{definition.code}' no coincide con su versión."
            )
        rows.append(
            _CurriculumRow(
                definition=definition,
                skill=skill,
                membership=membership,
            )
        )
    return curriculum, tuple(rows)


def _exercise_types(membership: CurriculumSkill) -> list[str]:
    try:
        value = json.loads(membership.exercise_types)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CurriculumUnavailableError(
            "El currículo persistido contiene tipos de ejercicio inválidos."
        ) from exc
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise CurriculumUnavailableError(
            "El currículo persistido contiene tipos de ejercicio inválidos."
        )
    return value


def _criteria(row: _CurriculumRow) -> MasteryCriteria:
    membership = row.membership
    try:
        return MasteryCriteria(
            min_mastery=membership.min_mastery,
            min_confidence=membership.min_confidence,
            min_evidence=membership.min_evidence,
            unassisted_streak=membership.unassisted_streak,
        )
    except ValueError as exc:
        raise CurriculumUnavailableError(
            f"Los criterios persistidos de '{row.skill.code}' son inválidos."
        ) from exc


def _curriculum_skill_read(row: _CurriculumRow, *, is_active: bool) -> CurriculumSkillRead:
    membership = row.membership
    criteria = _criteria(row)
    return CurriculumSkillRead(
        code=row.skill.code,
        name=row.skill.name,
        category=row.skill.category,
        description=row.skill.description,
        cefr_hint="pre-A1" if membership.cefr_reference == "A0" else "A1",
        order=membership.curriculum_order,
        prerequisite_codes=list(row.definition.prerequisite_codes),
        mastery_criteria=MasteryCriteriaRead(
            min_mastery=criteria.min_mastery,
            min_confidence=criteria.min_confidence,
            min_evidence=criteria.min_evidence,
            min_unassisted_streak=criteria.unassisted_streak,
        ),
        difficulty=membership.difficulty,
        compatible_exercise_types=_exercise_types(membership),
        curriculum_version=membership.curriculum_version,
        is_active=is_active,
    )


def _state_is_mastered(state: StudentSkill, criteria: MasteryCriteria) -> bool:
    return is_internally_mastered(
        estimated_mastery=state.estimated_mastery,
        confidence=state.confidence,
        evidence_count=state.evidence_count,
        unassisted_streak=state.unassisted_streak,
        criteria=criteria,
    )


def _state_read(
    skill_code: str, state: StudentSkill, criteria: MasteryCriteria
) -> StudentSkillStateRead:
    return StudentSkillStateRead(
        skill_code=skill_code,
        estimated_mastery=state.estimated_mastery,
        confidence=state.confidence,
        evidence_count=state.evidence_count,
        last_practised_at=state.last_practised_at,
        next_review_at=state.next_review_at,
        last_outcome=state.last_outcome,
        unassisted_streak=state.unassisted_streak,
        internally_mastered=_state_is_mastered(state, criteria),
        mastered_at=state.mastered_at,
    )


def get_curriculum(db: Session) -> CurriculumRead:
    curriculum, rows = _load_curriculum(db)
    return CurriculumRead(
        version=curriculum.version,
        skills=[_curriculum_skill_read(row, is_active=True) for row in rows],
    )


def list_skills(db: Session) -> list[LearningSkillRead]:
    _curriculum, rows = _load_curriculum(db)
    states = {
        state.skill_id: state
        for state in db.scalars(
            select(StudentSkill).where(StudentSkill.skill_id.in_([row.skill.id for row in rows]))
        )
    }
    mastered_codes = {
        row.skill.code
        for row in rows
        if (state := states.get(row.skill.id)) is not None
        and _state_is_mastered(state, _criteria(row))
    }

    result: list[LearningSkillRead] = []
    for row in rows:
        base = _curriculum_skill_read(row, is_active=True)
        state = states.get(row.skill.id)
        unmet = list(unmet_prerequisites(row.definition, mastered_codes))
        result.append(
            LearningSkillRead(
                **base.model_dump(),
                state=(
                    _state_read(row.skill.code, state, _criteria(row))
                    if state is not None
                    else None
                ),
                eligible_for_introduction=state is None and not unmet,
                unmet_prerequisites=unmet,
            )
        )
    return result


def list_due_reviews(db: Session, *, as_of: datetime | None = None) -> ReviewsRead:
    now = as_of or utc_now()
    _curriculum, rows = _load_curriculum(db)
    states = {
        state.skill_id: state
        for state in db.scalars(
            select(StudentSkill).where(StudentSkill.skill_id.in_([row.skill.id for row in rows]))
        )
    }
    candidates = [
        ReviewCandidate(
            skill_code=row.skill.code,
            due_at=state.next_review_at,
            estimated_mastery=state.estimated_mastery,
            confidence=state.confidence,
            evidence_count=state.evidence_count,
            curriculum_order=row.membership.curriculum_order,
            last_outcome=(
                AttemptOutcome(state.last_outcome) if state.last_outcome is not None else None
            ),
        )
        for row in rows
        if (state := states.get(row.skill.id)) is not None
    ]
    ranked = rank_due_reviews(candidates, as_of=now)
    names = {row.skill.code: row.skill.name for row in rows}
    items = [
        ReviewRead(
            skill_code=item.skill_code,
            skill_name=names[item.skill_code],
            due_at=item.due_at,
            overdue=item.due_at < now,
            estimated_mastery=item.estimated_mastery,
            confidence=item.confidence,
            evidence_count=item.evidence_count,
        )
        for item in ranked
    ]
    return ReviewsRead(as_of=now, total_due=len(items), items=items)


def _legacy_outcome(score: float) -> AttemptOutcome:
    """Map immutable v1 evidence only; new requests never submit a numeric score."""

    if score < 0.2:
        return AttemptOutcome.FAILURE
    if score < 0.55:
        return AttemptOutcome.PARTIAL
    if score < 0.85:
        return AttemptOutcome.CORRECT_WITH_HELP
    return AttemptOutcome.CORRECT_WITHOUT_HELP


def _outcome_for_evidence(evidence: SkillEvidence) -> AttemptOutcome:
    if evidence.outcome is None:
        return _legacy_outcome(evidence.score)
    return AttemptOutcome(evidence.outcome)


def _root_observation(
    evidence: SkillEvidence, evidence_by_id: dict[int, SkillEvidence]
) -> tuple[datetime, int]:
    current = evidence
    visited: set[int] = set()
    while current.supersedes_evidence_id is not None:
        if current.id in visited:
            raise CorrectionConflictError("La cadena de correcciones contiene un ciclo.")
        visited.add(current.id)
        parent = evidence_by_id.get(current.supersedes_evidence_id)
        if parent is None:
            raise CorrectionConflictError("La cadena de correcciones está incompleta.")
        current = parent
    return current.created_at, current.id


def _effective_observations(
    evidence_rows: list[SkillEvidence],
    *,
    pending_outcome: AttemptOutcome,
    observed_at: datetime,
    corrected: SkillEvidence | None,
) -> list[_Observation]:
    evidence_by_id = {evidence.id: evidence for evidence in evidence_rows}
    superseded_ids = {
        evidence.supersedes_evidence_id
        for evidence in evidence_rows
        if evidence.supersedes_evidence_id is not None
    }
    if corrected is not None:
        superseded_ids.add(corrected.id)

    observations = [
        _Observation(
            outcome=_outcome_for_evidence(evidence),
            observed_at=_root_observation(evidence, evidence_by_id)[0],
            stable_order=_root_observation(evidence, evidence_by_id)[1],
        )
        for evidence in evidence_rows
        if evidence.id not in superseded_ids
    ]
    if corrected is None:
        pending_observed_at = observed_at
        pending_order = max(evidence_by_id, default=0) + 1
    else:
        pending_observed_at, pending_order = _root_observation(corrected, evidence_by_id)
    observations.append(
        _Observation(
            outcome=pending_outcome,
            observed_at=pending_observed_at,
            stable_order=pending_order,
        )
    )
    observations.sort(key=lambda item: (item.observed_at, item.stable_order))
    return observations


def _aggregate(observations: list[_Observation], *, criteria: MasteryCriteria) -> _Aggregate:
    aggregate = _Aggregate()
    for observation in observations:
        update = calculate_mastery_update(
            current_mastery=aggregate.estimated_mastery,
            evidence_count=aggregate.evidence_count,
            outcome=observation.outcome,
            current_unassisted_streak=aggregate.unassisted_streak,
            current_lapse_count=aggregate.lapse_count,
        )
        schedule = calculate_review_schedule(
            outcome=observation.outcome,
            observed_at=observation.observed_at,
            unassisted_streak=update.unassisted_streak,
        )
        mastered = is_internally_mastered(
            estimated_mastery=update.estimated_mastery,
            confidence=update.confidence,
            evidence_count=update.evidence_count,
            unassisted_streak=update.unassisted_streak,
            criteria=criteria,
        )
        if mastered:
            mastered_at = aggregate.mastered_at or observation.observed_at
        else:
            mastered_at = None
        aggregate = _Aggregate(
            estimated_mastery=update.estimated_mastery,
            confidence=update.confidence,
            evidence_count=update.evidence_count,
            last_outcome=update.last_outcome,
            unassisted_streak=update.unassisted_streak,
            lapse_count=update.lapse_count,
            last_practised_at=observation.observed_at,
            next_review_at=schedule.next_review_at,
            mastered_at=mastered_at,
        )
    return aggregate


def _evidence_matches(db: Session, evidence: SkillEvidence, payload: AttemptCreate) -> bool:
    attempt = evidence.exercise_attempt
    if payload.corrects_submission_id is None:
        corrected_id = None
    else:
        corrected_id = db.scalar(
            select(SkillEvidence.id).where(
                SkillEvidence.submission_id == str(payload.corrects_submission_id)
            )
        )
        if corrected_id is None:
            return False
    return (
        evidence.skill.code == payload.skill_code
        and evidence.source == payload.source
        and _outcome_for_evidence(evidence) == AttemptOutcome(payload.outcome)
        and evidence.supersedes_evidence_id == corrected_id
        and attempt.exercise_type == payload.exercise_type
        and attempt.prompt == payload.prompt
        and attempt.student_answer == payload.student_answer
        and attempt.expected_answer == payload.expected_answer
        and attempt.feedback == payload.feedback
    )


def _receipt(
    evidence: SkillEvidence,
    state: StudentSkill,
    criteria: MasteryCriteria,
    *,
    created: bool,
) -> AttemptReceipt:
    return AttemptReceipt(
        created=created,
        evidence_id=evidence.id,
        corrected_evidence_id=evidence.supersedes_evidence_id,
        exercise_attempt_id=evidence.exercise_attempt_id,
        learning_session_id=evidence.exercise_attempt.learning_session_id,
        engine_version=evidence.engine_version,
        outcome=_outcome_for_evidence(evidence).value,
        derived_score=evidence.score,
        state=_state_read(evidence.skill.code, state, criteria),
    )


def _existing_receipt(
    db: Session, payload: AttemptCreate, criteria: MasteryCriteria
) -> AttemptReceipt | None:
    evidence = db.scalar(
        select(SkillEvidence)
        .options(
            joinedload(SkillEvidence.exercise_attempt),
            joinedload(SkillEvidence.skill),
        )
        .where(SkillEvidence.submission_id == str(payload.submission_id))
    )
    if evidence is None:
        return None
    if not _evidence_matches(db, evidence, payload):
        raise IdempotencyConflictError(
            "submission_id ya fue usado con un intento evaluado diferente."
        )
    state = db.scalar(select(StudentSkill).where(StudentSkill.skill_id == evidence.skill_id))
    if state is None:
        raise LearningEngineError("La evidencia existe pero su estado agregado no está disponible.")
    return _receipt(evidence, state, criteria, created=False)


def _record_attempt_in_transaction(db: Session, payload: AttemptCreate) -> AttemptReceipt:
    _curriculum, curriculum_rows = _load_curriculum(db)
    row_by_code = {row.skill.code: row for row in curriculum_rows}
    row = row_by_code.get(payload.skill_code)
    if row is None:
        raise UnknownSkillError(
            f"La habilidad '{payload.skill_code}' no pertenece al currículo activo."
        )
    criteria = _criteria(row)
    prior = _existing_receipt(db, payload, criteria)
    if prior is not None:
        return prior

    compatible_types = set(_exercise_types(row.membership))
    if payload.exercise_type not in compatible_types:
        raise IncompatibleExerciseTypeError(
            f"El tipo '{payload.exercise_type}' no es compatible con '{payload.skill_code}'."
        )

    corrected: SkillEvidence | None = None
    if payload.corrects_submission_id is not None:
        corrected = db.scalar(
            select(SkillEvidence).where(
                SkillEvidence.submission_id == str(payload.corrects_submission_id)
            )
        )
        if corrected is None:
            raise UnknownEvidenceError("La evidencia que se quiere corregir no existe.")
        if corrected.skill_id != row.skill.id:
            raise CorrectionConflictError(
                "Una corrección debe pertenecer a la misma habilidad que la evidencia original."
            )
        existing_correction = db.scalar(
            select(SkillEvidence.id).where(SkillEvidence.supersedes_evidence_id == corrected.id)
        )
        if existing_correction is not None:
            raise CorrectionConflictError(
                "La evidencia indicada ya fue sustituida; corrige la última valoración de la cadena."
            )

    now = utc_now()
    state = db.scalar(select(StudentSkill).where(StudentSkill.skill_id == row.skill.id))
    before = _Aggregate(
        estimated_mastery=state.estimated_mastery if state is not None else 0.0,
        confidence=state.confidence if state is not None else 0.0,
        evidence_count=state.evidence_count if state is not None else 0,
        last_outcome=(
            AttemptOutcome(state.last_outcome)
            if state is not None and state.last_outcome is not None
            else None
        ),
        unassisted_streak=state.unassisted_streak if state is not None else 0,
        lapse_count=state.lapse_count if state is not None else 0,
        last_practised_at=state.last_practised_at if state is not None else None,
        next_review_at=state.next_review_at if state is not None else None,
        mastered_at=state.mastered_at if state is not None else None,
    )
    evidence_rows = list(
        db.scalars(
            select(SkillEvidence)
            .where(SkillEvidence.skill_id == row.skill.id)
            .order_by(SkillEvidence.created_at, SkillEvidence.id)
        )
    )
    outcome = AttemptOutcome(payload.outcome)
    after = _aggregate(
        _effective_observations(
            evidence_rows,
            pending_outcome=outcome,
            observed_at=now,
            corrected=corrected,
        ),
        criteria=criteria,
    )

    if state is None:
        state = StudentSkill(skill_id=row.skill.id, updated_at=now)
        db.add(state)

    learning_session = LearningSession(
        session_type=(
            "corrected_assessment"
            if corrected is not None
            else ("diagnostic_attempt" if payload.source == "diagnostic" else "evaluated_practice")
        ),
        started_at=now,
        completed_at=now,
        duration_seconds=0,
        model_used=None,
        summary=f"Resultado estructurado para {row.skill.code}; contenido local por separado.",
    )
    attempt = ExerciseAttempt(
        learning_session=learning_session,
        exercise_type=payload.exercise_type,
        prompt=payload.prompt,
        student_answer=payload.student_answer,
        expected_answer=payload.expected_answer,
        score=score_for_outcome(outcome),
        feedback=payload.feedback,
        created_at=now,
    )
    evidence = SkillEvidence(
        submission_id=str(payload.submission_id),
        exercise_attempt=attempt,
        skill_id=row.skill.id,
        score=score_for_outcome(outcome),
        source=payload.source,
        engine_version=ENGINE_VERSION,
        outcome=outcome.value,
        supersedes_evidence_id=corrected.id if corrected is not None else None,
        mastery_before=before.estimated_mastery,
        mastery_after=after.estimated_mastery,
        confidence_before=before.confidence,
        confidence_after=after.confidence,
        evidence_count_before=before.evidence_count,
        evidence_count_after=after.evidence_count,
        unassisted_streak_before=before.unassisted_streak,
        unassisted_streak_after=after.unassisted_streak,
        lapse_count_before=before.lapse_count,
        lapse_count_after=after.lapse_count,
        next_review_at_before=before.next_review_at,
        next_review_at_after=after.next_review_at,
        created_at=now,
    )
    db.add_all([learning_session, attempt, evidence, state])
    try:
        db.flush()
        state.estimated_mastery = after.estimated_mastery
        state.confidence = after.confidence
        state.evidence_count = after.evidence_count
        state.last_practised_at = after.last_practised_at
        state.next_review_at = after.next_review_at
        state.last_outcome = after.last_outcome.value if after.last_outcome is not None else None
        state.unassisted_streak = after.unassisted_streak
        state.lapse_count = after.lapse_count
        state.mastered_at = after.mastered_at
        state.engine_version = ENGINE_VERSION
        state.last_evidence_id = evidence.id
        state.updated_at = now
        db.commit()
    except IntegrityError:
        db.rollback()
        raced = _existing_receipt(db, payload, criteria)
        if raced is not None:
            return raced
        raise
    except OperationalError as exc:
        db.rollback()
        if _is_sqlite_lock_error(exc):
            raise LearningEngineBusyError(
                "La base local está ocupada por otra actualización; vuelve a intentarlo."
            ) from exc
        raise
    return _receipt(evidence, state, criteria, created=True)


def record_attempt(db: Session, payload: AttemptCreate) -> AttemptReceipt:
    _begin_serialized_write(db)
    try:
        receipt = _record_attempt_in_transaction(db, payload)
    except Exception:
        if db.in_transaction():
            db.rollback()
        raise
    # Idempotent and conflict-race reads can start a new transaction after the
    # inner operation's commit/rollback. Never return while retaining a lock.
    if db.in_transaction():
        db.rollback()
    return receipt


def _progress(rows: tuple[_CurriculumRow, ...], states: dict[int, StudentSkill]):
    return {
        row.skill.code: SkillProgress(
            skill_code=row.skill.code,
            estimated_mastery=state.estimated_mastery,
            confidence=state.confidence,
            evidence_count=state.evidence_count,
            next_review_at=state.next_review_at,
            last_practised_at=state.last_practised_at,
            last_outcome=(
                AttemptOutcome(state.last_outcome) if state.last_outcome is not None else None
            ),
            unassisted_streak=state.unassisted_streak,
        )
        for row in rows
        if (state := states.get(row.skill.id)) is not None
    }


def _priority_categories(profile: StudentProfile) -> tuple[str, ...]:
    defaults = ("listening", "speaking")
    try:
        preferences = json.loads(profile.learning_preferences)
    except (TypeError, json.JSONDecodeError):
        return defaults
    if not isinstance(preferences, dict):
        return defaults
    configured = preferences.get("priority_categories")
    values = (
        tuple(item for item in configured if isinstance(item, str) and item)
        if isinstance(configured, list)
        else ()
    )
    legacy_priority = preferences.get("priority")
    priority_aliases = {
        "spontaneous_speech": "speaking",
        "conversation": "speaking",
        "listening_comprehension": "listening",
    }
    if isinstance(legacy_priority, str):
        legacy_values = (legacy_priority,)
    elif isinstance(legacy_priority, list):
        legacy_values = tuple(item for item in legacy_priority if isinstance(item, str) and item)
    else:
        legacy_values = ()
    values = (
        *values,
        *(priority_aliases.get(item, item) for item in legacy_values),
    )
    return tuple(dict.fromkeys((*defaults, *values)))


def _plan_options():
    return (
        joinedload(DailyPlan.primary_skill),
        joinedload(DailyPlan.new_skill),
        selectinload(DailyPlan.blocks).joinedload(DailyPlanBlock.skill),
    )


def _plan_read(plan: DailyPlan) -> DailyPlanRead:
    try:
        reason_codes = json.loads(plan.reason_codes)
    except (TypeError, json.JSONDecodeError) as exc:
        raise LearningEngineError("El plan guardado contiene motivos internos inválidos.") from exc
    if not isinstance(reason_codes, list) or not all(
        isinstance(reason, str) and reason for reason in reason_codes
    ):
        raise LearningEngineError("El plan guardado contiene motivos internos inválidos.")
    if not plan.blocks:
        raise LearningEngineError("El plan guardado no contiene bloques de aprendizaje.")
    if [block.position for block in plan.blocks] != list(range(1, len(plan.blocks) + 1)):
        raise LearningEngineError("El plan guardado contiene posiciones de bloque inválidas.")
    if sum(block.duration_minutes for block in plan.blocks) != plan.duration_minutes:
        raise LearningEngineError("La duración del plan no coincide con sus bloques guardados.")
    review_codes = list(
        dict.fromkeys(block.skill.code for block in plan.blocks if block.kind == "review")
    )
    return DailyPlanRead(
        id=plan.id,
        plan_date=plan.plan_date,
        engine_version=plan.engine_version,
        curriculum_version=plan.curriculum_version,
        generated_at=plan.generated_at,
        requested_minutes=plan.requested_minutes,
        duration_minutes=plan.duration_minutes,
        motivation=plan.motivation,
        objective=plan.objective,
        intensity=plan.intensity,
        primary_skill_code=plan.primary_skill.code,
        review_skill_codes=review_codes,
        new_skill_code=plan.new_skill.code if plan.new_skill is not None else None,
        blocks=[
            DailyPlanBlockRead(
                position=block.position,
                kind=block.kind,
                skill_code=block.skill.code,
                exercise_type=block.exercise_type,
                duration_minutes=block.duration_minutes,
                objective=block.objective,
                internal_reason=block.reason,
            )
            for block in plan.blocks
        ],
        internal_reason=plan.internal_reason,
        reason_codes=reason_codes,
    )


def _create_daily_plan_in_transaction(
    db: Session,
    *,
    available_minutes: int,
    motivation: int,
    timezone_name: str,
    generated_at: datetime | None = None,
) -> DailyPlanRead:
    now = generated_at or utc_now()
    curriculum, rows = _load_curriculum(db)
    profile = db.get(StudentProfile, 1)
    if profile is None:
        raise MissingProfileError("No hay un perfil local para generar el plan diario.")
    states = {
        state.skill_id: state
        for state in db.scalars(
            select(StudentSkill).where(StudentSkill.skill_id.in_([row.skill.id for row in rows]))
        )
    }
    last_exercise_type = db.scalar(
        select(ExerciseAttempt.exercise_type)
        .join(SkillEvidence, SkillEvidence.exercise_attempt_id == ExerciseAttempt.id)
        .where(SkillEvidence.skill_id.in_([row.skill.id for row in rows]))
        .order_by(ExerciseAttempt.created_at.desc(), ExerciseAttempt.id.desc())
        .limit(1)
    )
    planned = build_daily_plan(
        available_minutes=available_minutes,
        motivation=motivation,
        generated_at=now,
        progress_by_code=_progress(rows, states),
        last_exercise_type=last_exercise_type,
        priority_categories=_priority_categories(profile),
    )
    skill_by_code = {row.skill.code: row.skill for row in rows}
    plan = DailyPlan(
        profile_id=profile.id,
        plan_date=local_date(now, timezone_name),
        requested_minutes=planned.requested_minutes,
        duration_minutes=planned.duration_minutes,
        motivation=planned.motivation,
        intensity=planned.intensity.value,
        objective=planned.objective,
        primary_skill_id=skill_by_code[planned.primary_skill_code].id,
        new_skill_id=(
            skill_by_code[planned.new_skill_code].id if planned.new_skill_code is not None else None
        ),
        internal_reason=planned.internal_reason,
        reason_codes=json.dumps(list(planned.reason_codes), ensure_ascii=False),
        engine_version=planned.engine_version,
        curriculum_version=curriculum.version,
        generated_at=planned.generated_at,
    )
    plan.blocks = [
        DailyPlanBlock(
            position=block.position,
            kind=block.kind.value,
            skill_id=skill_by_code[block.skill_code].id,
            exercise_type=block.exercise_type,
            duration_minutes=block.duration_minutes,
            objective=block.objective,
            reason=block.internal_reason,
        )
        for block in planned.blocks
    ]
    db.add(plan)
    try:
        db.commit()
    except OperationalError as exc:
        db.rollback()
        if _is_sqlite_lock_error(exc):
            raise LearningEngineBusyError(
                "La base local está ocupada por otra actualización; vuelve a intentarlo."
            ) from exc
        raise
    plan = db.scalar(select(DailyPlan).options(*_plan_options()).where(DailyPlan.id == plan.id))
    if plan is None:  # pragma: no cover - a committed primary key cannot disappear locally
        raise LearningEngineError("El plan se guardó pero no pudo volver a cargarse.")
    return _plan_read(plan)


def create_daily_plan(
    db: Session,
    *,
    available_minutes: int,
    motivation: int,
    timezone_name: str,
    generated_at: datetime | None = None,
) -> DailyPlanRead:
    _begin_serialized_write(db)
    try:
        plan = _create_daily_plan_in_transaction(
            db,
            available_minutes=available_minutes,
            motivation=motivation,
            timezone_name=timezone_name,
            generated_at=generated_at,
        )
    except Exception:
        if db.in_transaction():
            db.rollback()
        raise
    # Reloading the committed plan creates a read transaction; close it before
    # handing the serializable result to the API.
    if db.in_transaction():
        db.rollback()
    return plan


def get_today_plan(
    db: Session, *, timezone_name: str, as_of: datetime | None = None
) -> DailyPlanRead:
    now = as_of or utc_now()
    plan = db.scalar(
        select(DailyPlan)
        .options(*_plan_options())
        .where(DailyPlan.plan_date == local_date(now, timezone_name))
        .order_by(DailyPlan.generated_at.desc(), DailyPlan.id.desc())
        .limit(1)
    )
    if plan is None:
        raise DailyPlanNotFoundError("Todavía no hay un plan guardado para hoy.")
    return _plan_read(plan)


# Compatibility name for callers of the provisional Milestone 0 service.
record_evaluated_attempt = record_attempt
