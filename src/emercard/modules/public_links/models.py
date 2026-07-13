"""MongoDB document and command result models for public profile links."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from emercard.core.types import ObjectIdValue, UtcDateTime
from emercard.modules.cards.identity import validate_token_hash
from emercard.modules.profiles.models import PublicProfileOutput


class PublicLinkPurpose(StrEnum):
    CARD = "card"
    STANDALONE = "standalone"


class PublicAccessLinkStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    DISABLED = "disabled"
    REVOKED = "revoked"
    EXPIRED = "expired"


class PublicLinkModel(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class PublicAccessLinkDocument(PublicLinkModel):
    """MongoDB document linking one medical profile to one public bearer token."""

    id: ObjectIdValue = Field(alias="_id")
    profile_id: ObjectIdValue
    purpose: PublicLinkPurpose = PublicLinkPurpose.STANDALONE
    label: str | None = None
    token_hash: str
    status: PublicAccessLinkStatus
    created_by: ObjectIdValue | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    activated_at: UtcDateTime | None = None
    disabled_at: UtcDateTime | None = None
    revoked_at: UtcDateTime | None = None
    expires_at: UtcDateTime | None = None
    expired_at: UtcDateTime | None = None

    @field_validator("token_hash")
    @classmethod
    def validate_token_hash(cls, value: str) -> str:
        return validate_token_hash(value)

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_timestamp_invariants(self) -> PublicAccessLinkDocument:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        if self.status is PublicAccessLinkStatus.PENDING:
            if self.activated_at is not None:
                raise ValueError("pending public links must not have an activated_at timestamp")
            if (
                self.disabled_at is not None
                or self.revoked_at is not None
                or self.expired_at is not None
            ):
                raise ValueError("pending public links must not have terminal timestamps")
        if self.status is PublicAccessLinkStatus.ACTIVE and (
            self.disabled_at is not None
            or self.revoked_at is not None
            or self.expired_at is not None
        ):
            raise ValueError("active public links must not have terminal timestamps")
        if self.status is PublicAccessLinkStatus.DISABLED and self.disabled_at is None:
            raise ValueError("disabled public links must have a disabled_at timestamp")
        if self.status is PublicAccessLinkStatus.REVOKED and self.revoked_at is None:
            raise ValueError("revoked public links must have a revoked_at timestamp")
        if self.status is PublicAccessLinkStatus.EXPIRED and self.expired_at is None:
            raise ValueError("expired public links must have an expired_at timestamp")
        if self.purpose is PublicLinkPurpose.CARD and self.label is None:
            # Card links may be labeled later, but the field is kept explicit for future UI use.
            pass
        return self


@dataclass(frozen=True)
class PublicProfileLinkResult:
    """Structured operator output for link lifecycle operations."""

    action: str
    status: str
    profile_id: str
    public_url: str | None = None
    raw_token: str | None = None
    disabled: bool = False
    link_id: str | None = None
    purpose: PublicLinkPurpose | None = None


@dataclass(frozen=True)
class PublicProfileLookupResult:
    """Anonymous lookup result with internal attribution metadata."""

    profile: PublicProfileOutput
    link_id: str
    purpose: PublicLinkPurpose
    assignment_id: str | None = None
    card_id: str | None = None

    def __getattr__(self, name: str) -> object:
        return getattr(self.profile, name)


PublicAccessLinkDocuments = list[PublicAccessLinkDocument]
