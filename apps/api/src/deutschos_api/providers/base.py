from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, TypeVar

from pydantic import BaseModel

from deutschos_api.schemas.api import ModelInfo


class ProviderUnavailableError(RuntimeError):
    pass


class ProviderResponseError(RuntimeError):
    pass


class ModelNotFoundError(RuntimeError):
    pass


class MalformedStructuredOutputError(RuntimeError):
    pass


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class ModelProvider(ABC):
    @abstractmethod
    async def health_check(self) -> bool: ...

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]: ...

    @abstractmethod
    async def chat(self, model: str, messages: list[dict[str, str]]) -> str: ...

    async def stream_chat(self, model: str, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        yield await self.chat(model, messages)

    @abstractmethod
    async def structured_generate(
        self, model: str, messages: list[dict[str, str]], schema: type[StructuredModel]
    ) -> StructuredModel: ...

    async def embeddings(self, texts: list[str]) -> Any:
        raise NotImplementedError("Embeddings are reserved for a later milestone")
