"""Application settings loaded from environment variables.

This is the single source of truth for all configuration. Every other module
should import settings from here — never read os.environ directly elsewhere.

Required environment variables (set these in .env at project root):
    EODHD_API_KEY       Your EODHD API subscription key
    DATABASE_URL        Postgres connection string
                        Format: postgresql+asyncpg://user:pass@host:port/dbname
    LOG_LEVEL           Optional, defaults to INFO (DEBUG, INFO, WARNING, ERROR)
"""

import secrets
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

    # Scheduler
    enable_scheduler: bool = Field(
        default=True,
        alias="ENABLE_SCHEDULER",
        description="Run background ingestion jobs. Disable for tests/scripts.",
    )

    # Authentication
    auth_enabled: bool = Field(
        default=True,
        alias="AUTH_ENABLED",
        description="Require dashboard users to sign in.",
    )
    auth_secret_key: str = Field(
        default_factory=lambda: secrets.token_urlsafe(48),
        alias="AUTH_SECRET_KEY",
        description="Secret used to sign dashboard session cookies.",
    )
    auth_session_cookie: str = Field(
        default="macro_dashboard_session",
        alias="AUTH_SESSION_COOKIE",
        description="Cookie name for dashboard sessions.",
    )
    auth_session_max_age_seconds: int = Field(
        default=60 * 60 * 12,
        alias="AUTH_SESSION_MAX_AGE_SECONDS",
        description="Session lifetime in seconds.",
    )

    # Bank research ingestion
    google_drive_api_key: str | None = Field(
        default=None,
        alias="GOOGLE_DRIVE_API_KEY",
        description="Google Drive API key for reading daily public research folders",
    )
    openai_api_key: str | None = Field(
        default=None,
        alias="OPENAI_API_KEY",
        description="OpenAI API key for bank research summarization",
    )
    openai_model: str = Field(
        default="gpt-5.5-pro",
        alias="OPENAI_MODEL",
        description="Model used for bank research report analysis",
    )
    bank_research_drive_folder_url: str | None = Field(
        default=None,
        alias="BANK_RESEARCH_DRIVE_FOLDER_URL",
        description="Latest Google Drive folder URL containing bank research reports",
    )
    bank_research_retention_days: int = Field(
        default=7,
        alias="BANK_RESEARCH_RETENTION_DAYS",
        description="Number of days to keep downloaded bank research files",
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
