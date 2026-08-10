import asyncio
import json
import logging
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select

from llc_api.api.routes import (
    CONTINUATION_INSTRUCTION,
    EMPTY_RESPONSE_RETRY_INSTRUCTION,
    exact_overlap_size,
    stream_chat,
)
from llc_api.core.model_roles import DEEP_TEACHER_MODEL, TEACHER_MODEL
from llc_api.main import app
from llc_api.models import LearningSession
from llc_api.providers.base import ProviderStreamEvent, ProviderUnavailableError
from llc_api.providers.dependencies import get_model_provider
from llc_api.providers.lm_studio import LMStudioProvider
from llc_api.schemas.api import ChatRequest, ModelInfo

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


class ScriptedStreamingProvider(WorkingProvider):
    def __init__(self, attempts: list[list[ProviderStreamEvent]]) -> None:
        self.attempts = attempts
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    async def stream_chat_events(self, model, messages) -> AsyncIterator[ProviderStreamEvent]:
        self.calls.append((model, messages))
        index = len(self.calls) - 1
        for event in self.attempts[index] if index < len(self.attempts) else []:
            yield event


class BlockingStreamingProvider(WorkingProvider):
    def __init__(self, block_on_attempt: int) -> None:
        self.block_on_attempt = block_on_attempt
        self.calls = 0
        self.blocking = asyncio.Event()

    async def stream_chat_events(self, model, messages) -> AsyncIterator[ProviderStreamEvent]:
        self.calls += 1
        yield ProviderStreamEvent(reasoning_present=True)
        if self.calls == self.block_on_attempt:
            self.blocking.set()
            await asyncio.Future()


class ContinuationBlockingProvider(WorkingProvider):
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, str]]]] = []
        self.blocking = asyncio.Event()

    async def stream_chat_events(self, model, messages) -> AsyncIterator[ProviderStreamEvent]:
        self.calls.append((model, messages))
        if len(self.calls) == 1:
            yield ProviderStreamEvent(content="Respuesta parcial.")
            yield ProviderStreamEvent(finish_reason="length")
            return
        yield ProviderStreamEvent(reasoning_present=True)
        self.blocking.set()
        await asyncio.Future()


