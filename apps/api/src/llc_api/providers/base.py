import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

from llc_api.schemas.api import ModelInfo


class ProviderUnavailableError(RuntimeError):
    pass


class ProviderResponseError(RuntimeError):
    pass


class EmptyVisibleContentError(ProviderResponseError):
    def __init__(
        self,
        *,
        reasoning_present: bool = False,
        finish_reason: str | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        super().__init__("LM Studio devolvió una respuesta sin contenido visible.")
        self.reasoning_present = reasoning_present
        self.finish_reason = finish_reason
        self.usage = usage or {}


class ModelNotFoundError(RuntimeError):
    pass


class MalformedStructuredOutputError(RuntimeError):
    pass


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)

_TECHNICAL_MARKER = re.compile(
    r"(?:</?(?:think|analysis|reasoning)>|<\|[^|>\r\n]+\|>)",
    re.IGNORECASE,
)


def has_visible_content(content: str) -> bool:
    """Return whether completed model content has something renderable."""
    return bool(_TECHNICAL_MARKER.sub("", content).strip())


@dataclass(slots=True)
class ProviderStreamEvent:
    content: str = ""
    reasoning_present: bool = False
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


class ModelProvider(ABC):
    @abstractmethod
    async def health_check(self) -> bool: ...

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]: ...

    @abstractmethod
    async def chat(self, model: str, messages: list[dict[str, str]]) -> str: ...

    async def stream_chat(self, model: str, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        yield await self.chat(model, messages)

    async def stream_chat_events(
        self, model: str, messages: list[dict[str, str]]
    ) -> AsyncIterator[ProviderStreamEvent]:
        async for content in self.stream_chat(model, messages):
            yield ProviderStreamEvent(content=content)

    @abstractmethod
    async def structured_generate(
        self, model: str, messages: list[dict[str, str]], schema: type[StructuredModel]
    ) -> StructuredModel: ...

    async def embeddings(self, texts: list[str]) -> Any:
        raise NotImplementedError("Embeddings are reserved for a later milestone")
