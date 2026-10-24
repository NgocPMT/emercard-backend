"""User persistence, input, and authenticated output models."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from emercard.core.types import ObjectIdValue, UtcDateTime

UserRole = Literal["user", "admin"]

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_PATTERN = re.compile(r"^\+?[0-9]{8,15}$")


class UserModel(BaseModel):
    """Base model with strict extra-field handling."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


def canonicalize_email(value: str) -> str:
    """Apply the single Phase 1 email canonicalization strategy."""

    normalized = value.strip().lower()
    if len(normalized) > 254 or not _EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("Email không hợp lệ")
    return normalized


def canonicalize_phone(value: str) -> str:
    """Normalize phone numbers by stripping formatting characters and validating digits."""

    cleaned = re.sub(r"[\s\-\.\(\)]", "", value.strip())
    if not _PHONE_PATTERN.fullmatch(cleaned):
        raise ValueError("Số điện thoại không hợp lệ")

    if cleaned.startswith("+84") and len(cleaned) == 12:
        cleaned = f"0{cleaned[3:]}"
    elif cleaned.startswith("84") and len(cleaned) == 11 and not value.strip().startswith("+"):
        cleaned = f"0{cleaned[2:]}"

    return cleaned


def canonicalize_login_identifier(value: str) -> str:
    """Normalize login identifier as either canonical email or canonical phone."""

    val = value.strip()
    if "@" in val:
        return canonicalize_email(val)
    return canonicalize_phone(val)


PasswordInput = Annotated[str, Field(min_length=8, max_length=128)]


class UserDocument(UserModel):
    """MongoDB user document; never use this as an HTTP response."""

    id: ObjectIdValue = Field(alias="_id")
    email: str = Field(min_length=3, max_length=254)
    password_hash: str = Field(min_length=1, max_length=512)
    role: UserRole = "user"
    phone: str | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    # Optional verification metadata keeps reads compatible with accounts created
    # by the email-verification flow while remaining absent from public outputs.
    email_verified_at: UtcDateTime | None = None
    email_verification_token_hash: str | None = None
    email_verification_token_expires_at: UtcDateTime | None = None
    email_verification_last_sent_at: UtcDateTime | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return canonicalize_email(value)

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return canonicalize_phone(stripped) if stripped else None


class UserRegistrationInput(UserModel):
    """Registration input kept separate from the persistence document."""

    email: str = Field(min_length=3, max_length=254)
    phone: str | None = Field(default=None, max_length=32)
    password: PasswordInput

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return canonicalize_email(value)

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return canonicalize_phone(stripped) if stripped else None


class UserLoginInput(UserModel):
    email: str = Field(min_length=3, max_length=254)
    password: PasswordInput

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return canonicalize_login_identifier(value)


class PrivateProfileAuthorizationInput(UserModel):
    """Current account password used only to authorize private-profile changes."""

    password: PasswordInput


class PrivateProfileAuthorizationOutput(UserModel):
    """Short-lived bearer authorization for an owner private-profile write."""

    authorization_token: str = Field(min_length=1, max_length=4096)
    expires_at: UtcDateTime
    purpose: Literal["private_profile_write"]


class CurrentUserOutput(UserModel):
    """Authenticated user response with no password material."""

    id: str
    email: str
    role: UserRole
    phone: str | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
