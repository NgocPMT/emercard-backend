import pytest
from pydantic import SecretStr, ValidationError

from emercard.core.config import Settings


def test_settings_accept_comma_separated_cors_origins() -> None:
    settings = Settings(
        environment="test",
        cors_origins="http://localhost:4321, https://demo.example.com",  # type: ignore[arg-type]
    )

    assert settings.cors_origins == ["http://localhost:4321", "https://demo.example.com"]


def test_deployment_settings_require_non_debug_and_future_auth_secret() -> None:
    with pytest.raises(ValidationError, match="auth_secret"):
        Settings(environment="demo", debug=False)

    with pytest.raises(ValidationError, match="debug"):
        Settings(environment="demo", debug=True, auth_secret=SecretStr("x" * 32))


def test_production_requires_tls() -> None:
    with pytest.raises(ValidationError, match="mongodb_tls_required"):
        Settings(
            environment="production",
            debug=False,
            auth_secret=SecretStr("x" * 32),
            mongodb_tls_required=False,
        )


def test_wildcard_cors_origin_is_rejected() -> None:
    with pytest.raises(ValidationError, match="cors_origins"):
        Settings(environment="test", cors_origins=["*"])


def test_persistence_and_auth_ready_settings_are_validated() -> None:
    settings = Settings(
        environment="demo",
        debug=False,
        auth_secret=SecretStr("x" * 32),
        auth_cookie_secure=True,
        frontend_base_url="https://demo.example.com",
        mongodb_index_initialization_mode="startup",
        public_link_route_prefix="/public",
    )

    assert settings.mongodb_users_collection == "users"
    assert settings.mongodb_profiles_collection == "medical_profiles"
    assert settings.public_link_token_bytes == 32
    assert settings.auth_cookie_http_only is True
    assert settings.public_link_route_prefix == "/public"


def test_unsafe_cookie_and_public_url_settings_are_rejected() -> None:
    with pytest.raises(ValidationError, match="auth_cookie_secure"):
        Settings(
            environment="test",
            auth_cookie_same_site="none",
            auth_cookie_secure=False,
        )

    with pytest.raises(ValidationError, match="frontend_base_url"):
        Settings(environment="test", frontend_base_url="not-a-url")
