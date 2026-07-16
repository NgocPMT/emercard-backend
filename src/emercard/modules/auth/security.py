"""Password hashing and signed session-token primitives."""

import secrets
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


def issue_private_profile_authorization(
    user_id: str,
    settings: Settings,
    *,
    lifetime_seconds: int,
) -> tuple[str, datetime]:
    """Issue a short-lived bearer token for one private-profile write purpose.

    The token is intentionally replayable until expiry because this service has
    no durable nonce store. It is scoped to the authenticated user and purpose;
    callers must use HTTPS and avoid logging it.
    """

    now = datetime.now(UTC).replace(microsecond=0)
    expires_at = now + timedelta(seconds=lifetime_seconds)
    claims = {
        "sub": user_id,
        "purpose": "private_profile_write",
        "jti": secrets.token_urlsafe(16),
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(  # pyright: ignore[reportUnknownMemberType]
        claims,
        _auth_secret(settings),
        algorithm=settings.auth_algorithm,
    )
    return token, expires_at


def validate_private_profile_authorization(
    token: str,
    user_id: str,
    settings: Settings,
) -> datetime:
    """Validate the user- and purpose-bound private-profile bearer token."""

    try:
        claims: Any = jwt.decode(  # pyright: ignore[reportUnknownMemberType]
            token,
            _auth_secret(settings),
            algorithms=[settings.auth_algorithm],
            leeway=settings.auth_clock_skew_seconds,
            options={"require": ["sub", "purpose", "jti", "iat", "exp"], "verify_aud": False},
        )
    except jwt.InvalidTokenError as error:
        raise ValueError("invalid private profile authorization") from error

    if not isinstance(claims, dict):
        raise ValueError("invalid private profile authorization claims")
    typed_claims = cast(dict[str, Any], claims)
    if (
        typed_claims.get("sub") != user_id
        or typed_claims.get("purpose") != "private_profile_write"
        or not isinstance(typed_claims.get("jti"), str)
    ):
        raise ValueError("invalid private profile authorization claims")
    expires_at = typed_claims.get("exp")
    if not isinstance(expires_at, (int, float)):
        raise ValueError("invalid private profile authorization expiry")
    return datetime.fromtimestamp(expires_at, tz=UTC)


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
