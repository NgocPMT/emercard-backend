"""Application service for secure card provisioning and lifecycle changes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol

from bson.objectid import ObjectId

from emercard.core.types import utc_now
from emercard.db.repositories import RepositoryError
from emercard.modules.cards.errors import (
    CardAlreadyAssignedError,
    CardError,
    CardInvalidTransitionError,
    CardNotFoundError,
    CardOwnershipMismatchError,
    CardProvisioningError,
    CardReplacementError,
    CardSerialConflictError,
    CardTerminalStateError,
    CardTokenHashConflictError,
    CardUserNotFoundError,
)
from emercard.modules.cards.identity import (
    generate_public_token,
    generate_serial,
    hash_public_token,
)
from emercard.modules.cards.models import CardDocument, CardProvisioningResult, CardStatus

_MAX_IDENTITY_RETRIES = 3


class CardRepositoryProtocol(Protocol):
    async def create_unassigned_card(
        self,
        *,
        serial: str,
        token_hash: str,
        replaces_card_id: ObjectId | str | None = None,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument: ...

    async def find_by_id(
        self, card_id: ObjectId | str, *, session: Any | None = None
    ) -> CardDocument | None: ...

    async def assign_to_user(
        self,
        *,
        card_id: ObjectId | str,
        user_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None: ...

    async def transition_status(
        self,
        *,
        card_id: ObjectId | str,
        from_statuses: set[CardStatus],
        to_status: CardStatus,
        owner_id: ObjectId | str | None = None,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None: ...

    async def mark_replaced(
        self,
        *,
        card_id: ObjectId | str,
        owner_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None: ...

    async def link_replacement(
        self,
        *,
        card_id: ObjectId | str,
        replacement_card_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None: ...

    async def with_transaction(self, operation: Callable[[Any], Awaitable[Any]]) -> Any: ...


class UserRepositoryProtocol(Protocol):
    async def find_by_id(self, user_id: ObjectId | str) -> Any | None: ...


class CardService:
    """Own card identity generation, lifecycle policy, and replacement orchestration."""

    def __init__(
        self,
        repository: CardRepositoryProtocol,
        user_repository: UserRepositoryProtocol,
        *,
        serial_generator: Callable[[], str] = generate_serial,
        token_generator: Callable[[], str] = generate_public_token,
    ) -> None:
        self._repository = repository
        self._user_repository = user_repository
        self._serial_generator = serial_generator
        self._token_generator = token_generator

    async def provision_unassigned(self, *, now: datetime | None = None) -> CardProvisioningResult:
        """Persist a new identity before returning its raw token exactly once."""

        timestamp = now or utc_now()
        for _ in range(_MAX_IDENTITY_RETRIES):
            public_token = self._token_generator()
            try:
                card = await self._repository.create_unassigned_card(
                    serial=self._serial_generator(),
                    token_hash=hash_public_token(public_token),
                    now=timestamp,
                )
            except CardSerialConflictError, CardTokenHashConflictError:
                continue
            except RepositoryError as error:
                raise CardProvisioningError("card provisioning failed") from error
            except CardError as error:
                raise CardProvisioningError("card provisioning failed") from error
            return CardProvisioningResult(card=card, public_token=public_token)
        raise CardProvisioningError("card identity could not be made unique")

    async def assign_to_user(
        self,
        *,
        card_id: ObjectId | str,
        user_id: ObjectId | str,
        now: datetime | None = None,
    ) -> CardDocument:
        if await self._user_repository.find_by_id(user_id) is None:
            raise CardUserNotFoundError("card owner does not exist")
        card = await self._repository.find_by_id(card_id)
        if card is None:
            raise CardNotFoundError("card does not exist")
        if card.status is not CardStatus.UNASSIGNED:
            if card.status in {CardStatus.LOST, CardStatus.REPLACED}:
                raise CardTerminalStateError("terminal cards cannot be assigned")
            raise CardAlreadyAssignedError("card is already assigned")
        if card.owner_id is not None or card.is_current:
            raise CardAlreadyAssignedError("card is already assigned")
        assigned = await self._repository.assign_to_user(card_id=card.id, user_id=user_id, now=now)
        if assigned is None:
            raise CardAlreadyAssignedError("card assignment was lost to a concurrent update")
        return assigned

    async def transition(
        self,
        *,
        card_id: ObjectId | str,
        to_status: CardStatus,
        owner_id: ObjectId | str | None = None,
        now: datetime | None = None,
    ) -> CardDocument:
        card = await self._repository.find_by_id(card_id)
        if card is None:
            raise CardNotFoundError("card does not exist")
        if owner_id is not None and card.owner_id != _as_object_id(owner_id):
            raise CardOwnershipMismatchError("card does not belong to the expected user")
        if card.status in {CardStatus.LOST, CardStatus.REPLACED}:
            raise CardTerminalStateError("terminal cards cannot change state")
        allowed = {
            CardStatus.ASSIGNED: {CardStatus.ACTIVE, CardStatus.LOST, CardStatus.REPLACED},
            CardStatus.ACTIVE: {CardStatus.DISABLED, CardStatus.LOST, CardStatus.REPLACED},
            CardStatus.DISABLED: {CardStatus.ACTIVE, CardStatus.LOST, CardStatus.REPLACED},
        }
        if to_status not in allowed.get(card.status, set()):
            raise CardInvalidTransitionError("card lifecycle transition is not allowed")
        transitioned = await self._repository.transition_status(
            card_id=card.id,
            from_statuses={card.status},
            to_status=to_status,
            owner_id=owner_id,
            now=now,
        )
        if transitioned is None:
            raise CardInvalidTransitionError("card lifecycle transition was not applied")
        return transitioned

    async def activate(
        self, *, card_id: ObjectId | str, owner_id: ObjectId | str, now: datetime | None = None
    ) -> CardDocument:
        return await self.transition(
            card_id=card_id, owner_id=owner_id, to_status=CardStatus.ACTIVE, now=now
        )

    async def disable(
        self, *, card_id: ObjectId | str, owner_id: ObjectId | str, now: datetime | None = None
    ) -> CardDocument:
        return await self.transition(
            card_id=card_id, owner_id=owner_id, to_status=CardStatus.DISABLED, now=now
        )

    async def mark_lost(
        self, *, card_id: ObjectId | str, now: datetime | None = None
    ) -> CardDocument:
        return await self.transition(card_id=card_id, to_status=CardStatus.LOST, now=now)

    async def replace(
        self, *, card_id: ObjectId | str, now: datetime | None = None
    ) -> CardProvisioningResult:
        old_card = await self._repository.find_by_id(card_id)
        if old_card is None:
            raise CardNotFoundError("card does not exist")
        if old_card.status in {CardStatus.LOST, CardStatus.REPLACED}:
            raise CardTerminalStateError("terminal cards cannot be replaced")
        if (
            old_card.status
            not in {
                CardStatus.ASSIGNED,
                CardStatus.ACTIVE,
                CardStatus.DISABLED,
            }
            or old_card.owner_id is None
        ):
            raise CardReplacementError("card is not eligible for replacement")
        owner_id = old_card.owner_id

        timestamp = now or utc_now()
        for _ in range(_MAX_IDENTITY_RETRIES):
            public_token = self._token_generator()

            async def operation(
                session: Any, public_token: str = public_token
            ) -> CardProvisioningResult:
                new_card = await self._repository.create_unassigned_card(
                    serial=self._serial_generator(),
                    token_hash=hash_public_token(public_token),
                    replaces_card_id=old_card.id,
                    now=timestamp,
                    session=session,
                )
                assigned = await self._repository.assign_to_user(
                    card_id=new_card.id,
                    user_id=owner_id,
                    now=timestamp,
                    session=session,
                )
                if assigned is None:
                    raise CardReplacementError("replacement card assignment failed")
                replaced = await self._repository.mark_replaced(
                    card_id=old_card.id,
                    owner_id=owner_id,
                    now=timestamp,
                    session=session,
                )
                if replaced is None:
                    raise CardReplacementError("old card replacement failed")
                linked = await self._repository.link_replacement(
                    card_id=old_card.id,
                    replacement_card_id=assigned.id,
                    now=timestamp,
                    session=session,
                )
                if linked is None:
                    raise CardReplacementError("replacement history could not be linked")
                return CardProvisioningResult(card=assigned, public_token=public_token)

            try:
                return await self._repository.with_transaction(operation)
            except CardSerialConflictError, CardTokenHashConflictError:
                continue
            except CardReplacementError:
                raise
            except (RepositoryError, CardError) as error:
                raise CardReplacementError("card replacement failed") from error
            except Exception as error:
                raise CardReplacementError("card replacement failed") from error
        raise CardReplacementError("replacement card identity could not be made unique")


def _as_object_id(value: ObjectId | str) -> ObjectId:
    return value if isinstance(value, ObjectId) else ObjectId(value)
