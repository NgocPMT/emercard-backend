"""Password hashing and signed session-token primitives."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from emercard.core.config import Settings

_password_hasher = PasswordHasher()
# A fixed valid hash keeps unknown-email login on the same verification path.
DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$/cVWEiJ31apJtJVYbDN5PA$"
    "0obFG8kspmIT1Bn7B53yO96dVf7EsSJP58yapK83LV4"
)


class AuthConfigurationError(RuntimeError):
    """Authentication cannot operate with the current application settings."""


def hash_password(password: str) -> str:
    """Hash a password using the maintained Argon2 library defaults."""

    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Return whether a password matches a stored Argon2 hash."""

    try:
        return _password_hasher.verify(password_hash, password)
    except InvalidHashError, VerificationError, VerifyMismatchError:
        return False


def issue_session_token(user_id: str, settings: Settings) -> str:
    """Issue a short-lived HMAC JWT containing only the required identity claims."""

    now = datetime.now(UTC)
    claims = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(seconds=settings.auth_access_token_lifetime_seconds),
    }
    return jwt.encode(  # pyright: ignore[reportUnknownMemberType]
        claims,
        _auth_secret(settings),
        algorithm=settings.auth_algorithm,
    )


def validate_session_token(token: str, settings: Settings) -> str:
    """Validate a session JWT and return its user subject."""

    try:
        claims: Any = jwt.decode(  # pyright: ignore[reportUnknownMemberType]
            token,
            _auth_secret(settings),
            algorithms=[settings.auth_algorithm],
            leeway=settings.auth_clock_skew_seconds,
            options={"require": ["sub", "iat", "exp"], "verify_aud": False},
        )
    except jwt.InvalidTokenError as error:
        raise ValueError("invalid session token") from error

    if not isinstance(claims, dict):
        raise ValueError("invalid session claims")

    typed_claims = cast(dict[str, Any], claims)
    subject = typed_claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise ValueError("invalid session subject")
    return subject


def _auth_secret(settings: Settings) -> str:
    if settings.auth_secret is None:
        raise AuthConfigurationError("auth_secret is required when authentication is enabled")
    return settings.auth_secret.get_secret_value()
