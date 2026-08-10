from enum import StrEnum

TEACHER_MODEL = "google/gemma-4-12b-qat"
DEEP_TEACHER_MODEL = "google/gemma-4-26b-a4b-qat"
EMBEDDING_MODEL = "text-embedding-embeddinggemma-300m"


class LLCModelRole(StrEnum):
    TEACHER = "teacher"
    DEEP_TEACHER = "deep_teacher"
    EMBEDDING = "embedding"


ROLE_MODELS: dict[LLCModelRole, str] = {
    LLCModelRole.TEACHER: TEACHER_MODEL,
    LLCModelRole.DEEP_TEACHER: DEEP_TEACHER_MODEL,
    LLCModelRole.EMBEDDING: EMBEDDING_MODEL,
}


def model_for_role(role: LLCModelRole) -> str:
    return ROLE_MODELS[role]
