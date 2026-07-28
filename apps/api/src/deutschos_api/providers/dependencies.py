from fastapi import Depends

from deutschos_api.core.config import Settings, get_settings
from deutschos_api.providers.base import ModelProvider
from deutschos_api.providers.lm_studio import LMStudioProvider


def get_model_provider(settings: Settings = Depends(get_settings)) -> ModelProvider:
    """The only composition point that knows which concrete provider is configured."""
    return LMStudioProvider(
        settings.lm_studio_base_url,
        timeout=settings.lm_studio_timeout_seconds,
        context_length=settings.lm_studio_context_length,
        temperature=settings.lm_studio_temperature,
        max_tokens=settings.lm_studio_max_tokens,
    )
