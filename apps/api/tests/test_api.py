from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select

from deutschos_api.core.model_roles import DEEP_TEACHER_MODEL, TEACHER_MODEL
from deutschos_api.main import app
from deutschos_api.models import LearningSession
from deutschos_api.providers.base import ProviderUnavailableError
from deutschos_api.providers.dependencies import get_model_provider
from deutschos_api.providers.lm_studio import LMStudioProvider
from deutschos_api.schemas.api import ModelInfo

pytestmark = pytest.mark.anyio


class OfflineProvider:
    async def list_models(self):
        raise ProviderUnavailableError("LM Studio no está disponible")

    async def health_check(self):
        return False

    async def chat(self, model, messages):
        raise ProviderUnavailableError("LM Studio no está disponible")


class WorkingProvider:
    async def list_models(self):
        return [ModelInfo(name=TEACHER_MODEL), ModelInfo(name=DEEP_TEACHER_MODEL)]

    async def health_check(self):
        return True

    async def chat(self, model, messages):
        return "Respuesta privada del profesor"

    async def stream_chat(self, model, messages) -> AsyncIterator[str]:
        yield "Guten "
        yield "Tag"


def lm_studio_provider_with_tags(payload: dict) -> LMStudioProvider:
    return LMStudioProvider(
        "http://lm_studio.test",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload)),
    )


async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_profile_persists_and_emits_utc(client):
    profile = (await client.get("/api/profile")).json()
    profile["preferred_name"] = "Johannes"
    editable = {
        key: value
        for key, value in profile.items()
        if key not in {"id", "created_at", "updated_at"}
    }
    response = await client.put("/api/profile", json=editable)
    assert response.status_code == 200
    reloaded = (await client.get("/api/profile")).json()
    assert reloaded["preferred_name"] == "Johannes"
    assert reloaded["created_at"].endswith("Z")


async def test_profile_rejects_unknown_fields(client):
    profile = (await client.get("/api/profile")).json()
    response = await client.put("/api/profile", json=profile)
    assert response.status_code == 422
    assert any(error["type"] == "extra_forbidden" for error in response.json()["detail"])


async def test_lm_studio_unavailable_is_visible_and_does_not_write_session(
    client, db_session_factory
):
    app.dependency_overrides[get_model_provider] = lambda: OfflineProvider()
    models = await client.get("/api/models")
    dashboard = await client.get("/api/dashboard")
    assert models.status_code == 200
    assert models.json()["available"] is False
    assert dashboard.status_code == 200
    assert dashboard.json()["lm_studio_available"] is False
    chat = await client.post(
        "/api/chat", json={"message": "Hallo", "role": "teacher", "history": []}
    )
    assert chat.status_code == 503
    assert "LM Studio" in chat.json()["detail"]
    with db_session_factory() as db:
        assert db.scalar(select(LearningSession)) is None


async def test_model_list_and_dashboard_accept_realistic_lm_studio_metadata(client):
    provider = lm_studio_provider_with_tags(
        {
            "data": [
                {
                    "id": "google/gemma-4-12b-qat",
                    "object": "model",
                }
            ]
        }
    )
    app.dependency_overrides[get_model_provider] = lambda: provider

    models = await client.get("/api/models")
    dashboard = await client.get("/api/dashboard")

    assert models.status_code == 200
    assert models.json() == {
        "provider": "lm_studio",
        "available": True,
        "models": [
            {
                "name": "google/gemma-4-12b-qat",
                "size": None,
                "modified_at": None,
            }
        ],
        "error": None,
    }
    assert dashboard.status_code == 200
    assert dashboard.json()["lm_studio_available"] is True


