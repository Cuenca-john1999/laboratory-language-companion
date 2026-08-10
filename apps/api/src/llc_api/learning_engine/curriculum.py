"""The versioned A0 -> A1 curriculum.

This module is deliberately data-only and deterministic.  The curriculum order,
prerequisites and mastery criteria are code-owned pedagogical decisions; a model
provider may create content for a selected skill but cannot change these rules.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Literal

CURRICULUM_VERSION = "a0-a1.v1"

CefrReference = Literal["A0", "A1"]


class InvalidCurriculumError(ValueError):
    """Raised when a curriculum definition is ambiguous or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class MasteryCriteria:
    min_mastery: float = 0.80
    min_confidence: float = 0.60
    min_evidence: int = 6
    unassisted_streak: int = 2

    def __post_init__(self) -> None:
        if not 0 <= self.min_mastery <= 1:
            raise InvalidCurriculumError("min_mastery must be between 0 and 1")
        if not 0 <= self.min_confidence <= 1:
            raise InvalidCurriculumError("min_confidence must be between 0 and 1")
        if self.min_evidence < 1:
            raise InvalidCurriculumError("min_evidence must be positive")
        if self.unassisted_streak < 1:
            raise InvalidCurriculumError("unassisted_streak must be positive")


DEFAULT_MASTERY_CRITERIA = MasteryCriteria()


@dataclass(frozen=True, slots=True)
class CurriculumSkill:
    code: str
    name: str
    category: str
    description: str
    cefr_reference: CefrReference
    prerequisite_codes: tuple[str, ...]
    mastery_criteria: MasteryCriteria
    difficulty: int
    exercise_types: tuple[str, ...]
    curriculum_version: str = CURRICULUM_VERSION
    curriculum_order: int = 0

    def __post_init__(self) -> None:
        if not self.code or not self.name or not self.category or not self.description:
            raise InvalidCurriculumError("skill identity fields cannot be empty")
        if self.curriculum_version != CURRICULUM_VERSION:
            raise InvalidCurriculumError("every skill must use the active curriculum version")
        if self.curriculum_order < 1:
            raise InvalidCurriculumError("curriculum_order must be positive")
        if not 1 <= self.difficulty <= 5:
            raise InvalidCurriculumError("difficulty must be between 1 and 5")
        if not self.exercise_types:
            raise InvalidCurriculumError("every skill needs at least one exercise type")
        if len(set(self.exercise_types)) != len(self.exercise_types):
            raise InvalidCurriculumError(f"{self.code} contains duplicate exercise types")
        if len(set(self.prerequisite_codes)) != len(self.prerequisite_codes):
            raise InvalidCurriculumError(f"{self.code} contains duplicate prerequisites")


def _skill(
    order: int,
    code: str,
    name: str,
    category: str,
    description: str,
    cefr_reference: CefrReference,
    difficulty: int,
    exercise_types: tuple[str, ...],
    prerequisite_codes: tuple[str, ...] = (),
) -> CurriculumSkill:
    return CurriculumSkill(
        code=code,
        name=name,
        category=category,
        description=description,
        cefr_reference=cefr_reference,
        prerequisite_codes=prerequisite_codes,
        mastery_criteria=DEFAULT_MASTERY_CRITERIA,
        difficulty=difficulty,
        exercise_types=exercise_types,
        curriculum_order=order,
    )


