from llc_api.core.config import Settings
from llc_api.main import app


def test_api_uses_llc_product_identity():
    assert app.title == "LLC API"
    assert "Laboratory Language Companion" in app.description


def test_llc_environment_is_preferred(monkeypatch):
    monkeypatch.setenv("LLC_TIMEZONE", "Europe/Berlin")
    monkeypatch.setenv("DEUTSCHOS_TIMEZONE", "UTC")

    settings = Settings(_env_file=None)

    assert settings.timezone == "Europe/Berlin"


def test_legacy_environment_remains_a_fallback(monkeypatch):
    monkeypatch.delenv("LLC_TIMEZONE", raising=False)
    monkeypatch.setenv("DEUTSCHOS_TIMEZONE", "UTC")

    settings = Settings(_env_file=None)

    assert settings.timezone == "UTC"
