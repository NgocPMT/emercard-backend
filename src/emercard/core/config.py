"""Typed application settings."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EnvironmentName = Literal["local", "test", "demo", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """All environment-dependent application values in one place."""

    model_config = SettingsConfigDict(
        env_prefix="EMERCARD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "EmerCard API"
    environment: EnvironmentName = "local"
    debug: bool = False
    api_prefix: str = "/api/v1"
    host: str = "127.0.0.1"
    port: Annotated[int, Field(ge=1, le=65535)] = 8000
    log_level: LogLevel = "INFO"
    build_revision: str | None = None

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "emercard_local"
    mongodb_server_selection_timeout_ms: Annotated[int, Field(ge=100, le=120_000)] = 1_500
    mongodb_min_pool_size: Annotated[int, Field(ge=0, le=1_000)] = 0
    mongodb_max_pool_size: Annotated[int, Field(ge=1, le=10_000)] = 20
    mongodb_tls_required: bool = False

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:4321"])
    cors_allow_credentials: bool = False
    frontend_base_url: str | None = None

    # Reserved for the authentication stage; no authentication is implemented here.
    auth_secret: SecretStr | None = None

    @field_validator("api_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or not normalized.startswith("/"):
            raise ValueError("api_prefix must start with '/'")
        return normalized.rstrip("/") or "/"

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("mongodb_uri")
    @classmethod
    def validate_mongodb_uri(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError("mongodb_uri must use mongodb:// or mongodb+srv://")
        return normalized

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                return value
            return [origin.strip() for origin in stripped.split(",") if origin.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, value: list[str]) -> list[str]:
        origins = [origin.strip().rstrip("/") for origin in value if origin.strip()]
        if any(origin == "*" for origin in origins):
            raise ValueError("cors_origins must not contain '*'")
        return origins

    @model_validator(mode="after")
    def validate_deployment_values(self) -> Settings:
        if self.mongodb_min_pool_size > self.mongodb_max_pool_size:
            raise ValueError("mongodb_min_pool_size cannot exceed mongodb_max_pool_size")
        if self.cors_allow_credentials and "*" in self.cors_origins:
            raise ValueError("wildcard CORS origins cannot be used with credentials")
        if self.environment in {"demo", "staging", "production"}:
            if self.debug:
                raise ValueError("debug must be false outside local and test environments")
            if self.auth_secret is None or len(self.auth_secret.get_secret_value()) < 32:
                raise ValueError("auth_secret must contain at least 32 characters for deployments")
        if self.environment == "production" and not self.mongodb_tls_required:
            raise ValueError("mongodb_tls_required must be true in production")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
