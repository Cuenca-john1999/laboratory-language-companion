import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any, NoReturn

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from llc_api.providers.base import (
    EmptyVisibleContentError,
    MalformedStructuredOutputError,
    ModelNotFoundError,
    ModelProvider,
    ProviderResponseError,
    ProviderStreamEvent,
    ProviderUnavailableError,
    StructuredModel,
    has_visible_content,
)
from llc_api.schemas.api import ModelInfo


class LMStudioModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str


class LMStudioModelsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    data: list[LMStudioModel]


class LMStudioProvider(ModelProvider):
    """Client for LM Studio's OpenAI-compatible local API."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 180,
        context_length: int = 8192,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        model_cache_ttl_seconds: float = 15,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.context_length = context_length
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.transport = transport
        self.model_cache_ttl_seconds = model_cache_ttl_seconds
        self._models_cache: tuple[ModelInfo, ...] | None = None
        self._models_cache_expires_at = 0.0
        self._models_lock = asyncio.Lock()
        self.last_request_metrics: dict[str, int | str] = {}

    def client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout or self.timeout, transport=self.transport)

    async def health_check(self) -> bool:
        try:
            async with self.client(3) as client:
                response = await client.get(f"{self.base_url}/models")
                return response.is_success
        except httpx.HTTPError:
            return False

    async def list_models(self) -> list[ModelInfo]:
        now = time.monotonic()
        if self._models_cache is not None and now < self._models_cache_expires_at:
            return list(self._models_cache)
        async with self._models_lock:
            now = time.monotonic()
            if self._models_cache is not None and now < self._models_cache_expires_at:
                return list(self._models_cache)
            models = await self._fetch_models()
            self._models_cache = tuple(models)
            self._models_cache_expires_at = now + self.model_cache_ttl_seconds
            return list(models)

    async def _fetch_models(self) -> list[ModelInfo]:
        try:
            async with self.client(5) as client:
                response = await client.get(f"{self.base_url}/models")
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                "LM Studio no está disponible. Inicia su servidor local y vuelve a intentarlo."
            ) from exc
        try:
            payload = LMStudioModelsResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise ProviderResponseError(
                "LM Studio devolvió una lista de modelos incompatible."
            ) from exc
        return [ModelInfo(name=item.id) for item in payload.data]

    def _completion_payload(
        self, model: str, messages: list[dict[str, Any]], *, stream: bool
    ) -> dict[str, Any]:
        return {
            "model": model,
            "messages": messages,
            "stream": stream,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

    @staticmethod
    def _raise_status(exc: httpx.HTTPStatusError, model: str) -> NoReturn:
        status = exc.response.status_code
        detail = ""
        try:
            payload = exc.response.json()
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict):
                    detail = str(error.get("message") or "")
                elif isinstance(error, str):
                    detail = error
                if not detail and isinstance(payload.get("message"), str):
                    detail = payload["message"]
        except (TypeError, ValueError):
            detail = exc.response.text[:300]
        if status == 404 or "model" in detail.casefold() and "not found" in detail.casefold():
            raise ModelNotFoundError(
                f"El modelo local '{model}' no está disponible en LM Studio."
            ) from exc
        raise ProviderResponseError(
            f"LM Studio rechazó la solicitud ({status})" + (f": {detail}" if detail else ".")
        ) from exc

    def _capture_metrics(self, model: str, payload: dict[str, Any]) -> None:
        usage = payload.get("usage")
        metrics: dict[str, int | str] = {"model": model}
        if isinstance(usage, dict):
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if isinstance(usage.get(key), int):
                    metrics[key] = usage[key]
        self.last_request_metrics = metrics

    @staticmethod
    def _usage(payload: dict[str, Any]) -> dict[str, int]:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return {}
        return {
            key: value
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if isinstance((value := usage.get(key)), int)
        }

    async def chat(self, model: str, messages: list[dict[str, str]]) -> str:
        try:
            async with self.client() as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=self._completion_payload(model, messages, stream=False),
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            self._raise_status(exc, model)
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError(
                "LM Studio agotó el tiempo de generación configurado."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                "No se pudo conectar con el servidor local de LM Studio."
            ) from exc
        try:
            choice = payload["choices"][0]
            message = choice["message"]
            content = message["content"]
            self._capture_metrics(model, payload)
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderResponseError("LM Studio devolvió una respuesta malformada.") from exc
        if not isinstance(content, str) or not has_visible_content(content):
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            finish_reason = choice.get("finish_reason")
            raise EmptyVisibleContentError(
                reasoning_present=isinstance(reasoning, str) and bool(reasoning),
                finish_reason=finish_reason if isinstance(finish_reason, str) else None,
                usage=self._usage(payload),
            )
        return content

    async def stream_chat(self, model: str, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        async for event in self.stream_chat_events(model, messages):
            if event.content:
                yield event.content

    async def stream_chat_events(
        self, model: str, messages: list[dict[str, str]]
    ) -> AsyncIterator[ProviderStreamEvent]:
        try:
            async with self.client() as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    json=self._completion_payload(model, messages, stream=True),
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line or line == "data: [DONE]":
                            continue
                        if not line.startswith("data: "):
                            raise ProviderResponseError(
                                "LM Studio devolvió un fragmento SSE incompatible."
                            )
                        try:
                            payload = json.loads(line[6:])
                            usage = self._usage(payload)
                            choices = payload.get("choices")
                            if isinstance(choices, list) and not choices and usage:
                                yield ProviderStreamEvent(usage=usage)
                                continue
                            choice = choices[0]
                            delta = choice["delta"]
                            content = delta.get("content")
                            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                            finish_reason = choice.get("finish_reason")
                        except (ValueError, KeyError, IndexError, TypeError) as exc:
                            raise ProviderResponseError(
                                "LM Studio devolvió un fragmento SSE malformado."
                            ) from exc
                        if (
                            isinstance(content, str)
                            or isinstance(reasoning, str)
                            or isinstance(finish_reason, str)
                            or usage
                        ):
                            yield ProviderStreamEvent(
                                content=content if isinstance(content, str) else "",
                                reasoning_present=isinstance(reasoning, str) and bool(reasoning),
                                finish_reason=(
                                    finish_reason if isinstance(finish_reason, str) else None
                                ),
                                usage=usage,
                            )
        except ProviderResponseError:
            raise
        except httpx.HTTPStatusError as exc:
            self._raise_status(exc, model)
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError(
                "LM Studio agotó el tiempo durante el streaming."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                "Se perdió la conexión con LM Studio durante el streaming."
            ) from exc

    async def structured_generate(
        self, model: str, messages: list[dict[str, str]], schema: type[StructuredModel]
    ) -> StructuredModel:
        payload = self._completion_payload(model, messages, stream=False)
        payload["temperature"] = 0
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "strict": True,
                "schema": schema.model_json_schema(),
            },
        }
        raw = await self._request_structured(model, payload)
        try:
            return schema.model_validate_json(raw)
        except ValidationError as validation_error:
            repair_contract = {
                "instruction": (
                    "Corrige únicamente formato y estructura. No añadas contenido nuevo. "
                    "Devuelve solo JSON."
                ),
                "validation_errors": validation_error.errors(include_url=False),
                "schema": schema.model_json_schema(),
            }
            repair = [
                {
                    "role": "system",
                    "content": (
                        "Repair only the supplied JSON so it matches the supplied schema. "
                        "Do not add facts or content. Return JSON only."
                    ),
                },
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": "Repara el JSON para cumplir exactamente el esquema.\n"
                    + json.dumps(repair_contract, ensure_ascii=False, sort_keys=True),
                },
            ]
            payload = self._completion_payload(model, repair, stream=False)
            payload["temperature"] = 0
            payload["response_format"] = {"type": "json_object"}
            repaired = await self._request_structured(model, payload)
            try:
                return schema.model_validate_json(repaired)
            except ValidationError as exc:
                raise MalformedStructuredOutputError(
                    "LM Studio no produjo JSON válido después de repararlo."
                ) from exc

    async def _request_structured(self, model: str, payload: dict[str, Any]) -> str:
        try:
            async with self.client() as client:
                response = await client.post(f"{self.base_url}/chat/completions", json=payload)
                response.raise_for_status()
                body = response.json()
                self._capture_metrics(model, body)
                content = body["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            self._raise_status(exc, model)
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                "Falló la generación estructurada en LM Studio."
            ) from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderResponseError(
                "LM Studio devolvió datos estructurados ilegibles."
            ) from exc
        if not isinstance(content, str):
            raise ProviderResponseError("LM Studio no devolvió contenido estructurado.")
        return content

    async def embeddings(self, texts: list[str], model: str) -> list[list[float]]:
        if not texts:
            return []
        try:
            async with self.client() as client:
                response = await client.post(
                    f"{self.base_url}/embeddings",
                    json={"model": model, "input": texts},
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            self._raise_status(exc, model)
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("LM Studio no pudo generar embeddings.") from exc
        try:
            ordered = sorted(payload["data"], key=lambda item: item["index"])
            vectors = [item["embedding"] for item in ordered]
        except (KeyError, TypeError) as exc:
            raise ProviderResponseError("LM Studio devolvió embeddings malformados.") from exc
        if len(vectors) != len(texts) or any(
            not isinstance(item, list) or not item for item in vectors
        ):
            raise ProviderResponseError("LM Studio devolvió un número inválido de embeddings.")
        return [[float(value) for value in vector] for vector in vectors]
