from enum import StrEnum

TEACHER_MODEL = "google/gemma-4-12b-qat"
DEEP_TEACHER_MODEL = "google/gemma-4-26b-a4b-qat"
EMBEDDING_MODEL = "text-embedding-embeddinggemma-300m"


class DeutschOSModelRole(StrEnum):
    TEACHER = "teacher"
    DEEP_TEACHER = "deep_teacher"
    EMBEDDING = "embedding"


ROLE_MODELS: dict[DeutschOSModelRole, str] = {
    DeutschOSModelRole.TEACHER: TEACHER_MODEL,
    DeutschOSModelRole.DEEP_TEACHER: DEEP_TEACHER_MODEL,
    DeutschOSModelRole.EMBEDDING: EMBEDDING_MODEL,
}


def model_for_role(role: DeutschOSModelRole) -> str:
    return ROLE_MODELS[role]
