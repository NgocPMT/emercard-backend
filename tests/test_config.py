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
