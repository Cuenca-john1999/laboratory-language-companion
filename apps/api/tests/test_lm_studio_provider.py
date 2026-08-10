import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from llc_api.providers.base import (
    EmptyVisibleContentError,
    MalformedStructuredOutputError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from llc_api.providers.lm_studio import LMStudioProvider
from llc_api.schemas.api import ModelInfo

pytestmark = pytest.mark.anyio

LM_STUDIO_MODELS = {
    "data": [
        {
            "id": "google/gemma-4-12b-qat",
            "object": "model",
            "owned_by": "organization_owner",
        }
    ],
    "object": "list",
}


def provider_with(handler) -> LMStudioProvider:
    return LMStudioProvider("http://lm_studio.test", transport=httpx.MockTransport(handler))


async def test_malformed_model_list_is_normalized():
    provider = provider_with(lambda _request: httpx.Response(200, content=b"not-json"))
    with pytest.raises(ProviderResponseError):
        await provider.list_models()


async def test_lm_studio_model_metadata_is_explicitly_normalized():
    provider = provider_with(lambda _request: httpx.Response(200, json=LM_STUDIO_MODELS))

    models = await provider.list_models()

    assert len(models) == 1
    model = models[0]
    assert model.name == "google/gemma-4-12b-qat"
    assert model.size is None
    assert model.modified_at is None
    assert model.model_dump().keys() == {"name", "size", "modified_at"}


async def test_empty_model_list_is_valid():
    provider = provider_with(lambda _request: httpx.Response(200, json={"data": []}))

    assert await provider.list_models() == []


async def test_concurrent_and_cached_model_lists_share_one_lm_studio_request():
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return httpx.Response(200, json=LM_STUDIO_MODELS)

    provider = LMStudioProvider(
        "http://lm_studio.test",
        transport=httpx.MockTransport(handler),
        model_cache_ttl_seconds=60,
    )

    first, second = await asyncio.gather(provider.list_models(), provider.list_models())
    third = await provider.list_models()

    assert first == second == third
    assert calls == 1


async def test_unknown_external_model_fields_are_not_exposed():
    provider = provider_with(
        lambda _request: httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "future:latest",
                        "size": 42,
                        "future_metadata": {"private": "provider-only"},
                    }
                ]
            },
        )
    )

    assert (await provider.list_models())[0].model_dump() == {
        "name": "future:latest",
        "size": None,
        "modified_at": None,
    }


def test_internal_model_info_remains_strict():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ModelInfo.model_validate({"name": "google/gemma-4-12b-qat", "digest": "provider-only"})


async def test_malformed_model_item_is_normalized():
    provider = provider_with(
        lambda _request: httpx.Response(200, json={"data": [{"object": "model"}]})
    )

    with pytest.raises(ProviderResponseError):
        await provider.list_models()


async def test_disconnected_lm_studio_is_normalized():
    def disconnect(request):
        raise httpx.ConnectError("connection refused", request=request)

    provider = provider_with(disconnect)

    with pytest.raises(ProviderUnavailableError):
        await provider.list_models()


