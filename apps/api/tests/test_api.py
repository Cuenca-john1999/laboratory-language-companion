from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select

from deutschos_api.main import app
from deutschos_api.models import LearningSession
from deutschos_api.providers.base import ProviderUnavailableError
from deutschos_api.providers.dependencies import get_model_provider
from deutschos_api.schemas.api import ModelInfo

pytestmark = pytest.mark.anyio


class OfflineProvider:
    async def list_models(self):
        raise ProviderUnavailableError("Ollama no está disponible")

    async def health_check(self):
        return False

    async def chat(self, model, messages):
        raise ProviderUnavailableError("Ollama no está disponible")


class WorkingProvider:
    async def list_models(self):
        return [ModelInfo(name="local-test")]

    async def health_check(self):
        return True

    async def chat(self, model, messages):
        return "Respuesta privada del profesor"

    async def stream_chat(self, model, messages) -> AsyncIterator[str]:
        yield "Guten "
        yield "Tag"


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


async def test_ollama_unavailable_is_visible_and_does_not_write_session(client, db_session_factory):
    app.dependency_overrides[get_model_provider] = lambda: OfflineProvider()
    models = await client.get("/api/models")
    assert models.status_code == 200
    assert models.json()["available"] is False
    chat = await client.post("/api/chat", json={"message": "Hallo", "model": "test", "history": []})
    assert chat.status_code == 503
    assert "Ollama" in chat.json()["detail"]
    with db_session_factory() as db:
        assert db.scalar(select(LearningSession)) is None


async def test_chat_keeps_history_ephemeral_and_stores_only_safe_metadata(
    client, db_session_factory
):
    app.dependency_overrides[get_model_provider] = lambda: WorkingProvider()
    response = await client.post(
        "/api/chat",
        json={
            "message": "Privater Inhalt",
            "model": "local-test",
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
        json={"message": "Hallo", "model": "local-test", "history": []},
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
            "model": "local-test",
            "history": [{"role": "assistant", "content": "Guten Tag"}],
            "session_id": session_id,
        },
    )
    assert second.status_code == 200
    with db_session_factory() as db:
        sessions = db.scalars(select(LearningSession)).all()
        assert len(sessions) == 1
        assert sessions[0].id == session_id
