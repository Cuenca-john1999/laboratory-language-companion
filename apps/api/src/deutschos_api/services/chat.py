import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from deutschos_api.models import Mistake, StudentProfile
from deutschos_api.prompts.contracts import teacher_prompt
from deutschos_api.schemas.api import ChatTurn


class MissingProfileError(RuntimeError):
    pass


class CorruptLearningDataError(RuntimeError):
    pass


def decode_json_field(raw: str, field_name: str) -> object:
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CorruptLearningDataError(
            f"El campo estructurado {field_name} del perfil no contiene JSON válido."
        ) from exc


def build_teacher_messages(
    db: Session, user_message: str, history: list[ChatTurn] | None = None
) -> list[dict[str, str]]:
    profile = db.scalar(select(StudentProfile).limit(1))
    if profile is None:
        raise MissingProfileError("No hay un perfil de estudiante configurado.")
    recent_mistakes = db.scalars(
        select(Mistake)
        .where(Mistake.status != "ignored")
        .order_by(Mistake.last_seen_at.desc())
        .limit(3)
    ).all()
    system_prompt = teacher_prompt("teacher_v1.md")
    context = {
        "profile": {
            "preferred_name": profile.preferred_name,
            "native_language": profile.native_language,
            "additional_languages": decode_json_field(
                profile.additional_languages, "additional_languages"
            ),
            "current_location": profile.current_location,
            "professional_background": profile.professional_background,
            "learning_goals": decode_json_field(profile.learning_goals, "learning_goals"),
            "interests": decode_json_field(profile.interests, "interests"),
            "learning_preferences": decode_json_field(
                profile.learning_preferences, "learning_preferences"
            ),
        },
        "recent_mistakes": [
            {"original": m.original_text, "corrected": m.corrected_text, "category": m.category}
            for m in recent_mistakes
        ],
    }
    messages = [
        {
            "role": "system",
            "content": f"{system_prompt}\n\nContexto estructurado:\n{json.dumps(context, ensure_ascii=False)}",
        },
    ]
    messages.extend(turn.model_dump() for turn in (history or []))
    messages.append({"role": "user", "content": user_message})
    return messages
