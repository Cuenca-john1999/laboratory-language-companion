from fastapi import Depends

from deutschos_api.core.config import Settings, get_settings
from deutschos_api.providers.base import ModelProvider
from deutschos_api.providers.ollama import OllamaProvider


def get_model_provider(settings: Settings = Depends(get_settings)) -> ModelProvider:
    """The only composition point that knows which concrete provider is configured."""
    return OllamaProvider(settings.ollama_base_url)
