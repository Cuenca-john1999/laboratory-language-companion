from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import Field

from deutschos_api.schemas.base import APIModel


class TranscriptionSegment(APIModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    text: str = Field(min_length=1, max_length=20_000)


class TranscriptionResult(APIModel):
    backend: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    language: str | None = Field(default=None, max_length=20)
    segments: list[TranscriptionSegment] = Field(max_length=100_000)


class TranscriptionProvider(Protocol):
    backend_name: str
    model_name: str

    async def available(self) -> bool: ...

    async def transcribe(self, path: Path) -> TranscriptionResult: ...


class UnavailableTranscriptionProvider:
    """Explicit fallback: it never fabricates a transcript."""

    backend_name = "unavailable"
    model_name = "unavailable"

    async def available(self) -> bool:
        return False

    async def transcribe(self, path: Path) -> TranscriptionResult:
        del path
        raise RuntimeError("No hay un transcriptor local configurado.")
