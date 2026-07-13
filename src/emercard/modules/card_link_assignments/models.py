"""MongoDB document models for physical-card to public-link assignments."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from emercard.core.types import ObjectIdValue, UtcDateTime


class CardLinkAssignmentStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    DETACHED = "detached"


class CardLinkAssignmentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class CardLinkAssignmentDocument(CardLinkAssignmentModel):
    """MongoDB document recording the active and historical card-link relation."""

    id: ObjectIdValue = Field(alias="_id")
    card_id: ObjectIdValue
    public_access_link_id: ObjectIdValue
    status: CardLinkAssignmentStatus
    attached_at: UtcDateTime
    updated_at: UtcDateTime
    attached_by_admin_id: ObjectIdValue | None = None
    disabled_at: UtcDateTime | None = None
    disabled_by_admin_id: ObjectIdValue | None = None
    detached_at: UtcDateTime | None = None
    detached_by_admin_id: ObjectIdValue | None = None
    detach_reason: str | None = None

    @field_validator("detach_reason")
    @classmethod
    def validate_detach_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> CardLinkAssignmentDocument:
        if self.updated_at < self.attached_at:
            raise ValueError("updated_at cannot be earlier than attached_at")
        if self.status is CardLinkAssignmentStatus.ACTIVE:
            if self.disabled_at is not None or self.detached_at is not None:
                raise ValueError("active assignments must not have terminal timestamps")
        elif self.status is CardLinkAssignmentStatus.DISABLED:
            if self.disabled_at is None:
                raise ValueError("disabled assignments must have a disabled_at timestamp")
            if self.detached_at is not None:
                raise ValueError("disabled assignments must not have a detached_at timestamp")
        elif self.status is CardLinkAssignmentStatus.DETACHED and self.detached_at is None:
            raise ValueError("detached assignments must have a detached_at timestamp")
        if self.status is not CardLinkAssignmentStatus.ACTIVE and self.attached_by_admin_id is None:
            # Preserve importability for legacy backfill rows.
            pass
        if self.status is CardLinkAssignmentStatus.DETACHED and self.detach_reason is None:
            raise ValueError("detached assignments must include a detach reason")
        return self


@dataclass(frozen=True)
class CardLinkAssignmentResult:
    action: str
    status: str
    assignment_id: str
    card_id: str
    public_access_link_id: str
    detached_reason: str | None = None


CardLinkAssignmentDocuments = list[CardLinkAssignmentDocument]
