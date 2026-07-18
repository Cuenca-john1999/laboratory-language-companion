import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from deutschos_api.providers.base import (
    MalformedStructuredOutputError,
    ModelNotFoundError,
    ModelProvider,
    ProviderResponseError,
    ProviderUnavailableError,
    StructuredModel,
)
from deutschos_api.schemas.api import ModelInfo


class OllamaModel(BaseModel):
    """Provider DTO: accept Ollama evolution without widening the public API."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    name: str | None = None
    model: str | None = None
    size: int | None = Field(default=None, ge=0)
    modified_at: datetime | None = None

    @model_validator(mode="after")
    def require_model_identifier(self) -> "OllamaModel":
        if not self.model and not self.name:
            raise ValueError("Ollama model entry has no model identifier")
        return self

    def to_model_info(self) -> ModelInfo:
        # Ollama uses `model` in chat requests. Older versions may expose only
        # `name`, so keep it as a compatibility fallback.
        identifier = self.model or self.name
        if identifier is None:  # Enforced by require_model_identifier.
            raise AssertionError("validated Ollama model has no identifier")
        return ModelInfo(
            name=identifier,
            size=self.size,
            modified_at=self.modified_at,
        )


class OllamaTagsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    models: list[OllamaModel]


class OllamaMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    content: str


class OllamaChatResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    message: OllamaMessage


class OllamaStreamResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    message: OllamaMessage | None = None
    done: bool = False
    error: str | None = None


class OllamaProvider(ModelProvider):
    def __init__(
        self,
        base_url: str,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
        keep_alive: str = "5m",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport
        self.keep_alive = keep_alive
        self.last_request_metrics: dict[str, int | str] = {}

    def _capture_metrics(self, model: str, payload: object) -> None:
        if not isinstance(payload, dict):
            self.last_request_metrics = {"model": model}
            return
        metrics: dict[str, int | str] = {"model": model}
        for key in (
            "total_duration",
            "load_duration",
            "prompt_eval_count",
            "prompt_eval_duration",
            "eval_count",
            "eval_duration",
        ):
            value = payload.get(key)
            if isinstance(value, int):
                metrics[key] = value
        self.last_request_metrics = metrics

    def client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self.transport)

    async def health_check(self) -> bool:
        try:
            async with self.client(timeout=3) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                return response.is_success
        except httpx.HTTPError:
            return False

    async def list_models(self) -> list[ModelInfo]:
        try:
            async with self.client(timeout=5) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                "No se puede conectar con Ollama. Comprueba que esté instalado y ejecutándose."
            ) from exc
        try:
            payload = OllamaTagsResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise ProviderResponseError("Ollama devolvió una lista de modelos inválida.") from exc
        return [item.to_model_info() for item in payload.models]

    async def chat(self, model: str, messages: list[dict[str, str]]) -> str:
        try:
            async with self.client(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": False,
                        "think": False,
                        "keep_alive": self.keep_alive,
                    },
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ModelNotFoundError(
                    f"El modelo local '{model}' no está instalado en Ollama."
                ) from exc
            raise ProviderUnavailableError("Ollama rechazó la solicitud de conversación.") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                "Ollama no respondió. Inícialo y confirma que el modelo seleccionado está descargado."
            ) from exc
        try:
            raw_payload = response.json()
            self._capture_metrics(model, raw_payload)
            payload = OllamaChatResponse.model_validate(raw_payload)
        except (ValueError, ValidationError) as exc:
            raise ProviderResponseError("Ollama devolvió una respuesta inválida.") from exc
        if not payload.message.content:
            raise ProviderResponseError("Ollama devolvió una respuesta vacía.")
        return payload.message.content

    async def stream_chat(self, model: str, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        try:
            async with self.client(timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/api/chat",
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": True,
                        "think": False,
                        "keep_alive": self.keep_alive,
                    },
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            chunk = OllamaStreamResponse.model_validate_json(line)
                        except ValidationError as exc:
                            raise ProviderResponseError(
                                "Ollama devolvió un fragmento de respuesta inválido."
                            ) from exc
                        if chunk.error:
                            raise ProviderResponseError(chunk.error)
                        if chunk.message and chunk.message.content:
                            yield chunk.message.content
        except ProviderResponseError:
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ModelNotFoundError(
                    f"El modelo local '{model}' no está instalado en Ollama."
                ) from exc
            raise ProviderUnavailableError("Ollama rechazó la conversación.") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("Ollama no respondió durante la conversación.") from exc

    async def structured_generate(
        self, model: str, messages: list[dict[str, str]], schema: type[StructuredModel]
    ) -> StructuredModel:
        provider_schema = _ollama_compatible_schema(schema.model_json_schema())
        raw = await self._structured_request(model, messages, provider_schema)
        try:
            return schema.model_validate_json(raw)
        except ValidationError as validation_error:
            issues = [
                {
                    "location": ".".join(str(part) for part in issue["loc"]),
                    "type": issue["type"],
                    "message": issue["msg"],
                }
                for issue in validation_error.errors(include_input=False)
            ][:20]
            repair_messages = messages + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        "Repara el JSON para ajustarlo exactamente al esquema. Devuelve solo JSON. "
                        "Errores de validación: " + json.dumps(issues, ensure_ascii=False)
                    ),
                },
            ]
            repaired = await self._structured_request(model, repair_messages, provider_schema)
            try:
                return schema.model_validate_json(repaired)
            except ValidationError as exc:
                raise MalformedStructuredOutputError(
                    "El modelo no produjo datos estructurados válidos después de un intento de reparación."
                ) from exc

    async def _structured_request(
        self, model: str, messages: list[dict[str, str]], schema: dict[str, Any]
    ) -> str:
        try:
            async with self.client(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": False,
                        "think": False,
                        "format": schema,
                        "options": {"temperature": 0, "num_predict": 2_048},
                        "keep_alive": self.keep_alive,
                    },
                )
                response.raise_for_status()
                raw_payload = response.json()
                self._capture_metrics(model, raw_payload)
                payload = OllamaChatResponse.model_validate(raw_payload)
                return payload.message.content
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("Falló la generación estructurada local.") from exc
        except (ValueError, ValidationError, json.JSONDecodeError) as exc:
            raise ProviderResponseError("Ollama devolvió datos estructurados ilegibles.") from exc


def _ollama_compatible_schema(value: Any) -> Any:
    """Keep semantic constraints in Pydantic without exceeding Ollama's grammar limits."""
    if isinstance(value, dict):
        return {
            key: _ollama_compatible_schema(item)
            for key, item in value.items()
            if key not in {"maxLength", "minLength", "maxItems", "minItems"}
        }
    if isinstance(value, list):
        return [_ollama_compatible_schema(item) for item in value]
    return value
