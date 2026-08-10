from functools import lru_cache

from fastapi import Depends

from llc_api.core.config import Settings, get_settings
from llc_api.providers.base import ModelProvider
from llc_api.providers.lm_studio import LMStudioProvider


@lru_cache(maxsize=4)
def _cached_model_provider(
    base_url: str,
    timeout: float,
    context_length: int,
    temperature: float,
    max_tokens: int,
) -> LMStudioProvider:
    return LMStudioProvider(
        base_url,
        timeout=timeout,
        context_length=context_length,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def get_model_provider(settings: Settings = Depends(get_settings)) -> ModelProvider:
    """The only composition point that knows which concrete provider is configured."""
    return _cached_model_provider(
        settings.lm_studio_base_url,
        settings.lm_studio_timeout_seconds,
        settings.lm_studio_context_length,
        settings.lm_studio_temperature,
        settings.lm_studio_max_tokens,
    )
