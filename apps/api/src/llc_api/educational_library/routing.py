from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from llc_api.providers.base import (
    MalformedStructuredOutputError,
    ModelNotFoundError,
    ModelProvider,
    ProviderResponseError,
    ProviderUnavailableError,
    StructuredModel,
)

from .schemas import ModelRoleRead, ModelRoutingRead, TeacherFailureReason

ROUTING_POLICY_VERSION = "library-model-routing.v1"
_LARGE_MODEL_LOCK = asyncio.Lock()
_T = TypeVar("_T", bound=StructuredModel)


class ModelRole(StrEnum):
    PLANNER = "planner"
    EMBEDDING = "embedding"
    TEACHER = "teacher"
    DEEP = "deep"
    VISION = "vision"
    REPAIR = "repair"


@dataclass(frozen=True)
class ModelRoutingPolicy:
    planner: str
    embedding: str
    teacher: str
    fallback: str
    vision: str
    repair: str
    planner_timeout: float = 45
    teacher_timeout: float = 180

    def configured(self, role: ModelRole) -> str:
        if role == ModelRole.DEEP:
            return self.fallback
        return str(getattr(self, role.value))


class ModelRoutingError(RuntimeError):
    def __init__(self, reason: TeacherFailureReason, message: str):
        super().__init__(message)
        self.reason = reason


class LibraryModelRouter:
    """Capability-aware local routing with one configured model per role."""

    def __init__(self, provider: ModelProvider, policy: ModelRoutingPolicy):
        self.provider = provider
        self.policy = policy
        self._installed: tuple[str, ...] | None = None

    async def installed_models(self, *, refresh: bool = False) -> tuple[str, ...]:
        if self._installed is not None and not refresh:
            return self._installed
        try:
            models = await asyncio.wait_for(self.provider.list_models(), timeout=5)
        except (TimeoutError, ProviderUnavailableError, ProviderResponseError) as exc:
            raise ModelRoutingError(
                TeacherFailureReason.MODEL_UNAVAILABLE,
                "No se pudo consultar la capacidad de los modelos locales.",
            ) from exc
        self._installed = tuple(sorted({model.name for model in models}))
        return self._installed

    def candidates(self, role: ModelRole) -> tuple[str, ...]:
        configured = self.policy.configured(role)
        return (configured,) if configured else ()

    async def select(self, role: ModelRole) -> str:
        installed = set(await self.installed_models())
        selected = next((model for model in self.candidates(role) if model in installed), "")
        if not selected:
            raise ModelRoutingError(
                TeacherFailureReason.MODEL_UNAVAILABLE,
                f"No hay un modelo local disponible para el rol {role.value}.",
            )
        return selected

    async def status(self) -> ModelRoutingRead:
        try:
            installed = list(await self.installed_models())
            available = True
        except ModelRoutingError:
            installed = []
            available = False
        installed_set = set(installed)
        authorized_models = {
            self.policy.configured(role) for role in ModelRole if self.policy.configured(role)
        }
        roles = []
        for role in ModelRole:
            candidates = self.candidates(role)
            selected = next((model for model in candidates if model in installed_set), None)
            roles.append(
                ModelRoleRead(
                    role=role.value,
                    configured_model=self.policy.configured(role),
                    available=selected is not None,
                    selected_model=selected,
                    fallback_models=list(candidates[1:]),
                )
            )
        return ModelRoutingRead(
            lm_studio_available=available,
            installed_models=sorted(installed_set & authorized_models),
            roles=roles,
            policy_version=ROUTING_POLICY_VERSION,
        )

    async def structured_generate(
        self,
        role: ModelRole,
        messages: list[dict[str, str]],
        schema: type[_T],
    ) -> tuple[_T, str, bool]:
        installed = set(await self.installed_models())
        candidates = [model for model in self.candidates(role) if model in installed]
        if not candidates:
            raise ModelRoutingError(
                TeacherFailureReason.MODEL_UNAVAILABLE,
                f"No hay un modelo local disponible para el rol {role.value}.",
            )
        timeout = (
            self.policy.planner_timeout
            if role in {ModelRole.PLANNER, ModelRole.REPAIR}
            else self.policy.teacher_timeout
        )
        model = candidates[0]
        try:
            call = self.provider.structured_generate(model, messages, schema)
            if role in {ModelRole.TEACHER, ModelRole.DEEP} and self._is_large(model):
                async with _LARGE_MODEL_LOCK:
                    result = await asyncio.wait_for(call, timeout=timeout)
            else:
                result = await asyncio.wait_for(call, timeout=timeout)
            return result, model, False
        except TimeoutError as exc:
            last_error: BaseException = exc
        except (
            ModelNotFoundError,
            ProviderUnavailableError,
            ProviderResponseError,
            MalformedStructuredOutputError,
        ) as exc:
            last_error = exc
        reason = (
            TeacherFailureReason.TIMEOUT
            if isinstance(last_error, TimeoutError)
            else TeacherFailureReason.MODEL_UNAVAILABLE
        )
        message = (
            "El modelo local agotó el tiempo disponible."
            if reason == TeacherFailureReason.TIMEOUT
            else "El modelo local configurado no está disponible."
        )
        raise ModelRoutingError(reason, message) from last_error

    @staticmethod
    def _is_large(model: str) -> bool:
        return any(marker in model.casefold() for marker in ("14b", "27b", "32b", "70b"))