async def test_malformed_chat_response_is_normalized():
    provider = provider_with(lambda _request: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(ProviderResponseError):
        await provider.chat("model", [{"role": "user", "content": "Hallo"}])


async def test_streaming_response_is_decoupled_into_text_chunks():
    content = "\n".join(
        [
            "data: " + json.dumps({"choices": [{"delta": {"content": "Guten "}}]}),
            "data: " + json.dumps({"choices": [{"delta": {"content": "Tag"}}]}),
            "data: [DONE]",
        ]
    )
    provider = provider_with(lambda _request: httpx.Response(200, text=content))
    chunks = [
        chunk
        async for chunk in provider.stream_chat("model", [{"role": "user", "content": "Hallo"}])
    ]
    assert chunks == ["Guten ", "Tag"]


async def test_streaming_keeps_generation_budget_and_temperature_per_call():
    captured: dict[str, object] = {}

    def handler(request):
        captured.update(json.loads(request.content))
        content = "\n".join(
            [
                "data: "
                + json.dumps(
                    {"choices": [{"delta": {"content": "Antwort"}, "finish_reason": "stop"}]}
                ),
                "data: [DONE]",
            ]
        )
        return httpx.Response(200, text=content)

    provider = provider_with(handler)
    events = [
        event
        async for event in provider.stream_chat_events(
            "model", [{"role": "user", "content": "Hallo"}]
        )
    ]

    assert events[0].content == "Antwort"
    assert captured["max_tokens"] == 2048
    assert captured["temperature"] == 0.2


async def test_streaming_separates_reasoning_finish_reason_and_usage_from_visible_text():
    private_reasoning = "contenido interno privado"
    content = "\n".join(
        [
            "data: "
            + json.dumps(
                {
                    "model": "model",
                    "choices": [
                        {"delta": {"reasoning_content": private_reasoning}, "finish_reason": None}
                    ],
                }
            ),
            "data: "
            + json.dumps(
                {
                    "model": "model",
                    "choices": [{"delta": {"content": "Sí."}, "finish_reason": None}],
                }
            ),
            "data: "
            + json.dumps(
                {
                    "model": "model",
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                }
            ),
            "data: "
            + json.dumps(
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 20,
                        "total_tokens": 30,
                    },
                }
            ),
            "data: [DONE]",
        ]
    )
    provider = provider_with(lambda _request: httpx.Response(200, text=content))

    events = [
        event
        async for event in provider.stream_chat_events(
            "model", [{"role": "user", "content": "Hallo"}]
        )
    ]
    visible_chunks = [
        chunk
        async for chunk in provider.stream_chat("model", [{"role": "user", "content": "Hallo"}])
    ]

    assert events[0].reasoning_present is True
    assert events[0].content == ""
    assert events[1].content == "Sí."
    assert events[2].finish_reason == "stop"
    assert events[3].usage == {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30,
    }
    assert visible_chunks == ["Sí."]
    assert private_reasoning not in "".join(visible_chunks)


@pytest.mark.parametrize("content", ["", " \n", "<think></think>", "<|end|>"])
async def test_non_streaming_rejects_only_empty_visible_content(content):
    provider = provider_with(
        lambda _request: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": content,
                            "reasoning_content": "contenido interno privado",
                        },
                        "finish_reason": "length",
                    }
                ],
                "usage": {"completion_tokens": 2048},
            },
        )
    )

    with pytest.raises(EmptyVisibleContentError) as raised:
        await provider.chat("model", [{"role": "user", "content": "Hallo"}])

    assert raised.value.reasoning_present is True
    assert raised.value.finish_reason == "length"
    assert raised.value.usage == {"completion_tokens": 2048}


@pytest.mark.parametrize("content", ["Sí.", "Gut", "$x$", "**Ja.**"])
async def test_non_streaming_accepts_short_visible_content(content):
    provider = provider_with(
        lambda _request: httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
        )
    )

    assert await provider.chat("model", [{"role": "user", "content": "Hallo"}]) == content


class StructuredResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: float


class LongStructuredResult(BaseModel):
    text: str = Field(min_length=1, max_length=8_000)
    items: list[str] = Field(min_length=1, max_length=50)


async def test_structured_generation_sends_lm_studio_compatible_schema():
    captured: dict[str, object] = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"text":"ok","items":["one"]}'}}]},
        )

    provider = provider_with(handler)
    result = await provider.structured_generate(
        "model", [{"role": "user", "content": "test"}], LongStructuredResult
    )

    assert result.text == "ok"
    assert captured["response_format"]["type"] == "json_schema"
    assert captured["temperature"] == 0


async def test_structured_generation_repairs_once_then_fails():
    calls = 0
    requests: list[dict[str, object]] = []

    def handler(request):
        nonlocal calls
        calls += 1
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"wrong": true}'}}]})

    provider = provider_with(handler)
    with pytest.raises(MalformedStructuredOutputError):
        await provider.structured_generate(
            "model", [{"role": "user", "content": "score"}], StructuredResult
        )
    assert calls == 2
    repair_text = requests[1]["messages"][-1]["content"]
    assert "Repara el JSON" in repair_text
