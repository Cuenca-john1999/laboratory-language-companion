from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, AliasGenerator, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from llc_api.core.model_roles import (
    DEEP_TEACHER_MODEL,
    EMBEDDING_MODEL,
    TEACHER_MODEL,
)

PROJECT_ROOT = Path(__file__).resolve().parents[5]


def settings_alias(field_name: str) -> AliasChoices:
    """Prefer LLC_* configuration while accepting the legacy prefix."""
    suffix = field_name.upper()
    return AliasChoices(f"LLC_{suffix}", f"DEUTSCHOS_{suffix}")


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/deutschos.sqlite3"
    lm_studio_base_url: str = "http://127.0.0.1:1234/v1"
    lm_studio_model: str = TEACHER_MODEL
    lm_studio_deep_model: str = DEEP_TEACHER_MODEL
    lm_studio_vision_model: str = TEACHER_MODEL
    lm_studio_embedding_model: str = EMBEDDING_MODEL
    lm_studio_timeout_seconds: float = 180
    lm_studio_context_length: int = 8192
    lm_studio_temperature: float = 0.2
    lm_studio_max_tokens: int = 2048
    timezone: str = "Europe/Berlin"
    cors_origins: list[str] = ["http://127.0.0.1:3000", "http://localhost:3000"]
    educational_materials_dir: Path = PROJECT_ROOT / "material educativo"
    educational_library_runtime_dir: Path = PROJECT_ROOT / "var" / "educational-library"
    educational_library_scan_on_startup: bool = False
    educational_library_scan_interval_seconds: int = 900
    educational_library_embedding_model: str = EMBEDDING_MODEL
    educational_library_planner_model: str = TEACHER_MODEL
    educational_library_teacher_model: str = TEACHER_MODEL
    educational_library_fallback_model: str = DEEP_TEACHER_MODEL
    educational_library_vision_model: str = TEACHER_MODEL
    educational_library_repair_model: str = TEACHER_MODEL
    educational_library_planner_timeout_seconds: float = 45
    educational_library_teacher_timeout_seconds: float = 180
    educational_library_embedding_timeout_seconds: float = 120
    educational_library_cache_ttl_seconds: int = 86_400
    educational_library_max_extract_bytes: int = 512 * 1024 * 1024
    educational_library_max_text_characters: int = 12_000_000
    educational_library_max_pdf_pages: int = 2_000
    educational_library_teacher_max_search_queries: int = 6
    educational_library_teacher_max_sources: int = 5
    educational_library_teacher_max_chunks: int = 10
    educational_library_teacher_max_chunks_per_source: int = 2
    educational_library_teacher_max_context_characters: int = 18_000

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        alias_generator=AliasGenerator(validation_alias=settings_alias),
        populate_by_name=True,
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

    @field_validator("lm_studio_base_url")
    @classmethod
    def require_local_lm_studio(cls, value: str) -> str:
        cls._require_loopback_url(value, "lm_studio_base_url")
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

    @field_validator("educational_materials_dir", "educational_library_runtime_dir")
    @classmethod
    def anchor_library_paths(cls, value: Path) -> Path:
        path = value.expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve(strict=False)

    @field_validator("educational_library_scan_interval_seconds")
    @classmethod
    def safe_scan_interval(cls, value: int) -> int:
        if value < 60 or value > 86_400:
            raise ValueError("library scan interval must be between 60 and 86400 seconds")
        return value

    @field_validator(
        "educational_library_max_extract_bytes",
        "educational_library_max_text_characters",
        "educational_library_max_pdf_pages",
        "educational_library_teacher_max_search_queries",
        "educational_library_teacher_max_sources",
        "educational_library_teacher_max_chunks",
        "educational_library_teacher_max_chunks_per_source",
        "educational_library_teacher_max_context_characters",
        "educational_library_cache_ttl_seconds",
    )
    @classmethod
    def positive_library_limit(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("educational library limits must be positive")
        return value

    @field_validator(
        "educational_library_planner_timeout_seconds",
        "educational_library_teacher_timeout_seconds",
        "educational_library_embedding_timeout_seconds",
        "lm_studio_timeout_seconds",
    )
    @classmethod
    def safe_model_timeout(cls, value: float) -> float:
        if value < 1 or value > 600:
            raise ValueError("library model timeouts must be between 1 and 600 seconds")
        return value

    @field_validator("lm_studio_context_length", "lm_studio_max_tokens")
    @classmethod
    def positive_model_limit(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("LM Studio token limits must be positive")
        return value

    @field_validator("lm_studio_temperature")
    @classmethod
    def valid_temperature(cls, value: float) -> float:
        if value < 0 or value > 2:
            raise ValueError("LM Studio temperature must be between 0 and 2")
        return value

    @model_validator(mode="after")
    def separate_library_roots(self):
        materials = self.educational_materials_dir
        runtime = self.educational_library_runtime_dir
        if materials == runtime or materials in runtime.parents or runtime in materials.parents:
            raise ValueError("educational materials and runtime directories must be separate")
        return self

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

    def ensure_educational_library_directory(self) -> None:
        self.educational_library_runtime_dir.mkdir(parents=True, exist_ok=True)

    @property
    def educational_library_database_path(self) -> Path:
        return self.educational_library_runtime_dir / "library.sqlite3"

    @property
    def database_path(self) -> Path | None:
        url = make_url(self.database_url)
        if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
            return None
        return Path(url.database)


@lru_cache
def get_settings() -> Settings:
    return Settings()
