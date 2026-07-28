from __future__ import annotations

from hashlib import sha256

from deutschos_api.models import StudyMissionType

MISSION_LABELS = {
    StudyMissionType.STANDARD: "Estudio directo",
    StudyMissionType.LABORATORY: "Misión de laboratorio",
    StudyMissionType.FROZEN_CITY: "Supervivencia en la ciudad helada",
    StudyMissionType.UNDERWATER_EXPLORATION: "Expedición submarina",
    StudyMissionType.SPACE_MISSION: "Operación espacial",
    StudyMissionType.MIXED: "Sorpresa temática",
}

_MISSION_EXAMPLES: dict[StudyMissionType, tuple[str, str, str]] = {
    StudyMissionType.LABORATORY: (
        "Procesa una muestra y registra el resultado con precisión.",
        "Die Technikerin untersucht die Probe.",
        "La técnica analiza la muestra.",
    ),
    StudyMissionType.FROZEN_CITY: (
        "Coordina calor y recursos para mantener activa una ciudad aislada.",
        "Die Stadt muss den Generator reparieren.",
        "La ciudad tiene que reparar el generador.",
    ),
    StudyMissionType.UNDERWATER_EXPLORATION: (
        "Explora un hábitat submarino y documenta su ubicación.",
        "Die Forschungsstation liegt unter dem Meer.",
        "La estación de investigación está bajo el mar.",
    ),
    StudyMissionType.SPACE_MISSION: (
        "Coordina un equipo científico durante una operación orbital.",
        "Wir prüfen gemeinsam das Messgerät.",
        "Comprobamos juntos el instrumento de medición.",
    ),
}


def resolve_mission_type(
    requested: StudyMissionType,
    *,
    preferred: StudyMissionType,
    stable_seed: str,
) -> StudyMissionType:
    requested = StudyMissionType(requested)
    preferred = StudyMissionType(preferred)
    chosen = preferred if requested == StudyMissionType.AUTOMATIC else requested
    if chosen == StudyMissionType.AUTOMATIC:
        chosen = StudyMissionType.MIXED
    if chosen == StudyMissionType.MIXED:
        options = tuple(_MISSION_EXAMPLES)
        index = int(sha256(stable_seed.encode()).hexdigest()[:8], 16) % len(options)
        return options[index]
    return chosen


def build_mission(
    mission_type: StudyMissionType,
    *,
    concept: str,
    objective: str,
) -> dict[str, object]:
    mission_type = StudyMissionType(mission_type)
    if mission_type == StudyMissionType.STANDARD:
        return {
            "type": mission_type.value,
            "label": MISSION_LABELS[mission_type],
            "concept": concept,
            "brief": "Estudia la sección directamente y decide al final si necesitas repaso.",
            "example_de": None,
            "example_es": None,
            "objective": objective,
            "verifiable_task": f"Produce una frase alemana que demuestre: {objective}",
            "original_content": True,
        }
    brief, example_de, example_es = _MISSION_EXAMPLES[mission_type]
    return {
        "type": mission_type.value,
        "label": MISSION_LABELS[mission_type],
        "concept": concept,
        "brief": brief,
        "example_de": example_de,
        "example_es": example_es,
        "objective": objective,
        "verifiable_task": (
            f"Escribe una frase alemana original relacionada con «{concept}» "
            f"que cumpla este objetivo: {objective}"
        ),
        "original_content": True,
    }


def build_plan(*, planned_minutes: int | None, objective: str) -> dict[str, object]:
    if planned_minutes is None:
        steps = ["Preparación", "Lectura", "Consulta", "Ejemplo", "Práctica", "Cierre"]
    elif planned_minutes <= 20:
        steps = ["Preparación", "Lectura", "Consulta", "Cierre"]
    elif planned_minutes <= 30:
        steps = ["Preparación", "Lectura", "Consulta", "Ejemplo", "Cierre"]
    else:
        steps = ["Preparación", "Lectura", "Consulta", "Ejemplo", "Práctica", "Cierre"]
    return {
        "objective": objective,
        "duration_is_guidance": True,
        "steps": [
            {"id": f"step-{index}", "label": label, "completed": False}
            for index, label in enumerate(steps, start=1)
        ],
    }
