"""MongoDB documents and owner-safe output models for public-link access history."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from emercard.core.types import ObjectIdValue, UtcDateTime


class LinkAccessHistoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class LinkAccessEventDocument(LinkAccessHistoryModel):
    """Append-only event for a successful active card-link lookup."""

    id: ObjectIdValue = Field(alias="_id")
    card_id: ObjectIdValue
    public_access_link_id: ObjectIdValue
    accessed_at: UtcDateTime
    expires_at: UtcDateTime

    @model_validator(mode="after")
    def validate_expiry(self) -> LinkAccessEventDocument:
        if self.expires_at < self.accessed_at:
            raise ValueError("expires_at cannot be earlier than accessed_at")
        return self


class LinkAccessEventOutput(LinkAccessHistoryModel):
    """Minimal event projection exposed to the authenticated profile owner."""

    id: str
    accessed_at: UtcDateTime


class LinkAccessHistoryOutput(LinkAccessHistoryModel):
    items: list[LinkAccessEventOutput]
    next_cursor: str | None = None


def to_link_access_event_output(event: LinkAccessEventDocument) -> LinkAccessEventOutput:
    return LinkAccessEventOutput(id=str(event.id), accessed_at=event.accessed_at)
