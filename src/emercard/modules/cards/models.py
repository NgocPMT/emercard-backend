"""Card persistence contracts and one-time provisioning result."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from emercard.core.types import ObjectIdValue, UtcDateTime
from emercard.modules.cards.identity import normalize_serial, validate_token_hash


class CardStatus(StrEnum):
    """Persisted physical-card lifecycle states."""

    UNASSIGNED = "unassigned"
    ASSIGNED = "assigned"
    ACTIVE = "active"
    DISABLED = "disabled"
    LOST = "lost"
    REPLACED = "replaced"


class CardModel(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class CardDocument(CardModel):
    """MongoDB card document; raw public tokens are intentionally absent."""

    id: ObjectIdValue = Field(alias="_id")
    serial: str = Field(min_length=20, max_length=24)
    owner_id: ObjectIdValue | None = None
    token_hash: str = Field(min_length=67, max_length=67)
    status: CardStatus
    is_current: bool
    assigned_at: UtcDateTime | None = None
    activated_at: UtcDateTime | None = None
    disabled_at: UtcDateTime | None = None
    lost_at: UtcDateTime | None = None
    replaced_at: UtcDateTime | None = None
    replaces_card_id: ObjectIdValue | None = None
    replacement_card_id: ObjectIdValue | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime

    @field_validator("serial")
    @classmethod
    def validate_serial(cls, value: str) -> str:
        return normalize_serial(value)

    @field_validator("token_hash")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        return validate_token_hash(value)

    def __repr__(self) -> str:
        """Keep the internal token hash out of logs and diagnostic reprs."""

        return (
            "CardDocument("
            f"id={self.id!s}, serial={self.serial!r}, owner_id={self.owner_id!s}, "
            f"status={self.status.value!r}, is_current={self.is_current!r})"
        )

    def __str__(self) -> str:
        return self.__repr__()

    @model_validator(mode="after")
    def validate_lifecycle_invariants(self) -> CardDocument:
        operational_statuses = {
            CardStatus.ASSIGNED,
            CardStatus.ACTIVE,
            CardStatus.DISABLED,
        }
        terminal_statuses = {CardStatus.LOST, CardStatus.REPLACED}
        if self.status is CardStatus.UNASSIGNED:
            if self.owner_id is not None or self.is_current:
                raise ValueError("unassigned cards must be ownerless and non-current")
        elif self.status in operational_statuses:
            if self.owner_id is None or not self.is_current:
                raise ValueError("assigned operational cards must have an owner and be current")
        elif self.status in terminal_statuses and (self.owner_id is None or self.is_current):
            raise ValueError("terminal cards must have an owner and be non-current")
        if self.id == self.replaces_card_id or self.id == self.replacement_card_id:
            raise ValueError("replacement references cannot point to the same card")
        return self


@dataclass(frozen=True, repr=False)
class CardProvisioningResult:
    """Trusted one-time result containing the raw token after persistence succeeds."""

    card: CardDocument
    public_token: str

    def __repr__(self) -> str:
        return f"CardProvisioningResult(card={self.card!r}, public_token=<redacted>)"
