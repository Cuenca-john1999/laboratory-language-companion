import json

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from deutschos_api.providers.base import (
    MalformedStructuredOutputError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from deutschos_api.providers.ollama import OllamaProvider
from deutschos_api.schemas.api import ModelInfo

pytestmark = pytest.mark.anyio

OLLAMA_0312_TAGS = {
    "models": [
        {
            "name": "qwen3:14b",
            "model": "qwen3:14b",
            "modified_at": "2026-07-13T19:53:49.054584096+02:00",
            "size": 9_276_198_565,
            "digest": "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8",
            "details": {
                "format": "gguf",
                "family": "qwen3",
                "parameter_size": "14.8B",
                "quantization_level": "Q4_K_M",
            },
            "capabilities": ["completion", "tools", "thinking"],
        }
    ],
    "future_top_level_field": {"ignored": True},
}


def provider_with(handler) -> OllamaProvider:
    return OllamaProvider("http://ollama.test", transport=httpx.MockTransport(handler))


async def test_malformed_model_list_is_normalized():
    provider = provider_with(lambda _request: httpx.Response(200, content=b"not-json"))
    with pytest.raises(ProviderResponseError):
        await provider.list_models()


async def test_ollama_0312_model_metadata_is_explicitly_normalized():
    provider = provider_with(lambda _request: httpx.Response(200, json=OLLAMA_0312_TAGS))

    models = await provider.list_models()

    assert len(models) == 1
    model = models[0]
    assert model.name == "qwen3:14b"
    assert model.size == 9_276_198_565
    assert model.modified_at is not None
    assert model.modified_at.isoformat() == "2026-07-13T19:53:49.054584+02:00"
    assert model.model_dump().keys() == {"name", "size", "modified_at"}


@pytest.mark.parametrize(
    ("external", "expected"),
    [
        ({"name": "legacy:latest"}, "legacy:latest"),
        ({"model": "modern:latest"}, "modern:latest"),
        (
            {"name": "display-alias:latest", "model": "chat-identifier:latest"},
            "chat-identifier:latest",
        ),
    ],
)
async def test_model_identifier_uses_model_with_name_fallback(external, expected):
    provider = provider_with(lambda _request: httpx.Response(200, json={"models": [external]}))

    assert (await provider.list_models())[0].name == expected


async def test_empty_model_list_is_valid():
    provider = provider_with(lambda _request: httpx.Response(200, json={"models": []}))

    assert await provider.list_models() == []


async def test_unknown_external_model_fields_are_not_exposed():
    provider = provider_with(
        lambda _request: httpx.Response(
            200,
            json={
                "models": [
                    {
                        "model": "future:latest",
                        "size": 42,
                        "future_metadata": {"private": "provider-only"},
                    }
                ]
            },
        )
    )

    assert (await provider.list_models())[0].model_dump() == {
        "name": "future:latest",
        "size": 42,
        "modified_at": None,
    }


def test_internal_model_info_remains_strict():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ModelInfo.model_validate({"name": "qwen3:14b", "digest": "provider-only"})


async def test_malformed_model_item_is_normalized():
    provider = provider_with(
        lambda _request: httpx.Response(200, json={"models": [{"digest": "missing-id"}]})
    )

    with pytest.raises(ProviderResponseError):
        await provider.list_models()


async def test_disconnected_ollama_is_normalized():
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
            json.dumps({"message": {"content": "Guten "}, "done": False}),
            json.dumps({"message": {"content": "Tag"}, "done": False}),
            json.dumps({"message": {"content": ""}, "done": True}),
        ]
    )
    provider = provider_with(lambda _request: httpx.Response(200, text=content))
    chunks = [
        chunk
        async for chunk in provider.stream_chat("model", [{"role": "user", "content": "Hallo"}])
    ]
    assert chunks == ["Guten ", "Tag"]


class StructuredResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: float


class LongStructuredResult(BaseModel):
    text: str = Field(min_length=1, max_length=8_000)
    items: list[str] = Field(min_length=1, max_length=50)


async def test_structured_generation_sends_ollama_compatible_schema():
    captured: dict[str, object] = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"message": {"content": '{"text":"ok","items":["one"]}'}},
        )

    provider = provider_with(handler)
    result = await provider.structured_generate(
        "model", [{"role": "user", "content": "test"}], LongStructuredResult
    )

    assert result.text == "ok"
    assert "maxLength" not in json.dumps(captured["format"])
    assert "maxItems" not in json.dumps(captured["format"])
    assert captured["options"] == {"temperature": 0, "num_predict": 2_048}
    assert captured["think"] is False


async def test_structured_generation_repairs_once_then_fails():
    calls = 0
    requests: list[dict[str, object]] = []

    def handler(request):
        nonlocal calls
        calls += 1
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": '{"wrong": true}'}})

    provider = provider_with(handler)
    with pytest.raises(MalformedStructuredOutputError):
        await provider.structured_generate(
            "model", [{"role": "user", "content": "score"}], StructuredResult
        )
    assert calls == 2
    repair_text = requests[1]["messages"][-1]["content"]
    assert "score" in repair_text
    assert "missing" in repair_text
