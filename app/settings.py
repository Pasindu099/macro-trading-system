"""Application settings loaded from environment variables.

This is the single source of truth for all configuration. Every other module
should import settings from here — never read os.environ directly elsewhere.

Required environment variables (set these in .env at project root):
    EODHD_API_KEY       Your EODHD API subscription key
    DATABASE_URL        Postgres connection string
                        Format: postgresql+asyncpg://user:pass@host:port/dbname
    LOG_LEVEL           Optional, defaults to INFO (DEBUG, INFO, WARNING, ERROR)
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration.

    Values are loaded from environment variables, or from a .env file in the
    project root if present. Environment variables override .env values.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",  # ignore extra env vars we don't care about
    )

    # EODHD API
    eodhd_api_key: str = Field(
        ...,
        alias="EODHD_API_KEY",
        description="EODHD subscription API key",
    )
    eodhd_base_url: str = Field(
        default="https://eodhd.com/api",
        alias="EODHD_BASE_URL",
        description="EODHD API base URL",
    )

    # Database
    database_url: str = Field(
        ...,
        alias="DATABASE_URL",
        description="Async Postgres connection string",
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        alias="LOG_LEVEL",
        description="Python logging level",
    )

    # HTTP client behavior
    http_timeout_seconds: float = Field(
        default=30.0,
        description="Default HTTP request timeout",
    )
    http_max_retries: int = Field(
        default=3,
        description="Max retries for failed HTTP requests",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached settings instance.

    Uses lru_cache so settings are loaded once per process. This is the
    function you should import and call everywhere else.

    Example:
        from app.settings import get_settings
        settings = get_settings()
        api_key = settings.eodhd_api_key
    """
    return Settings()