async def test_teacher_roles_are_closed_and_report_role_specific_availability(client):
    provider = lm_studio_provider_with_tags(
        {
            "data": [
                {"id": TEACHER_MODEL, "object": "model"},
                {"id": "text-embedding-embeddinggemma-300m", "object": "model"},
                {"id": "nomic-embed-text-v1.5", "object": "model"},
            ]
        }
    )
    app.dependency_overrides[get_model_provider] = lambda: provider

    response = await client.get("/api/teacher/roles")

    assert response.status_code == 200
    assert response.json() == {
        "provider": "lm_studio",
        "available": True,
        "roles": [
            {"role": "teacher", "available": True},
            {"role": "deep_teacher", "available": False},
        ],
        "error": None,
    }


async def test_chat_rejects_physical_or_unknown_models_and_resolves_roles(client):
    app.dependency_overrides[get_model_provider] = lambda: WorkingProvider()

    for rejected in (
        {"message": "Hallo", "model": TEACHER_MODEL, "history": []},
        {"message": "Hallo", "role": "embedding", "history": []},
        {"message": "Hallo", "role": "nomic-embed-text-v1.5", "history": []},
    ):
        response = await client.post("/api/chat", json=rejected)
        assert response.status_code == 422

    regular = await client.post(
        "/api/chat", json={"message": "Hallo", "role": "teacher", "history": []}
    )
    deep = await client.post(
        "/api/chat", json={"message": "Hallo", "role": "deep_teacher", "history": []}
    )
    assert regular.json()["model"] == TEACHER_MODEL
    assert deep.json()["model"] == DEEP_TEACHER_MODEL


async def test_empty_lm_studio_list_is_available_without_invented_models(client):
    provider = lm_studio_provider_with_tags({"data": []})
    app.dependency_overrides[get_model_provider] = lambda: provider

    models = await client.get("/api/models")
    dashboard = await client.get("/api/dashboard")

    assert models.status_code == 200
    assert models.json()["available"] is True
    assert models.json()["models"] == []
    assert dashboard.status_code == 200
    assert dashboard.json()["lm_studio_available"] is True


async def test_malformed_lm_studio_item_does_not_crash_model_endpoints(client):
    provider = lm_studio_provider_with_tags({"data": [{"object": "model"}]})
    app.dependency_overrides[get_model_provider] = lambda: provider

    models = await client.get("/api/models")
    dashboard = await client.get("/api/dashboard")

    assert models.status_code == 200
    assert models.json()["available"] is False
    assert models.json()["models"] == []
    assert dashboard.status_code == 200
    assert dashboard.json()["lm_studio_available"] is False


async def test_chat_keeps_history_ephemeral_and_stores_only_safe_metadata(
    client, db_session_factory
):
    app.dependency_overrides[get_model_provider] = lambda: WorkingProvider()
    response = await client.post(
        "/api/chat",
        json={
            "message": "Privater Inhalt",
            "role": "teacher",
            "history": [{"role": "assistant", "content": "Vorherige private Antwort"}],
        },
    )
    assert response.status_code == 200
    session_id = response.json()["session_id"]
    with db_session_factory() as db:
        session = db.get(LearningSession, session_id)
        assert session is not None
        assert "Privater Inhalt" not in session.summary
        assert "Respuesta privada" not in session.summary
        assert session.started_at.tzinfo is not None


async def test_streaming_chat_reuses_one_session(client, db_session_factory):
    app.dependency_overrides[get_model_provider] = lambda: WorkingProvider()
    first = await client.post(
        "/api/chat/stream",
        json={"message": "Hallo", "role": "teacher", "history": []},
    )
    assert first.status_code == 200
    events = [line for line in first.text.splitlines() if line]
    assert '"type": "token"' in events[0]
    assert '"type": "done"' in events[-1]
    session_id = __import__("json").loads(events[-1])["session_id"]

    second = await client.post(
        "/api/chat/stream",
        json={
            "message": "Noch einmal",
            "role": "teacher",
            "history": [{"role": "assistant", "content": "Guten Tag"}],
            "session_id": session_id,
        },
    )
    assert second.status_code == 200
    with db_session_factory() as db:
        sessions = db.scalars(select(LearningSession)).all()
        assert len(sessions) == 1
        assert sessions[0].id == session_id
