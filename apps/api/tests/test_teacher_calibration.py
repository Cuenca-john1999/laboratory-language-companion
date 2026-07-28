from deutschos_api.educational_library.routing import ModelRole
from deutschos_api.educational_library.schemas import (
    QueryAmbiguity,
    TeacherAskRequest,
    TeacherIntent,
    TeacherQueryPlan,
)
from deutschos_api.educational_library.teacher import EducationalTeacherService
from deutschos_api.models import StudyMissionType
from deutschos_api.prompts.contracts import TEACHER_CONTRACT_VERSION, teacher_prompt
from deutschos_api.study.missions import build_mission


def _plan(**updates):
    base = TeacherQueryPlan(
        intent=TeacherIntent.GRAMMAR_EXPLANATION,
        language="de",
        target_expression="Akkusativ",
        ambiguity=QueryAmbiguity.LOW,
        search_queries=["Akkusativ"],
    )
    return base.model_copy(update=updates)


def test_contract_is_versioned_and_central_for_chat_and_library():
    assert TEACHER_CONTRACT_VERSION == "gemma-teacher.v1"
    chat = teacher_prompt("teacher_v1.md")
    library = teacher_prompt("library_teacher_answer_v1.md")
    for prompt in (chat, library):
        assert "gemma-teacher.v1" in prompt
        assert "español de España" in prompt
        assert "StudentSkill" in prompt and "SkillEvidence" in prompt


def test_contract_covers_correction_valid_variants_exercises_hints_and_level():
    prompt = teacher_prompt("teacher_v1.md")
    assert "Original" in prompt and "Corrección" in prompt
    assert "variantes correctas" in prompt
    assert "oculta la solución" in prompt
    assert "pista graduada" in prompt
    assert "no atribuyas CEFR" in prompt


def test_library_mode_prioritises_herder_workbook_and_rejects_fake_pages():
    prompt = teacher_prompt("library_teacher_answer_v1.md")
    assert "core_theory" in prompt and "core_workbook" in prompt
    assert "página concreta" in prompt
    assert "rejected, conflict y stale" in prompt
    assert "no permite completar con conocimiento general" in prompt


def test_insufficient_evidence_is_explicit_and_does_not_invent_an_answer():
    answer = EducationalTeacherService._insufficient_answer(_plan())
    assert not answer.evidence_sufficient
    assert "evidencia suficiente" in answer.direct_answer
    assert answer.claims == [] and answer.examples == []
    assert answer.follow_up_question


def test_ordinary_routing_for_basic_accusative_and_short_correction():
    request = TeacherAskRequest(question="Corrige: Ich sehe der Hund.")
    role, reason = EducationalTeacherService._teacher_route(request, _plan(), [])
    assert (role, reason) == (ModelRole.TEACHER, "ordinary_clear_evidence")


def test_deep_routing_requires_a_concrete_complex_reason():
    request = TeacherAskRequest(question="Compara estas estructuras paso a paso.")
    role, reason = EducationalTeacherService._teacher_route(
        request,
        _plan(target_expression="Konjunktiv II"),
        [],
    )
    assert (role, reason) == (ModelRole.DEEP, "complex_grammar")


def test_conflicting_or_rejected_memory_uses_deep_route():
    role, reason = EducationalTeacherService._teacher_route(
        TeacherAskRequest(question="¿Qué regla es correcta?"),
        _plan(),
        [{"status": "rejected"}],
    )
    assert (role, reason) == (ModelRole.DEEP, "conflicting_or_rejected_memory")


def test_length_alone_does_not_trigger_deep_routing():
    request = TeacherAskRequest(question="a" * 800)
    role, _ = EducationalTeacherService._teacher_route(request, _plan(), [])
    assert role == ModelRole.TEACHER


def test_laboratory_vocabulary_and_safety_are_in_the_contract():
    prompt = teacher_prompt("teacher_v1.md")
    assert all(word in prompt for word in ("muestras", "pipetas", "reactivos"))
    assert "No inventes procedimientos médicos" in prompt
    assert "Ich untersuche den Patienten" in prompt
    assert "Schlüssel" in prompt
    assert "solo si realmente pertenece" in prompt


def test_accusative_guidance_is_precise_and_explicitly_introductory():
    prompt = teacher_prompt("teacher_v1.md")
    assert "No afirmes que el acusativo sea «el caso que más se usa»" in prompt
    assert "No presentes «en acusativo solo cambia el masculino»" in prompt
    assert "simplificación inicial" in prompt
    assert "der` → `den" in prompt and "ein` → `einen" in prompt
    assert "otros determinantes se declinan" in prompt
    assert "los pronombres" in prompt and "los adjetivos" in prompt
    assert "artículo o determinante marca la función gramatical" in prompt


def test_simple_correction_is_brief_and_does_not_repeat_the_lesson():
    prompt = teacher_prompt("teacher_v1.md")
    assert all(word in prompt for word in ("Original", "Corrección", "explicación breve"))
    assert "No repitas automáticamente la lección ni el ejercicio anterior" in prompt


def test_mission_is_original_and_ends_in_a_verifiable_language_task():
    mission = build_mission(
        StudyMissionType.SPACE_MISSION,
        concept="Akkusativ",
        objective="usar el artículo definido en acusativo",
    )
    assert mission["original_content"] is True
    assert "Escribe una frase alemana original" in mission["verifiable_task"]
