"""Strict admin card request and safe response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from emercard.core.types import UtcDateTime
from emercard.modules.cards.models import CardDocument, CardStatus

EncodingState = Literal["not_provisioned", "link_generated", "verified"]
ReassignmentReason = Literal["assignment_error", "recipient_changed", "internal_correction"]


class CardSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BlankCardCreateInput(CardSchema):
    pass


class ProvisioningOutput(CardSchema):
    public_token: str
    public_url: str


class ConfirmEncodingInput(CardSchema):
    public_url: str = Field(min_length=1, max_length=2048)


class AssignCardInput(CardSchema):
    user_id: str = Field(min_length=1, max_length=64)


class ReassignCardInput(CardSchema):
    new_owner_id: str = Field(min_length=1, max_length=64)
    reason: ReassignmentReason


class SafeUserOutput(CardSchema):
    id: str
    email: str
    role: Literal["user", "admin"]
    created_at: UtcDateTime
    updated_at: UtcDateTime


class CardOwnerOutput(CardSchema):
    id: str
    email: str


class AdminCardOutput(CardSchema):
    id: str
    serial: str
    status: CardStatus
    is_current: bool
    encoding_state: EncodingState
    token_revision: int
    owner: CardOwnerOutput | None = None
    provisioned_at: UtcDateTime | None = None
    encoding_verified_at: UtcDateTime | None = None
    assigned_at: UtcDateTime | None = None
    issued_at: UtcDateTime | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class CardProvisioningOutput(CardSchema):
    card: AdminCardOutput
    provisioning: ProvisioningOutput


class CardListOutput(CardSchema):
    items: list[AdminCardOutput]
    next_cursor: str | None = None


class CustodyEventOutput(CardSchema):
    id: str
    card_id: str
    event_type: Literal["assigned", "reassigned", "unassigned", "issued", "voided"]
    previous_owner_id: str | None = None
    new_owner_id: str | None = None
    performed_by_admin_id: str
    reason: ReassignmentReason | None = None
    created_at: UtcDateTime


def encoding_state(card: CardDocument) -> EncodingState:
    if card.token_hash is None:
        return "not_provisioned"
    if card.encoding_verified_at is None:
        return "link_generated"
    return "verified"


def to_admin_card(
    card: CardDocument,
    *,
    owner: CardOwnerOutput | None = None,
) -> AdminCardOutput:
    return AdminCardOutput(
        id=str(card.id),
        serial=card.serial,
        status=card.status,
        is_current=card.is_current,
        encoding_state=encoding_state(card),
        token_revision=card.token_revision,
        owner=owner,
        provisioned_at=card.provisioned_at,
        encoding_verified_at=card.encoding_verified_at,
        assigned_at=card.assigned_at,
        issued_at=card.issued_at,
        created_at=card.created_at,
        updated_at=card.updated_at,
    )


class CardListQuery(CardSchema):
    status: CardStatus | None = None
    owner_id: str | None = None
    serial: str | None = None
    is_current: bool | None = None
    encoding_state: EncodingState | None = None
    issued: bool | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=512)

    @field_validator("serial")
    @classmethod
    def trim_serial(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None
