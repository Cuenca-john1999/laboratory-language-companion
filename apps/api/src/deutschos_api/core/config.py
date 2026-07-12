from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[5]


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/deutschos.sqlite3"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""
    timezone: str = "Europe/Berlin"
    cors_origins: list[str] = ["http://127.0.0.1:3000", "http://localhost:3000"]

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="DEUTSCHOS_",
        # The shared root .env also contains NEXT_PUBLIC_* values for the web app.
        extra="ignore",
    )

    @field_validator("database_url", mode="after")
    @classmethod
    def anchor_relative_sqlite_url(cls, value: str) -> str:
        """Resolve SQLite files from the repository, never from the process CWD."""
        url = make_url(value)
        if url.get_backend_name() != "sqlite":
            raise ValueError("Milestone 0 only permits a local SQLite database")
        if not url.database or url.database == ":memory:":
            return value
        database = Path(url.database).expanduser()
        if not database.is_absolute():
            database = (PROJECT_ROOT / database).resolve()
        return url.set(database=str(database)).render_as_string(hide_password=False)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str) and not value.startswith("["):
            return [origin.strip() for origin in value.split(",")]
        return value

    @field_validator("ollama_base_url")
    @classmethod
    def require_local_ollama(cls, value: str) -> str:
        cls._require_loopback_url(value, "ollama_base_url")
        return value.rstrip("/")

    @field_validator("cors_origins")
    @classmethod
    def require_local_cors_origins(cls, values: list[str]) -> list[str]:
        for value in values:
            cls._require_loopback_url(value, "cors_origins")
        return values

    @field_validator("timezone")
    @classmethod
    def require_valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @staticmethod
    def _require_loopback_url(value: str, field_name: str) -> None:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"{field_name} must be an HTTP loopback URL")
        if parsed.hostname == "localhost":
            return
        try:
            if ip_address(parsed.hostname).is_loopback:
                return
        except ValueError:
            pass
        raise ValueError(f"{field_name} must point to localhost/loopback")

    def ensure_data_directory(self) -> None:
        database_path = self.database_path
        if database_path is not None:
            database_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def database_path(self) -> Path | None:
        url = make_url(self.database_url)
        if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
            return None
        return Path(url.database)


@lru_cache
def get_settings() -> Settings:
    return Settings()
