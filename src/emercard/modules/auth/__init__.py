"""Authentication services and security primitives."""

from emercard.modules.auth.exceptions import (
    AuthenticationRequiredError,
    DuplicateEmailError,
    InvalidCredentialsError,
    InvalidSessionError,
    PrivateProfileAuthorizationError,
)
from emercard.modules.auth.service import AuthService

__all__ = [
    "AuthService",
    "AuthenticationRequiredError",
    "DuplicateEmailError",
    "InvalidCredentialsError",
    "InvalidSessionError",
    "PrivateProfileAuthorizationError",
]
