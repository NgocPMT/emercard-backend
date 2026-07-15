"""Strict admin card request and safe response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from emercard.core.types import UtcDateTime
from emercard.modules.card_link_assignments.models import (
    CardLinkAssignmentDocument,
    CardLinkAssignmentStatus,
)
from emercard.modules.cards.models import CardDocument, CardStatus
from emercard.modules.public_links.models import (
    PublicAccessLinkDocument,
    PublicAccessLinkStatus,
    PublicLinkPurpose,
)

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


class LinkCreateInput(CardSchema):
    purpose: PublicLinkPurpose
    label: str | None = Field(default=None, max_length=128)


class LinkAttachInput(CardSchema):
    public_access_link_id: str = Field(min_length=1, max_length=64)


class LinkDetachInput(CardSchema):
    reason: str | None = Field(default=None, max_length=256)


class SafeUserOutput(CardSchema):
    id: str
    email: str
    role: Literal["user", "admin"]
    created_at: UtcDateTime
    updated_at: UtcDateTime


class CardOwnerOutput(CardSchema):
    id: str
    email: str


class PublicLinkSummaryOutput(CardSchema):
    id: str
    purpose: PublicLinkPurpose
    label: str | None = None
    status: PublicAccessLinkStatus
    created_at: UtcDateTime
    updated_at: UtcDateTime
    activated_at: UtcDateTime | None = None
    disabled_at: UtcDateTime | None = None
    revoked_at: UtcDateTime | None = None
    expires_at: UtcDateTime | None = None
    expired_at: UtcDateTime | None = None


class CardAssignmentSummaryOutput(CardSchema):
    id: str
    status: CardLinkAssignmentStatus
    attached_at: UtcDateTime
    updated_at: UtcDateTime
    detached_at: UtcDateTime | None = None
    detach_reason: str | None = None


class AdminCardOutput(CardSchema):
    id: str
    serial: str
    status: CardStatus
    is_current: bool
    encoding_state: EncodingState
    owner: CardOwnerOutput | None = None
    link: PublicLinkSummaryOutput | None = None
    assignment: CardAssignmentSummaryOutput | None = None
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


class PublicLinkListOutput(CardSchema):
    items: list[PublicLinkSummaryOutput]


class LinkProvisioningOutput(CardSchema):
    link: PublicLinkSummaryOutput
    public_token: str
    public_url: str


class UserCardOutput(CardSchema):
    """Allowlisted card representation for the authenticated owner."""

    id: str
    serial: str
    status: CardStatus
    is_current: bool
    issued_at: UtcDateTime
    activated_at: UtcDateTime | None
    disabled_at: UtcDateTime | None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    can_activate: bool
    can_disable: bool
    can_revoke: bool
    can_report_lost: bool
    blocking_reason: str | None = None
    link_status: PublicAccessLinkStatus | None = None
    link_purpose: PublicLinkPurpose | None = None
    link_label: str | None = None


class UserCardListOutput(CardSchema):
    cards: list[UserCardOutput]


class CustodyEventOutput(CardSchema):
    id: str
    card_id: str
    event_type: Literal["assigned", "reassigned", "unassigned", "issued", "voided"]
    previous_owner_id: str | None = None
    new_owner_id: str | None = None
    performed_by_admin_id: str
    reason: ReassignmentReason | None = None
    created_at: UtcDateTime


def encoding_state(
    card: CardDocument,
    *,
    link: PublicAccessLinkDocument | None = None,
) -> EncodingState:
    if card.encoding_verified_at is not None:
        return "verified"
    if card.provisioned_at is not None or link is not None:
        return "link_generated"
    return "not_provisioned"


def to_user_card(
    card: CardDocument,
    *,
    link: PublicAccessLinkDocument | None = None,
) -> UserCardOutput:
    """Map an issued operational card without exposing internal card metadata."""

    issued_at = card.issued_at
    if issued_at is None:
        raise ValueError("user card output requires an issued card")

    terminal_statuses = {CardStatus.LOST, CardStatus.REPLACED, CardStatus.VOID}
    link_status = link.status if link is not None else None
    can_activate = (
        card.is_current
        and card.status not in terminal_statuses
        and card.encoding_verified_at is not None
        and link_status in {PublicAccessLinkStatus.PENDING, PublicAccessLinkStatus.DISABLED}
    )
    can_disable = (
        card.is_current
        and card.status not in terminal_statuses
        and card.encoding_verified_at is not None
        and link_status is PublicAccessLinkStatus.ACTIVE
    )
    can_revoke = (
        card.is_current
        and card.status not in terminal_statuses
        and link_status
        in {
            PublicAccessLinkStatus.PENDING,
            PublicAccessLinkStatus.ACTIVE,
            PublicAccessLinkStatus.DISABLED,
        }
    )
    can_report_lost = card.is_current and card.status in {
        CardStatus.ASSIGNED,
        CardStatus.ACTIVE,
        CardStatus.DISABLED,
    }
    blocking_reason: str | None = None
    if card.status in terminal_statuses:
        blocking_reason = "card_terminal"
    elif card.encoding_verified_at is None:
        blocking_reason = "card_encoding_not_verified"
    elif link is None:
        blocking_reason = "link_not_assigned"
    elif link.status is PublicAccessLinkStatus.REVOKED:
        blocking_reason = "link_revoked"
    elif link.status is PublicAccessLinkStatus.EXPIRED:
        blocking_reason = "link_expired"
    elif not card.is_current:
        blocking_reason = "card_not_current"

    return UserCardOutput(
        id=str(card.id),
        serial=card.serial,
        status=card.status,
        is_current=card.is_current,
        issued_at=issued_at,
        activated_at=card.activated_at,
        disabled_at=card.disabled_at,
        created_at=card.created_at,
        updated_at=card.updated_at,
        can_activate=can_activate,
        can_disable=can_disable,
        can_revoke=can_revoke,
        can_report_lost=can_report_lost,
        blocking_reason=blocking_reason,
        link_status=link_status,
        link_purpose=link.purpose if link is not None else None,
        link_label=link.label if link is not None else None,
    )


def to_admin_card(
    card: CardDocument,
    *,
    owner: CardOwnerOutput | None = None,
    link: PublicAccessLinkDocument | None = None,
    assignment: CardLinkAssignmentDocument | None = None,
) -> AdminCardOutput:
    return AdminCardOutput(
        id=str(card.id),
        serial=card.serial,
        status=card.status,
        is_current=card.is_current,
        encoding_state=encoding_state(card, link=link),
        owner=owner,
        link=to_public_link_summary(link) if link is not None else None,
        assignment=to_card_assignment_summary(assignment) if assignment is not None else None,
        provisioned_at=card.provisioned_at,
        encoding_verified_at=card.encoding_verified_at,
        assigned_at=card.assigned_at,
        issued_at=card.issued_at,
        created_at=card.created_at,
        updated_at=card.updated_at,
    )


def to_public_link_summary(link: PublicAccessLinkDocument) -> PublicLinkSummaryOutput:
    return PublicLinkSummaryOutput(
        id=str(link.id),
        purpose=link.purpose,
        label=link.label,
        status=link.status,
        created_at=link.created_at,
        updated_at=link.updated_at,
        activated_at=link.activated_at,
        disabled_at=link.disabled_at,
        revoked_at=link.revoked_at,
        expires_at=link.expires_at,
        expired_at=link.expired_at,
    )


def to_card_assignment_summary(
    assignment: CardLinkAssignmentDocument,
) -> CardAssignmentSummaryOutput:
    return CardAssignmentSummaryOutput(
        id=str(assignment.id),
        status=assignment.status,
        attached_at=assignment.attached_at,
        updated_at=assignment.updated_at,
        detached_at=assignment.detached_at,
        detach_reason=assignment.detach_reason,
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