CURRICULUM: tuple[CurriculumSkill, ...] = (
    _skill(
        1,
        "grammar.personal_pronouns",
        "Pronombres personales",
        "grammar",
        "Reconocer y usar los pronombres personales de sujeto en frases breves.",
        "A0",
        1,
        ("recognition", "gap_fill", "sentence_building"),
    ),
    _skill(
        2,
        "vocabulary.everyday_core",
        "Vocabulario cotidiano esencial",
        "vocabulary",
        "Comprender y producir palabras frecuentes sobre identidad, hogar y rutina.",
        "A0",
        1,
        ("recognition", "listening_choice", "sentence_building", "guided_dialogue"),
    ),
    _skill(
        3,
        "grammar.sein_present",
        "Sein en presente",
        "grammar",
        "Conjugar sein en presente y usarlo para identidad, estado y procedencia.",
        "A0",
        1,
        ("gap_fill", "sentence_building", "short_answer", "guided_dialogue"),
        ("grammar.personal_pronouns",),
    ),
    _skill(
        4,
        "grammar.haben_present",
        "Haben en presente",
        "grammar",
        "Conjugar haben en presente y expresar posesión o disponibilidad básica.",
        "A0",
        1,
        ("gap_fill", "sentence_building", "short_answer", "guided_dialogue"),
        ("grammar.personal_pronouns",),
    ),
    _skill(
        5,
        "grammar.present_regular",
        "Presente de verbos regulares",
        "grammar",
        "Formar el presente de verbos regulares en enunciados cotidianos.",
        "A0",
        2,
        ("gap_fill", "sentence_building", "short_answer", "guided_dialogue"),
        ("grammar.personal_pronouns", "vocabulary.everyday_core"),
    ),
    _skill(
        6,
        "listening.basic",
        "Comprensión oral básica",
        "listening",
        "Identificar información explícita en mensajes orales lentos y breves.",
        "A1",
        2,
        ("listening_choice", "listen_and_type"),
        ("vocabulary.everyday_core", "grammar.sein_present"),
    ),
    _skill(
        7,
        "grammar.articles_basic",
        "Artículos básicos",
        "grammar",
        "Distinguir y usar artículos definidos e indefinidos en vocabulario conocido.",
        "A0",
        2,
        ("recognition", "gap_fill", "sentence_building"),
        ("vocabulary.everyday_core",),
    ),
    _skill(
        8,
        "grammar.nominative_basic",
        "Nominativo básico",
        "grammar",
        "Reconocer y construir sujetos nominales sencillos en nominativo.",
        "A1",
        2,
        ("recognition", "gap_fill", "sentence_building"),
        ("grammar.personal_pronouns", "grammar.articles_basic"),
    ),
    _skill(
        9,
        "grammar.questions_basic",
        "Preguntas básicas",
        "grammar",
        "Formar preguntas sí/no y preguntas con palabras interrogativas frecuentes.",
        "A1",
        2,
        ("sentence_building", "short_answer", "guided_dialogue", "listening_choice"),
        ("grammar.sein_present", "grammar.present_regular"),
    ),
    _skill(
        10,
        "grammar.negation_basic",
        "Negación básica",
        "grammar",
        "Negar enunciados sencillos con nicht y kein en contextos controlados.",
        "A1",
        2,
        ("gap_fill", "sentence_building", "short_answer", "guided_dialogue"),
        ("grammar.present_regular", "grammar.articles_basic"),
    ),
    _skill(
        11,
        "speaking.basic",
        "Producción oral básica",
        "speaking",
        "Responder y mantener intercambios breves sobre información personal y cotidiana.",
        "A1",
        3,
        ("guided_dialogue", "spoken_response"),
        (
            "vocabulary.everyday_core",
            "grammar.sein_present",
            "grammar.questions_basic",
        ),
    ),
    _skill(
        12,
        "grammar.accusative_basic",
        "Acusativo básico",
        "grammar",
        "Reconocer y usar objetos directos frecuentes con artículos básicos.",
        "A1",
        3,
        ("recognition", "gap_fill", "sentence_building", "guided_dialogue"),
        (
            "grammar.nominative_basic",
            "grammar.present_regular",
            "grammar.articles_basic",
        ),
    ),
    _skill(
        13,
        "grammar.modal_verbs_basic",
        "Verbos modales básicos",
        "grammar",
        "Usar können, müssen y möchten en estructuras principales sencillas.",
        "A1",
        3,
        ("gap_fill", "sentence_building", "guided_dialogue", "spoken_response"),
        ("grammar.present_regular", "grammar.questions_basic"),
    ),
    _skill(
        14,
        "vocabulary.laboratory_intro",
        "Vocabulario inicial de laboratorio",
        "professional_language",
        "Comprender y usar términos iniciales de materiales, acciones y seguridad de laboratorio.",
        "A1",
        3,
        ("recognition", "listening_choice", "sentence_building", "guided_dialogue"),
        ("vocabulary.everyday_core", "grammar.accusative_basic"),
    ),
)


def validate_curriculum(skills: Sequence[CurriculumSkill]) -> None:
    if not skills:
        raise InvalidCurriculumError("curriculum cannot be empty")
    codes = [skill.code for skill in skills]
    orders = [skill.curriculum_order for skill in skills]
    if len(set(codes)) != len(codes):
        raise InvalidCurriculumError("skill codes must be unique")
    if len(set(orders)) != len(orders):
        raise InvalidCurriculumError("curriculum orders must be unique")
    if orders != sorted(orders):
        raise InvalidCurriculumError("skills must be stored in curriculum order")

    order_by_code = {skill.code: skill.curriculum_order for skill in skills}
    for skill in skills:
        for prerequisite in skill.prerequisite_codes:
            if prerequisite not in order_by_code:
                raise InvalidCurriculumError(
                    f"{skill.code} references unknown prerequisite {prerequisite}"
                )
            if order_by_code[prerequisite] >= skill.curriculum_order:
                raise InvalidCurriculumError(
                    f"{skill.code} prerequisite {prerequisite} must occur earlier"
                )


validate_curriculum(CURRICULUM)
SKILLS_BY_CODE = {skill.code: skill for skill in CURRICULUM}


def get_curriculum_skill(code: str) -> CurriculumSkill:
    try:
        return SKILLS_BY_CODE[code]
    except KeyError as exc:
        raise KeyError(f"unknown curriculum skill: {code}") from exc


def unmet_prerequisites(
    skill: CurriculumSkill, mastered_skill_codes: Collection[str]
) -> tuple[str, ...]:
    """Return prerequisites not currently meeting their own mastery criteria."""

    return tuple(
        prerequisite
        for prerequisite in skill.prerequisite_codes
        if prerequisite not in mastered_skill_codes
    )