class StreamingContinuationProvider(WorkingProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.blocking = asyncio.Event()

    async def stream_chat_events(self, model, messages) -> AsyncIterator[ProviderStreamEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ProviderStreamEvent(content="Respuesta anterior.")
            yield ProviderStreamEvent(finish_reason="length")
            return
        yield ProviderStreamEvent(content="Nuevo contenido visible.")
        self.blocking.set()
        await asyncio.Future()


class FailingStreamingProvider(WorkingProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def stream_chat_events(self, model, messages) -> AsyncIterator[ProviderStreamEvent]:
        self.calls += 1
        yield ProviderStreamEvent(content="Parcial")
        raise ProviderUnavailableError("Conexión interrumpida")


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


@pytest.mark.parametrize("answer", ["Sí.", "Korrekt", "**Gut.**"])
async def test_streaming_short_visible_answers_never_retry(client, answer):
    provider = ScriptedStreamingProvider(
        [
            [
                ProviderStreamEvent(content=answer),
                ProviderStreamEvent(finish_reason="stop"),
            ]
        ]
    )
    app.dependency_overrides[get_model_provider] = lambda: provider

    response = await client.post(
        "/api/chat/stream",
        json={"message": "Kurz", "role": "teacher", "history": []},
    )
    events = [json.loads(line) for line in response.text.splitlines()]

    assert [event["content"] for event in events if event["type"] == "token"] == [answer]
    assert events[-1]["attempt_count"] == 1
    assert events[-1]["finish_reason"] == "stop"
    assert len(provider.calls) == 1


@pytest.mark.parametrize(
    "first_attempt",
    [
        [],
        [ProviderStreamEvent(reasoning_present=True, finish_reason="length")],
        [ProviderStreamEvent(content=" \n\t"), ProviderStreamEvent(finish_reason="stop")],
        [ProviderStreamEvent(content="<think></think><|end|>")],
    ],
)
async def test_empty_stream_retries_once_without_exposing_or_persisting_failed_turn(
    client, db_session_factory, caplog, first_attempt
):
    private_reasoning = "razonamiento-que-no-debe-salir"
    if first_attempt and first_attempt[0].reasoning_present:
        first_attempt.insert(
            0,
            ProviderStreamEvent(reasoning_present=True),
        )
    provider = ScriptedStreamingProvider(
        [
            first_attempt,
            [
                ProviderStreamEvent(reasoning_present=True),
                ProviderStreamEvent(content="**Respuesta**"),
                ProviderStreamEvent(
                    finish_reason="stop",
                    usage={"completion_tokens": 17, "total_tokens": 29},
                ),
            ],
        ]
    )
    app.dependency_overrides[get_model_provider] = lambda: provider
    caplog.set_level(logging.INFO, logger="llc_api.api.routes")

    response = await client.post(
        "/api/chat/stream",
        json={
            "message": "Original",
            "role": "teacher",
            "history": [{"role": "assistant", "content": "Contexto válido"}],
        },
    )
    events = [json.loads(line) for line in response.text.splitlines()]

    assert [event["content"] for event in events if event["type"] == "token"] == ["**Respuesta**"]
    assert events[-1]["attempt_count"] == 2
    assert events[-1]["recovery"] == "empty_visible_content"
    assert events[-1]["finish_reason"] == "stop"
    assert len(provider.calls) == 2
    assert provider.calls[0][0] == provider.calls[1][0] == TEACHER_MODEL
    assert provider.calls[0][1][-1] == {"role": "user", "content": "Original"}
    assert provider.calls[1][1][:-1] == provider.calls[0][1]
    assert provider.calls[1][1][-1] == {
        "role": "system",
        "content": EMPTY_RESPONSE_RETRY_INSTRUCTION,
    }
    assert private_reasoning not in response.text
    assert private_reasoning not in caplog.text
    assert "reasoning_content" not in response.text
    with db_session_factory() as db:
        assert len(db.scalars(select(LearningSession)).all()) == 1


@pytest.mark.parametrize(
    ("role", "expected_model"),
    [("teacher", TEACHER_MODEL), ("deep_teacher", DEEP_TEACHER_MODEL)],
)
async def test_empty_recovery_preserves_resolved_role_without_fallback(
    client, role, expected_model
):
    provider = ScriptedStreamingProvider(
        [[], [ProviderStreamEvent(content="Erholt."), ProviderStreamEvent(finish_reason="stop")]]
    )
    app.dependency_overrides[get_model_provider] = lambda: provider

    response = await client.post(
        "/api/chat/stream",
        json={"message": "Hallo", "role": role, "history": []},
    )

    assert response.status_code == 200
    assert [model for model, _messages in provider.calls] == [
        expected_model,
        expected_model,
    ]


async def test_two_empty_attempts_return_retryable_error_without_session(
    client, db_session_factory
):
    provider = ScriptedStreamingProvider(
        [
            [ProviderStreamEvent(reasoning_present=True, finish_reason="length")],
            [ProviderStreamEvent(content=" \n"), ProviderStreamEvent(finish_reason="stop")],
            [ProviderStreamEvent(content="No debe ejecutarse")],
        ]
    )
    app.dependency_overrides[get_model_provider] = lambda: provider

    response = await client.post(
        "/api/chat/stream",
        json={"message": "Hallo", "role": "teacher", "history": []},
    )
    events = [json.loads(line) for line in response.text.splitlines()]

    assert events == [
        {
            "type": "error",
            "detail": (
                "El profesor no pudo generar una respuesta visible. Puedes volver a intentarlo."
            ),
            "retryable": True,
        }
    ]
    assert len(provider.calls) == 2
    with db_session_factory() as db:
        assert db.scalar(select(LearningSession)) is None


@pytest.mark.parametrize(
    ("previous", "continuation", "expected"),
    [
        ("cambia de der a", "der a den. En plural…", 5),
        ("Grüße aus Köln: groß", "großartig mit ß.", 4),
        ("**Regla:** texto", "Una regla parecida, pero nueva.", 0),
        ("```python\nprint('a')", "\nprint('b')\n```", 0),
    ],
)
def test_exact_overlap_is_conservative_unicode_safe_and_not_semantic(
    previous, continuation, expected
):
    assert exact_overlap_size(previous, continuation) == expected


@pytest.mark.parametrize(
    ("role", "expected_model"),
    [("teacher", TEACHER_MODEL), ("deep_teacher", DEEP_TEACHER_MODEL)],
)
async def test_visible_length_continues_once_with_same_role_and_one_session(
    client, db_session_factory, caplog, role, expected_model
):
    private_reasoning = "razonamiento privado nunca visible"
    provider = ScriptedStreamingProvider(
        [
            [
                ProviderStreamEvent(content="El acusativo se usa cuando"),
                ProviderStreamEvent(
                    reasoning_present=True,
                    finish_reason="length",
                    usage={"completion_tokens": 2048},
                ),
            ],
            [
                ProviderStreamEvent(reasoning_present=True),
                ProviderStreamEvent(content="cuando el verbo tiene un objeto directo."),
                ProviderStreamEvent(finish_reason="stop"),
            ],
        ]
    )
    app.dependency_overrides[get_model_provider] = lambda: provider
    caplog.set_level(logging.INFO, logger="llc_api.api.routes")

    response = await client.post(
        "/api/chat/stream",
        json={
            "message": "Explica el acusativo",
            "role": role,
            "history": [{"role": "assistant", "content": "Contexto anterior"}],
        },
    )
    events = [json.loads(line) for line in response.text.splitlines()]
    visible = "".join(event["content"] for event in events if event["type"] == "token")
    done = events[-1]

    assert visible == "El acusativo se usa cuando el verbo tiene un objeto directo."
    assert [event for event in events if event["type"] == "continuation"] == [
        {"type": "continuation", "active": True},
        {"type": "continuation", "active": False},
    ]
    assert [model for model, _messages in provider.calls] == [
        expected_model,
        expected_model,
    ]
    assert provider.calls[1][1][:-2] == provider.calls[0][1]
    assert provider.calls[1][1][-2] == {
        "role": "assistant",
        "content": "El acusativo se usa cuando",
    }
    assert provider.calls[1][1][-1] == {
        "role": "system",
        "content": CONTINUATION_INSTRUCTION,
    }
    assert done["attempt_count"] == 2
    assert done["segment_count"] == 2
    assert done["automatic_continuation_count"] == 1
    assert done["manual_continuation_count"] == 0
    assert done["visible_character_count"] == len(visible)
    assert done["finish_reason"] == "stop"
    assert done["continuation_available"] is False
    assert private_reasoning not in response.text
    assert private_reasoning not in caplog.text
    with db_session_factory() as db:
        sessions = db.scalars(select(LearningSession)).all()
        assert len(sessions) == 1
        assert sessions[0].model_used == expected_model

    segment_logs = [
        record.generation
        for record in caplog.records
        if getattr(record, "generation", {}).get("event") == "generation_segment_started"
    ]
    assert len({item["request_id"] for item in segment_logs}) == 2
    assert len({item["logical_generation_id"] for item in segment_logs}) == 1
    overlap_logs = [
        record.generation
        for record in caplog.records
        if getattr(record, "generation", {}).get("event") == "overlap_removed"
    ]
    assert overlap_logs[0]["overlap_characters"] == len("cuando")
    completion_log = next(
        record.generation
        for record in caplog.records
        if getattr(record, "generation", {}).get("event") == "generation_completed"
    )
    assert completion_log["reasoning_present"] is True
    assert completion_log["usage"] == {"completion_tokens": 2048}


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("1. Eins\n2. Zwei", "\n3. Drei", "1. Eins\n2. Zwei\n3. Drei"),
        (
            "| Caso | Forma |\n|---|---|\n| Nom.",
            " | der |\n| Akk. | den |",
            "| Caso | Forma |\n|---|---|\n| Nom. | der |\n| Akk. | den |",
        ),
        ("```python\nprint(", "'Hallo')\n```", "```python\nprint('Hallo')\n```"),
        ("La fórmula es $x", "^2 + y^2$.", "La fórmula es $x^2 + y^2$."),
    ],
)
async def test_continuation_preserves_split_markdown_without_artificial_closures(
    client, first, second, expected
):
    provider = ScriptedStreamingProvider(
        [
            [
                ProviderStreamEvent(content=first),
                ProviderStreamEvent(finish_reason="length"),
            ],
            [
                ProviderStreamEvent(content=second),
                ProviderStreamEvent(finish_reason="stop"),
            ],
        ]
    )
    app.dependency_overrides[get_model_provider] = lambda: provider

    response = await client.post(
        "/api/chat/stream",
        json={"message": "Markdown", "role": "teacher", "history": []},
    )
    events = [json.loads(line) for line in response.text.splitlines()]

    assert "".join(event["content"] for event in events if event["type"] == "token") == expected
    assert len(provider.calls) == 2


async def test_second_visible_length_stops_automatic_chain_and_offers_continue(client):
    provider = ScriptedStreamingProvider(
        [
            [
                ProviderStreamEvent(content="Primera parte. "),
                ProviderStreamEvent(finish_reason="length"),
            ],
            [
                ProviderStreamEvent(content="Segunda parte."),
                ProviderStreamEvent(finish_reason="length"),
            ],
            [ProviderStreamEvent(content="No debe ejecutarse")],
        ]
    )
    app.dependency_overrides[get_model_provider] = lambda: provider

    response = await client.post(
        "/api/chat/stream",
        json={"message": "Largo", "role": "teacher", "history": []},
    )
    events = [json.loads(line) for line in response.text.splitlines()]

    assert "".join(event["content"] for event in events if event["type"] == "token") == (
        "Primera parte. Segunda parte."
    )
    assert len(provider.calls) == 2
    assert events[-1]["finish_reason"] == "length"
    assert events[-1]["continuation_available"] is True
    assert events[-1]["automatic_continuation_count"] == 1


async def test_empty_continuation_retries_same_continuation_with_hard_call_limit(client):
    provider = ScriptedStreamingProvider(
        [
            [
                ProviderStreamEvent(content="Parte visible."),
                ProviderStreamEvent(finish_reason="length"),
            ],
            [ProviderStreamEvent(reasoning_present=True, finish_reason="stop")],
            [
                ProviderStreamEvent(content=" Final."),
                ProviderStreamEvent(finish_reason="stop"),
            ],
            [ProviderStreamEvent(content="Cuarta llamada prohibida")],
        ]
    )
    app.dependency_overrides[get_model_provider] = lambda: provider

    response = await client.post(
        "/api/chat/stream",
        json={"message": "Continúa", "role": "teacher", "history": []},
    )
    events = [json.loads(line) for line in response.text.splitlines()]

    assert "".join(event["content"] for event in events if event["type"] == "token") == (
        "Parte visible. Final."
    )
    assert len(provider.calls) == 3
    assert provider.calls[1][1][-2]["content"] == "Parte visible."
    assert provider.calls[2][1][-3]["content"] == "Parte visible."
    assert provider.calls[2][1][-1] == {
        "role": "system",
        "content": EMPTY_RESPONSE_RETRY_INSTRUCTION,
    }
    assert events[-1]["recovery"] == "empty_visible_content"
    assert events[-1]["segment_count"] == 2


async def test_initial_empty_then_truncated_retry_uses_exactly_three_calls(client):
    provider = ScriptedStreamingProvider(
        [
            [ProviderStreamEvent(reasoning_present=True, finish_reason="length")],
            [
                ProviderStreamEvent(content="Recuperada y cortada. "),
                ProviderStreamEvent(finish_reason="length"),
            ],
            [
                ProviderStreamEvent(content="Completada."),
                ProviderStreamEvent(finish_reason="stop"),
            ],
            [ProviderStreamEvent(content="No")],
        ]
    )
    app.dependency_overrides[get_model_provider] = lambda: provider

    response = await client.post(
        "/api/chat/stream",
        json={"message": "Secuencia máxima", "role": "teacher", "history": []},
    )
    events = [json.loads(line) for line in response.text.splitlines()]

    assert len(provider.calls) == 3
    assert events[-1]["attempt_count"] == 3
    assert events[-1]["automatic_continuation_count"] == 1
    assert events[-1]["recovery"] == "empty_visible_content"


async def test_manual_continuation_never_chains_automatically(client):
    provider = ScriptedStreamingProvider(
        [
            [
                ProviderStreamEvent(content=" previa. Nueva parte."),
                ProviderStreamEvent(finish_reason="length"),
            ],
            [ProviderStreamEvent(content="No debe ejecutarse")],
        ]
    )
    app.dependency_overrides[get_model_provider] = lambda: provider

    response = await client.post(
        "/api/chat/stream",
        json={
            "message": "Respuesta larga",
            "role": "teacher",
            "history": [],
            "session_id": None,
            "manual_continuation": True,
            "continuation_from": "Parte previa.",
            "prior_segment_count": 2,
            "automatic_continuation_count": 1,
            "manual_continuation_count": 0,
        },
    )
    events = [json.loads(line) for line in response.text.splitlines()]

    assert [event["content"] for event in events if event["type"] == "token"] == [" Nueva parte."]
    assert len(provider.calls) == 1
    assert provider.calls[0][1][-2]["content"] == "Parte previa."
    assert events[-1]["segment_count"] == 3
    assert events[-1]["manual_continuation_count"] == 1
    assert events[-1]["continuation_available"] is True


async def test_manual_continuation_reuses_the_same_logical_session(client, db_session_factory):
    provider = ScriptedStreamingProvider(
        [
            [
                ProviderStreamEvent(content="Uno. "),
                ProviderStreamEvent(finish_reason="length"),
            ],
            [
                ProviderStreamEvent(content="Dos."),
                ProviderStreamEvent(finish_reason="length"),
            ],
            [
                ProviderStreamEvent(content=" Tres."),
                ProviderStreamEvent(finish_reason="stop"),
            ],
        ]
    )
    app.dependency_overrides[get_model_provider] = lambda: provider

    first_response = await client.post(
        "/api/chat/stream",
        json={"message": "Respuesta larga", "role": "teacher", "history": []},
    )
    first_events = [json.loads(line) for line in first_response.text.splitlines()]
    first_done = first_events[-1]
    partial = "".join(event["content"] for event in first_events if event["type"] == "token")
    manual_response = await client.post(
        "/api/chat/stream",
        json={
            "message": "Respuesta larga",
            "role": "teacher",
            "history": [],
            "session_id": first_done["session_id"],
            "logical_generation_id": first_done["logical_generation_id"],
            "manual_continuation": True,
            "continuation_from": partial,
            "prior_segment_count": first_done["segment_count"],
            "automatic_continuation_count": first_done["automatic_continuation_count"],
            "manual_continuation_count": first_done["manual_continuation_count"],
        },
    )
    manual_events = [json.loads(line) for line in manual_response.text.splitlines()]

    assert manual_events[-1]["session_id"] == first_done["session_id"]
    assert manual_events[-1]["logical_generation_id"] == first_done["logical_generation_id"]
    assert manual_events[-1]["segment_count"] == 3
    assert manual_events[-1]["manual_continuation_count"] == 1
    assert manual_events[-1]["continuation_available"] is False
    with db_session_factory() as db:
        sessions = db.scalars(select(LearningSession)).all()
        assert len(sessions) == 1
        assert CONTINUATION_INSTRUCTION not in sessions[0].summary


async def test_transport_failure_after_visible_content_is_not_treated_as_length(client):
    provider = FailingStreamingProvider()
    app.dependency_overrides[get_model_provider] = lambda: provider

    response = await client.post(
        "/api/chat/stream",
        json={"message": "Falla", "role": "teacher", "history": []},
    )
    events = [json.loads(line) for line in response.text.splitlines()]

    assert events == [
        {"type": "token", "content": "Parcial"},
        {"type": "error", "detail": "Conexión interrumpida"},
    ]
    assert provider.calls == 1


@pytest.mark.parametrize("block_on_attempt", [1, 2])
async def test_cancellation_stops_active_attempt_and_prevents_further_retry(
    db_session_factory, caplog, block_on_attempt
):
    provider = BlockingStreamingProvider(block_on_attempt)
    caplog.set_level(logging.INFO, logger="llc_api.api.routes")
    with db_session_factory() as db:
        response = await stream_chat(
            ChatRequest(message="Hallo", role="teacher"),
            db=db,
            model_provider=provider,
        )
        next_event = asyncio.create_task(anext(response.body_iterator))
        await asyncio.wait_for(provider.blocking.wait(), timeout=1)
        next_event.cancel()
        with pytest.raises(asyncio.CancelledError):
            await next_event
        assert db.scalar(select(LearningSession)) is None

    assert provider.calls == block_on_attempt
    cancellation_records = [
        record
        for record in caplog.records
        if getattr(record, "generation", {}).get("event") == "generation_cancelled"
    ]
    started_records = [
        record
        for record in caplog.records
        if getattr(record, "generation", {}).get("event") == "generation_segment_started"
    ]
    assert len(cancellation_records) == 1
    assert cancellation_records[0].generation["attempt"] == block_on_attempt
    assert len(started_records) == block_on_attempt
    assert len({record.generation["request_id"] for record in started_records}) == block_on_attempt
    assert (
        cancellation_records[0].generation["request_id"]
        == started_records[-1].generation["request_id"]
    )


async def test_cancellation_between_segments_prevents_continuation(db_session_factory):
    provider = ContinuationBlockingProvider()
    with db_session_factory() as db:
        response = await stream_chat(
            ChatRequest(message="Hallo", role="teacher"),
            db=db,
            model_provider=provider,
        )
        assert json.loads(await anext(response.body_iterator)) == {
            "type": "token",
            "content": "Respuesta parcial.",
        }
        assert json.loads(await anext(response.body_iterator)) == {
            "type": "continuation",
            "active": True,
        }
        await response.body_iterator.aclose()
        assert db.scalar(select(LearningSession)) is None

    assert len(provider.calls) == 1


async def test_cancellation_during_automatic_continuation_uses_active_request_id(
    db_session_factory, caplog
):
    provider = ContinuationBlockingProvider()
    caplog.set_level(logging.INFO, logger="llc_api.api.routes")
    with db_session_factory() as db:
        response = await stream_chat(
            ChatRequest(message="Hallo", role="teacher"),
            db=db,
            model_provider=provider,
        )
        await anext(response.body_iterator)
        await anext(response.body_iterator)
        blocked = asyncio.create_task(anext(response.body_iterator))
        await asyncio.wait_for(provider.blocking.wait(), timeout=1)
        blocked.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocked
        assert db.scalar(select(LearningSession)) is None

    started = [
        record.generation
        for record in caplog.records
        if getattr(record, "generation", {}).get("event") == "generation_segment_started"
    ]
    cancelled = [
        record.generation
        for record in caplog.records
        if getattr(record, "generation", {}).get("event") == "continuation_cancelled"
    ]
    assert len(provider.calls) == 2
    assert len(started) == 2
    assert len(cancelled) == 1
    assert started[0]["request_id"] != started[1]["request_id"]
    assert started[0]["logical_generation_id"] == started[1]["logical_generation_id"]
    assert cancelled[0]["request_id"] == started[1]["request_id"]


async def test_continuation_starts_streaming_before_its_segment_finishes(
    db_session_factory,
):
    provider = StreamingContinuationProvider()
    with db_session_factory() as db:
        response = await stream_chat(
            ChatRequest(message="Hallo", role="teacher"),
            db=db,
            model_provider=provider,
        )
        assert json.loads(await anext(response.body_iterator))["content"] == ("Respuesta anterior.")
        assert json.loads(await anext(response.body_iterator)) == {
            "type": "continuation",
            "active": True,
        }
        assert json.loads(await asyncio.wait_for(anext(response.body_iterator), timeout=1)) == {
            "type": "token",
            "content": "Nuevo contenido visible.",
        }
        blocked = asyncio.create_task(anext(response.body_iterator))
        await asyncio.wait_for(provider.blocking.wait(), timeout=1)
        blocked.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocked
