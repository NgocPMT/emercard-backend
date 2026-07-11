"""Typed application settings."""

from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EnvironmentName = Literal["local", "test", "demo", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
IndexInitializationMode = Literal["disabled", "startup", "command"]
AuthAlgorithm = Literal["HS256", "HS384", "HS512"]
CookieSameSite = Literal["lax", "strict", "none"]


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
    mongodb_users_collection: str = "users"
    mongodb_profiles_collection: str = "medical_profiles"
    mongodb_test_database_prefix: str = "emercard_test"
    mongodb_index_initialization_mode: IndexInitializationMode = "disabled"

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:4321"])
    cors_allow_credentials: bool = True
    frontend_base_url: str | None = None

    public_link_token_bytes: Annotated[int, Field(ge=16, le=128)] = 32
    public_link_route_prefix: str = "/p"
    public_link_explicit_publication: bool = True

    # Reserved for the authentication stage; no authentication is implemented here.
    auth_secret: SecretStr | None = None
    auth_algorithm: AuthAlgorithm = "HS256"
    auth_access_token_lifetime_seconds: Annotated[int, Field(ge=60, le=86_400)] = 900
    auth_cookie_name: str = "emercard_session"
    auth_cookie_secure: bool = False
    auth_cookie_http_only: bool = True
    auth_cookie_same_site: CookieSameSite = "lax"
    auth_cookie_path: str = "/"
    auth_cookie_domain: str | None = None
    auth_clock_skew_seconds: Annotated[int, Field(ge=0, le=300)] = 30

    display_name_max_length: Annotated[int, Field(ge=1, le=500)] = 120
    emergency_note_max_length: Annotated[int, Field(ge=1, le=2_000)] = 500
    medical_item_max_length: Annotated[int, Field(ge=1, le=500)] = 120
    medical_list_max_items: Annotated[int, Field(ge=1, le=100)] = 10
    emergency_contacts_max_count: Annotated[int, Field(ge=1, le=20)] = 5
    contact_name_max_length: Annotated[int, Field(ge=1, le=500)] = 100
    contact_relationship_max_length: Annotated[int, Field(ge=1, le=500)] = 80
    contact_phone_max_length: Annotated[int, Field(ge=1, le=100)] = 32
    birth_year_min: Annotated[int, Field(ge=1800, le=2100)] = 1900
    birth_year_max: Annotated[int, Field(ge=1800, le=2100)] = 2026

    @field_validator("api_prefix", "public_link_route_prefix", "auth_cookie_path")
    @classmethod
    def validate_path_prefix(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or not normalized.startswith("/"):
            raise ValueError("path settings must start with '/'")
        return normalized.rstrip("/") or "/"

    @field_validator(
        "mongodb_users_collection",
        "mongodb_profiles_collection",
        "mongodb_test_database_prefix",
        "auth_cookie_name",
    )
    @classmethod
    def validate_identifier_settings(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(character in normalized for character in " $."):
            raise ValueError(
                "identifier settings must be non-empty and contain no spaces, '$', or '.'"
            )
        return normalized

    @field_validator("frontend_base_url")
    @classmethod
    def validate_frontend_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("frontend_base_url must be an absolute http(s) URL")
        return normalized

    @field_validator("auth_cookie_domain")
    @classmethod
    def reject_auth_cookie_domain(cls, value: str | None) -> None:
        if value is not None:
            raise ValueError("auth_cookie_domain must remain unset for host-only cookies")
        return None

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
        if self.birth_year_min > self.birth_year_max:
            raise ValueError("birth_year_min cannot exceed birth_year_max")
        if self.cors_allow_credentials and "*" in self.cors_origins:
            raise ValueError("wildcard CORS origins cannot be used with credentials")
        if self.environment in {"demo", "staging", "production"}:
            if self.debug:
                raise ValueError("debug must be false outside local and test environments")
            if self.auth_secret is None or len(self.auth_secret.get_secret_value()) < 32:
                raise ValueError("auth_secret must contain at least 32 characters for deployments")
        if self.environment == "production" and not self.mongodb_tls_required:
            raise ValueError("mongodb_tls_required must be true in production")
        if self.environment in {"demo", "staging", "production"}:
            if not self.auth_cookie_secure:
                raise ValueError("auth_cookie_secure must be true for deployments")
            if not self.cors_allow_credentials:
                raise ValueError("cors_allow_credentials must be true for deployments")
            if self.frontend_base_url is None:
                raise ValueError("frontend_base_url is required for deployments")
            if not self.frontend_base_url.startswith("https://"):
                raise ValueError("frontend_base_url must use https for deployments")
            if self.frontend_base_url not in self.cors_origins:
                raise ValueError("frontend_base_url must be included in cors_origins")
        if self.auth_cookie_same_site == "none" and not self.auth_cookie_secure:
            raise ValueError("auth_cookie_secure must be true when auth_cookie_same_site is 'none'")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
