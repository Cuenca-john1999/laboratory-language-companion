import json

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from deutschos_api.providers.base import (
    MalformedStructuredOutputError,
    ProviderResponseError,
)
from deutschos_api.providers.ollama import OllamaProvider

pytestmark = pytest.mark.anyio


def provider_with(handler) -> OllamaProvider:
    return OllamaProvider("http://ollama.test", transport=httpx.MockTransport(handler))


async def test_malformed_model_list_is_normalized():
    provider = provider_with(lambda _request: httpx.Response(200, content=b"not-json"))
    with pytest.raises(ProviderResponseError):
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


async def test_structured_generation_repairs_once_then_fails():
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"message": {"content": '{"wrong": true}'}})

    provider = provider_with(handler)
    with pytest.raises(MalformedStructuredOutputError):
        await provider.structured_generate(
            "model", [{"role": "user", "content": "score"}], StructuredResult
        )
    assert calls == 2
