from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from .models import (
    CapabilityRequirements,
    CloudProviderDescriptor,
    ProviderAvailability,
    ProviderExtractionRequest,
    ProviderRawResponse,
)


class CloudProviderError(RuntimeError):
    def __init__(
        self,
        state: ProviderAvailability,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, object] | None = None,
    ):
        super().__init__(message)
        self.state = state
        self.safe_message = message
        self.code = code
        self.details = details or {}


class CloudKnowledgeProvider(ABC):
    @property
    @abstractmethod
    def descriptor(self) -> CloudProviderDescriptor: ...

    @abstractmethod
    def extract(self, request: ProviderExtractionRequest) -> ProviderRawResponse: ...


class CloudProviderRegistry:
    """Deterministic registry; future routing policies can wrap ``resolve``."""

    def __init__(self, providers: Iterable[CloudKnowledgeProvider] = ()):
        self._providers: dict[str, CloudKnowledgeProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: CloudKnowledgeProvider) -> None:
        provider_id = provider.descriptor.provider_id
        if provider_id in self._providers:
            raise ValueError(f"cloud provider already registered: {provider_id}")
        self._providers[provider_id] = provider

    def descriptors(self) -> list[CloudProviderDescriptor]:
        return [self._providers[key].descriptor for key in sorted(self._providers)]

    def resolve(
        self,
        provider_id: str,
        required: CapabilityRequirements,
    ) -> CloudKnowledgeProvider:
        if provider_id != "auto":
            provider = self._providers.get(provider_id)
            if provider is None:
                raise CloudProviderError(
                    ProviderAvailability.UNAVAILABLE,
                    f"Cloud provider is not installed: {provider_id}",
                    code="provider_not_installed",
                )
            return self._require_compatible(provider, required)

        compatible = [
            provider
            for provider in self._providers.values()
            if provider.descriptor.configured
            and provider.descriptor.availability == ProviderAvailability.AVAILABLE
            and not provider.descriptor.capabilities.missing(required)
        ]
        if len(compatible) == 1:
            return compatible[0]
        if not compatible:
            raise CloudProviderError(
                ProviderAvailability.UNAVAILABLE,
                "No configured cloud provider satisfies the requested capabilities.",
                code="no_compatible_provider",
            )
        return sorted(compatible, key=lambda item: item.descriptor.provider_id)[0]

    @staticmethod
    def _require_compatible(
        provider: CloudKnowledgeProvider,
        required: CapabilityRequirements,
    ) -> CloudKnowledgeProvider:
        descriptor = provider.descriptor
        if not descriptor.configured:
            raise CloudProviderError(
                ProviderAvailability.AUTHENTICATION_ERROR,
                f"Cloud provider is not configured: {descriptor.provider_id}",
                code="provider_not_configured",
            )
        if descriptor.availability != ProviderAvailability.AVAILABLE:
            raise CloudProviderError(
                descriptor.availability,
                f"Cloud provider is not available: {descriptor.provider_id}",
                code="provider_unavailable",
            )
        missing = descriptor.capabilities.missing(required)
        if missing:
            raise CloudProviderError(
                ProviderAvailability.INVALID_REQUEST,
                "Provider lacks required capabilities: " + ", ".join(missing),
                code="capability_mismatch",
                details={"missing_capabilities": missing},
            )
        return provider
