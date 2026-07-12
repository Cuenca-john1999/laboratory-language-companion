"""Pure deterministic Daily Planner."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import ceil

from deutschos_api.learning_engine.curriculum import (
    CURRICULUM,
    CURRICULUM_VERSION,
    CurriculumSkill,
    unmet_prerequisites,
    validate_curriculum,
)
from deutschos_api.learning_engine.reviews import ReviewCandidate, rank_due_reviews
from deutschos_api.learning_engine.scoring import (
    ENGINE_VERSION,
    AttemptOutcome,
    is_internally_mastered,
)


class PlanIntensity(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class PlanBlockKind(StrEnum):
    REVIEW = "review"
    NEW_SKILL = "new_skill"
    PRACTICE = "practice"


@dataclass(frozen=True, slots=True)
class SkillProgress:
    skill_code: str
    estimated_mastery: float
    confidence: float
    evidence_count: int
    next_review_at: datetime | None = None
    last_practised_at: datetime | None = None
    last_outcome: AttemptOutcome | None = None
    unassisted_streak: int = 0


@dataclass(frozen=True, slots=True)
class DailyPlanBlock:
    position: int
    kind: PlanBlockKind
    skill_code: str
    exercise_type: str
    duration_minutes: int
    objective: str
    internal_reason: str


@dataclass(frozen=True, slots=True)
class DailyPlan:
    engine_version: str
    curriculum_version: str
    generated_at: datetime
    requested_minutes: int
    duration_minutes: int
    motivation: int
    objective: str
    intensity: PlanIntensity
    primary_skill_code: str
    review_skill_codes: tuple[str, ...]
    new_skill_code: str | None
    blocks: tuple[DailyPlanBlock, ...]
    internal_reason: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _BlockSeed:
    kind: PlanBlockKind
    skill: CurriculumSkill
    rationale: str


LISTENING_TYPES = ("listening_choice", "listen_and_type")
SPEAKING_TYPES = ("guided_dialogue", "spoken_response")
COMMUNICATION_TYPES = frozenset((*LISTENING_TYPES, *SPEAKING_TYPES))
TYPE_ORDER: dict[PlanIntensity, tuple[str, ...]] = {
    PlanIntensity.LOW: (
        "recognition",
        "listening_choice",
        "gap_fill",
        "guided_dialogue",
        "sentence_building",
        "short_answer",
        "listen_and_type",
        "spoken_response",
    ),
    PlanIntensity.NORMAL: (
        "listening_choice",
        "guided_dialogue",
        "recognition",
        "gap_fill",
        "sentence_building",
        "listen_and_type",
        "short_answer",
        "spoken_response",
    ),
    PlanIntensity.HIGH: (
        "spoken_response",
        "listen_and_type",
        "guided_dialogue",
        "sentence_building",
        "short_answer",
        "listening_choice",
        "gap_fill",
        "recognition",
    ),
}


def intensity_for_motivation(motivation: int) -> PlanIntensity:
    if not 1 <= motivation <= 5:
        raise ValueError("motivation must be between 1 and 5")
    if motivation <= 2:
        return PlanIntensity.LOW
    if motivation <= 4:
        return PlanIntensity.NORMAL
    return PlanIntensity.HIGH


def _validate_progress(progress: SkillProgress) -> None:
    if not 0 <= progress.estimated_mastery <= 1:
        raise ValueError("estimated_mastery must be between 0 and 1")
    if not 0 <= progress.confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    if progress.evidence_count < 0:
        raise ValueError("evidence_count cannot be negative")
    if progress.unassisted_streak < 0:
        raise ValueError("unassisted_streak cannot be negative")


def _mastered_codes(
    progress_by_code: Mapping[str, SkillProgress], curriculum: Sequence[CurriculumSkill]
) -> frozenset[str]:
    mastered: set[str] = set()
    for skill in curriculum:
        progress = progress_by_code.get(skill.code)
        if progress is None:
            continue
        if is_internally_mastered(
            estimated_mastery=progress.estimated_mastery,
            confidence=progress.confidence,
            evidence_count=progress.evidence_count,
            unassisted_streak=progress.unassisted_streak,
            criteria=skill.mastery_criteria,
        ):
            mastered.add(skill.code)
    return frozenset(mastered)


def _block_count(available_minutes: int) -> int:
    # Two short blocks make a ten-minute session useful and permit alternation.
    return min(6, max(2, ceil(available_minutes / 10)))


def _duration_distribution(total_minutes: int, count: int) -> tuple[int, ...]:
    base, extra = divmod(total_minutes, count)
    return tuple(base + (1 if index < extra else 0) for index in range(count))


def _choose_exercise_type(
    skill: CurriculumSkill,
    *,
    previous_type: str | None,
    intensity: PlanIntensity,
) -> str:
    candidates = list(skill.exercise_types)
    if previous_type in candidates and len(candidates) > 1:
        candidates.remove(previous_type)

    if previous_type in LISTENING_TYPES:
        communication_preference = SPEAKING_TYPES
    elif previous_type in SPEAKING_TYPES:
        communication_preference = LISTENING_TYPES
    else:
        communication_preference = (
            (*SPEAKING_TYPES, *LISTENING_TYPES)
            if intensity is PlanIntensity.HIGH
            else (*LISTENING_TYPES, *SPEAKING_TYPES)
        )

    for exercise_type in communication_preference:
        if exercise_type in candidates:
            return exercise_type
    for exercise_type in TYPE_ORDER[intensity]:
        if exercise_type in candidates:
            return exercise_type
    return candidates[0]


def _practice_order(
    skills: Sequence[CurriculumSkill],
    progress_by_code: Mapping[str, SkillProgress],
    priority_categories: Sequence[str],
) -> list[CurriculumSkill]:
    priority = {category: index for index, category in enumerate(priority_categories)}
    fallback_priority = len(priority)

    def key(skill: CurriculumSkill) -> tuple[int, float, float, int, str]:
        progress = progress_by_code[skill.code]
        return (
            priority.get(skill.category, fallback_priority),
            progress.estimated_mastery,
            progress.confidence,
            skill.curriculum_order,
            skill.code,
        )

    return sorted(
        (skill for skill in skills if skill.code in progress_by_code),
        key=key,
    )


def build_daily_plan(
    *,
    available_minutes: int,
    motivation: int,
    generated_at: datetime,
    progress_by_code: Mapping[str, SkillProgress],
    last_exercise_type: str | None = None,
    priority_categories: Sequence[str] = ("listening", "speaking"),
    curriculum: Sequence[CurriculumSkill] = CURRICULUM,
) -> DailyPlan:
    """Build a plan from explicit state; no model provider is consulted."""

    if not 10 <= available_minutes <= 120:
        raise ValueError("available_minutes must be between 10 and 120")
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("generated_at must include a timezone")
    intensity = intensity_for_motivation(motivation)
    validate_curriculum(curriculum)
    skills_by_code = {skill.code: skill for skill in curriculum}
    for progress in progress_by_code.values():
        _validate_progress(progress)

    mastered_codes = _mastered_codes(progress_by_code, curriculum)
    introduced = {
        code
        for code, progress in progress_by_code.items()
        if code in skills_by_code and progress.evidence_count > 0
    }
    eligible_new = [
        skill
        for skill in curriculum
        if skill.code not in introduced and not unmet_prerequisites(skill, mastered_codes)
    ]

    review_candidates = [
        ReviewCandidate(
            skill_code=skill.code,
            due_at=progress_by_code[skill.code].next_review_at,
            estimated_mastery=progress_by_code[skill.code].estimated_mastery,
            confidence=progress_by_code[skill.code].confidence,
            evidence_count=progress_by_code[skill.code].evidence_count,
            curriculum_order=skill.curriculum_order,
            last_outcome=progress_by_code[skill.code].last_outcome,
        )
        for skill in curriculum
        if skill.code in progress_by_code
    ]
    due_reviews = rank_due_reviews(review_candidates, as_of=generated_at)
    count = _block_count(available_minutes)
    seeds: list[_BlockSeed] = []

    for review in due_reviews[:count]:
        seeds.append(
            _BlockSeed(
                kind=PlanBlockKind.REVIEW,
                skill=skills_by_code[review.skill_code],
                rationale="Repaso vencido priorizado antes de contenido nuevo.",
            )
        )

    new_skill: CurriculumSkill | None = None
    capacity = count - len(seeds)
    bootstrap = not introduced
    may_introduce = (
        capacity > 0 and bool(eligible_new) and (intensity is not PlanIntensity.LOW or bootstrap)
    )
    if may_introduce:
        new_skill = eligible_new[0]
        seeds.append(
            _BlockSeed(
                kind=PlanBlockKind.NEW_SKILL,
                skill=new_skill,
                rationale=(
                    "Inicio mínimo necesario: todavía no hay habilidades activas."
                    if bootstrap
                    else "Todos los prerrequisitos satisfacen sus criterios internos de dominio."
                ),
            )
        )

    active_skills = _practice_order(curriculum, progress_by_code, priority_categories)
    practice_index = 0
    while len(seeds) < count:
        if new_skill is not None and not any(
            seed.kind is PlanBlockKind.PRACTICE and seed.skill.code == new_skill.code
            for seed in seeds
        ):
            practice_skill = new_skill
            rationale = "Consolidación inmediata de la única habilidad nueva del plan."
        elif active_skills:
            practice_skill = active_skills[practice_index % len(active_skills)]
            practice_index += 1
            rationale = "Habilidad activa priorizada por necesidad, modalidad y variedad."
        elif eligible_new:
            # This is reachable only for a bootstrap plan whose new skill already
            # has its reinforcement block; repeating it is safer than inventing a
            # second new skill.
            practice_skill = new_skill or eligible_new[0]
            rationale = "Práctica apoyada sin introducir una segunda habilidad."
        else:
            raise ValueError("cannot build a plan without an eligible or active curriculum skill")
        seeds.append(
            _BlockSeed(
                kind=PlanBlockKind.PRACTICE,
                skill=practice_skill,
                rationale=rationale,
            )
        )

    durations = _duration_distribution(available_minutes, len(seeds))
    blocks: list[DailyPlanBlock] = []
    previous_type = last_exercise_type
    for position, (seed, duration) in enumerate(zip(seeds, durations, strict=True), start=1):
        exercise_type = _choose_exercise_type(
            seed.skill,
            previous_type=previous_type,
            intensity=intensity,
        )
        action = {
            PlanBlockKind.REVIEW: "Recuperar y consolidar",
            PlanBlockKind.NEW_SKILL: "Introducir",
            PlanBlockKind.PRACTICE: "Practicar",
        }[seed.kind]
        blocks.append(
            DailyPlanBlock(
                position=position,
                kind=seed.kind,
                skill_code=seed.skill.code,
                exercise_type=exercise_type,
                duration_minutes=duration,
                objective=f"{action} {seed.skill.name}.",
                internal_reason=seed.rationale,
            )
        )
        previous_type = exercise_type

    review_skill_codes = tuple(
        dict.fromkeys(block.skill_code for block in blocks if block.kind is PlanBlockKind.REVIEW)
    )
    primary_skill = blocks[0].skill_code
    primary_name = skills_by_code[primary_skill].name
    if review_skill_codes:
        objective = f"Consolidar {primary_name} y atender los repasos pendientes."
    elif new_skill is not None:
        objective = f"Introducir {primary_name} y practicarlo con apoyo."
    else:
        objective = f"Consolidar {primary_name} con práctica variada."

    reason_codes: list[str] = ["deterministic_plan", "prerequisites_enforced"]
    if review_skill_codes:
        reason_codes.append("reviews_due_first")
    if intensity is PlanIntensity.LOW:
        reason_codes.append("low_motivation_reduced_intensity")
    if new_skill is not None:
        reason_codes.append("single_new_skill_limit")
    if available_minutes == 10:
        reason_codes.append("ten_minute_session_supported")
    if any(block.exercise_type in COMMUNICATION_TYPES for block in blocks):
        reason_codes.append("listening_speaking_preferred")
    if len({block.exercise_type for block in blocks}) > 1:
        reason_codes.append("exercise_types_alternated")

    internal_parts = []
    if review_skill_codes:
        internal_parts.append(f"Se colocaron primero {len(review_skill_codes)} repasos vencidos.")
    if intensity is PlanIntensity.LOW and bootstrap and new_skill is not None:
        internal_parts.append(
            "La motivación baja redujo la intensidad; se permitió solo la habilidad mínima de arranque."
        )
    elif intensity is PlanIntensity.LOW:
        internal_parts.append("La motivación baja redujo la intensidad y bloqueó contenido nuevo.")
    elif new_skill is not None:
        internal_parts.append("Se limitó el plan a una sola habilidad nueva elegible.")
    else:
        internal_parts.append("No se añadió contenido nuevo sin capacidad o prerrequisitos.")
    internal_parts.append("Los tipos de ejercicio se eligieron con una rotación determinista.")

    return DailyPlan(
        engine_version=ENGINE_VERSION,
        curriculum_version=CURRICULUM_VERSION,
        generated_at=generated_at,
        requested_minutes=available_minutes,
        duration_minutes=sum(block.duration_minutes for block in blocks),
        motivation=motivation,
        objective=objective,
        intensity=intensity,
        primary_skill_code=primary_skill,
        review_skill_codes=review_skill_codes,
        new_skill_code=new_skill.code if new_skill is not None else None,
        blocks=tuple(blocks),
        internal_reason=" ".join(internal_parts),
        reason_codes=tuple(reason_codes),
    )
