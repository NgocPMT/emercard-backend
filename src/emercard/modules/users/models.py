"""User persistence, input, and authenticated output models."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from emercard.core.types import ObjectIdValue, UtcDateTime

UserRole = Literal["user", "admin"]

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class UserModel(BaseModel):
    """Base model with strict extra-field handling."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


def canonicalize_email(value: str) -> str:
    """Apply the single Phase 1 email canonicalization strategy."""

    normalized = value.strip().lower()
    if len(normalized) > 254 or not _EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("email must be a valid email address")
    return normalized


PasswordInput = Annotated[str, Field(min_length=8, max_length=128)]


class UserDocument(UserModel):
    """MongoDB user document; never use this as an HTTP response."""

    id: ObjectIdValue = Field(alias="_id")
    email: str = Field(min_length=3, max_length=254)
    password_hash: str = Field(min_length=1, max_length=512)
    role: UserRole = "user"
    created_at: UtcDateTime
    updated_at: UtcDateTime

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return canonicalize_email(value)


class UserRegistrationInput(UserModel):
    """Registration input kept separate from the persistence document."""

    email: str = Field(min_length=3, max_length=254)
    password: PasswordInput

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return canonicalize_email(value)


class UserLoginInput(UserModel):
    email: str = Field(min_length=3, max_length=254)
    password: PasswordInput

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return canonicalize_email(value)


class CurrentUserOutput(UserModel):
    """Authenticated user response with no password material."""

    id: str
    email: str
    role: UserRole
    created_at: UtcDateTime
    updated_at: UtcDateTime
