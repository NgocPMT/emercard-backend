"""MongoDB document and command result models for public profile links."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from emercard.core.types import ObjectIdValue, UtcDateTime
from emercard.modules.cards.identity import validate_token_hash


class PublicAccessLinkStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class PublicLinkModel(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class PublicAccessLinkDocument(PublicLinkModel):
    """MongoDB document linking one medical profile to one public bearer token."""

    id: ObjectIdValue = Field(alias="_id")
    profile_id: ObjectIdValue
    token_hash: str
    status: PublicAccessLinkStatus
    created_at: UtcDateTime
    updated_at: UtcDateTime
    disabled_at: UtcDateTime | None = None

    @field_validator("token_hash")
    @classmethod
    def validate_token_hash(cls, value: str) -> str:
        return validate_token_hash(value)

    @model_validator(mode="after")
    def validate_timestamp_invariants(self) -> PublicAccessLinkDocument:
        if self.status is PublicAccessLinkStatus.ACTIVE and self.disabled_at is not None:
            raise ValueError("active public links must not have a disabled_at timestamp")
        if self.status is PublicAccessLinkStatus.DISABLED and self.disabled_at is None:
            raise ValueError("disabled public links must have a disabled_at timestamp")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
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


PublicAccessLinkDocuments = Annotated[list[PublicAccessLinkDocument], Field(default_factory=list)]
