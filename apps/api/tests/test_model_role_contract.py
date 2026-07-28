from pathlib import Path

import pytest
from pydantic import ValidationError

from deutschos_api.core.config import Settings
from deutschos_api.core.model_roles import (
    DEEP_TEACHER_MODEL,
    EMBEDDING_MODEL,
    TEACHER_MODEL,
    DeutschOSModelRole,
    model_for_role,
)
from deutschos_api.educational_library.dependencies import (
    get_library_model_router,
    get_library_search,
)
from deutschos_api.educational_library.schemas import (
    GroundedGenerationRequest,
    KnowledgeGenerationRequest,
)


def test_role_contract_resolves_only_authorized_models():
    assert model_for_role(DeutschOSModelRole.TEACHER) == TEACHER_MODEL
    assert model_for_role(DeutschOSModelRole.DEEP_TEACHER) == DEEP_TEACHER_MODEL
    assert model_for_role(DeutschOSModelRole.EMBEDDING) == EMBEDDING_MODEL


def test_library_rejects_an_unauthorized_embedding_configuration(tmp_path):
    settings = Settings(
        educational_library_runtime_dir=tmp_path / "runtime",
        educational_materials_dir=tmp_path / "materials",
        educational_library_embedding_model="nomic-embed-text-v1.5",
    )
    with pytest.raises(ValueError, match="no está autorizado"):
        get_library_search(settings=settings)


def test_library_router_ignores_untrusted_model_configuration():
    settings = Settings(
        educational_library_planner_model="arbitrary-generator",
        educational_library_embedding_model="nomic-embed-text-v1.5",
        educational_library_teacher_model="text-embedding-embeddinggemma-300m",
        educational_library_fallback_model="arbitrary-generator",
    )
    router = get_library_model_router(model_provider=object(), settings=settings)

    assert router.policy.teacher == TEACHER_MODEL
    assert router.policy.fallback == DEEP_TEACHER_MODEL
    assert router.policy.embedding == EMBEDDING_MODEL


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (
            KnowledgeGenerationRequest,
            {"query": "Artikel", "model": "arbitrary-generator"},
        ),
        (
            GroundedGenerationRequest,
            {
                "query": "Artikel",
                "objective": "answer",
                "model": EMBEDDING_MODEL,
            },
        ),
    ],
)
def test_library_generation_rejects_client_supplied_physical_models(schema, payload):
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        schema.model_validate(payload)


def test_normal_interfaces_expose_human_roles_without_physical_model_options():
    root = Path(__file__).resolve().parents[3]
    chat = (root / "apps/web/components/Chat.tsx").read_text()
    library = (root / "apps/web/components/LibraryWorkspace.tsx").read_text()

    assert 'label: "Profesor"' in chat
    assert 'label: "Profesor profundo"' in chat
    assert 'useState<TeacherRole>("teacher")' in chat
    assert "getModels" not in chat
    assert "EmbeddingGemma" not in chat
    assert "nomic" not in chat.casefold()
    assert "Motor semántico: EmbeddingGemma 300M" in library
    assert "setEmbeddingModel" not in library
    assert 'aria-label="Modelo de embeddings"' not in library
