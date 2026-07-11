"""Application service for secure card provisioning and lifecycle changes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

from bson.objectid import ObjectId

from emercard.core.types import utc_now
from emercard.db.repositories import RepositoryConflictError, RepositoryError
from emercard.modules.cards.errors import (
    CardAlreadyAssignedError,
    CardAlreadyIssuedError,
    CardAssignmentTargetInvalidError,
    CardEncodingMismatchError,
    CardEncodingNotVerifiedError,
    CardError,
    CardInvalidTransitionError,
    CardLinkAlreadyProvisionedError,
    CardNotFoundError,
    CardOwnershipMismatchError,
    CardProvisioningError,
    CardReassignmentNotAllowedError,
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
from emercard.modules.cards.models import (
    CardDocument,
    CardLinkProvisioningResult,
    CardProvisioningResult,
    CardStatus,
)

_MAX_IDENTITY_RETRIES = 3


class CardRepositoryProtocol(Protocol):
    async def create_unassigned_card(
        self,
        *,
        serial: str,
        token_hash: str | None,
        replaces_card_id: ObjectId | str | None = None,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument: ...

    async def find_by_id(
        self, card_id: ObjectId | str, *, session: Any | None = None
    ) -> CardDocument | None: ...

    async def create_blank_card(
        self,
        *,
        serial: str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument: ...

    async def provision_link(
        self,
        *,
        card_id: ObjectId | str,
        token_hash: str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None: ...

    async def reprovision_link(
        self,
        *,
        card_id: ObjectId | str,
        token_hash: str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None: ...

    async def confirm_encoding(
        self,
        *,
        card_id: ObjectId | str,
        token_hash: str,
        admin_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None: ...

    async def assign_to_user(
        self,
        *,
        card_id: ObjectId | str,
        user_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None: ...

    async def assign_verified_to_user(
        self,
        *,
        card_id: ObjectId | str,
        user_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None: ...

    async def reassign_before_issue(
        self,
        *,
        card_id: ObjectId | str,
        new_owner_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None: ...

    async def unassign_before_issue(
        self,
        *,
        card_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None: ...

    async def issue(
        self,
        *,
        card_id: ObjectId | str,
        admin_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None: ...

    async def void_before_issue(
        self,
        *,
        card_id: ObjectId | str,
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

    async def find_by_email(self, email: str) -> Any | None: ...


class CustodyEventRepositoryProtocol(Protocol):
    async def append(
        self,
        *,
        card_id: ObjectId,
        event_type: str,
        previous_owner_id: ObjectId | None,
        new_owner_id: ObjectId | None,
        performed_by_admin_id: ObjectId,
        reason: str | None,
        now: datetime,
        session: Any | None = None,
    ) -> ObjectId: ...


class IdempotencyRepositoryProtocol(Protocol):
    async def find_card_id(
        self, operation_key: str, *, session: Any | None = None
    ) -> ObjectId | None: ...

    async def save_card_id(
        self,
        *,
        operation_key: str,
        card_id: ObjectId,
        now: datetime,
        session: Any | None = None,
    ) -> None: ...


class CardService:
    """Own card identity generation, lifecycle policy, and replacement orchestration."""

    def __init__(
        self,
        repository: CardRepositoryProtocol,
        user_repository: UserRepositoryProtocol,
        *,
        serial_generator: Callable[[], str] = generate_serial,
        token_generator: Callable[[], str] = generate_public_token,
        public_card_base_url: str | None = None,
        idempotency_repository: IdempotencyRepositoryProtocol | None = None,
        custody_event_repository: CustodyEventRepositoryProtocol | None = None,
    ) -> None:
        self._repository = repository
        self._user_repository = user_repository
        self._serial_generator = serial_generator
        self._token_generator = token_generator
        self._public_card_base_url = (
            public_card_base_url.rstrip("/") if public_card_base_url else None
        )
        self._idempotency_repository = idempotency_repository
        self._custody_event_repository = custody_event_repository

    async def create_blank_card(
        self,
        *,
        operation_key: str | None = None,
        now: datetime | None = None,
    ) -> CardDocument:
        """Create serial-only inventory without generating public-link material."""

        timestamp = now or utc_now()
        idempotency = self._idempotency_repository
        if operation_key is not None and idempotency is not None:
            existing_id = await idempotency.find_card_id(operation_key)
            if existing_id is not None:
                existing = await self._repository.find_by_id(existing_id)
                if existing is None:
                    raise CardProvisioningError("idempotent card result is unavailable")
                return existing
        for _ in range(_MAX_IDENTITY_RETRIES):
            try:
                if operation_key is not None and idempotency is not None:

                    async def operation(session: Any) -> CardDocument:
                        created = await self._repository.create_blank_card(
                            serial=self._serial_generator(), now=timestamp, session=session
                        )
                        await idempotency.save_card_id(
                            operation_key=operation_key,
                            card_id=created.id,
                            now=timestamp,
                            session=session,
                        )
                        return created

                    return await self._repository.with_transaction(operation)
                return await self._repository.create_blank_card(
                    serial=self._serial_generator(), now=timestamp
                )
            except CardSerialConflictError:
                continue
            except RepositoryConflictError as conflict:
                existing_id = None
                if idempotency is not None and operation_key is not None:
                    existing_id = await idempotency.find_card_id(operation_key)
                if existing_id is not None:
                    existing = await self._repository.find_by_id(existing_id)
                    if existing is not None:
                        return existing
                raise CardProvisioningError(
                    "idempotency operation could not be completed"
                ) from conflict
            except (RepositoryError, CardError) as error:
                raise CardProvisioningError("blank card creation failed") from error
        raise CardProvisioningError("card serial could not be made unique")

    async def provision_link(
        self, *, card_id: ObjectId | str, now: datetime | None = None
    ) -> CardLinkProvisioningResult:
        """Persist a new token hash before returning the raw token and URL."""

        base_url = self._public_card_base_url
        if base_url is None:
            raise CardProvisioningError("public card base URL is not configured")
        card = await self._require_card(card_id)
        if card.status is not CardStatus.UNASSIGNED or card.owner_id is not None:
            raise CardLinkAlreadyProvisionedError("card is not available for provisioning")
        if card.encoding_verified_at is not None or card.issued_at is not None:
            raise CardLinkAlreadyProvisionedError("card link cannot be provisioned in this state")
        if card.token_hash is not None:
            raise CardLinkAlreadyProvisionedError("card link is already provisioned")
        return await self._persist_link(card, base_url=base_url, reprovision=False, now=now)

    async def reprovision_link(
        self, *, card_id: ObjectId | str, now: datetime | None = None
    ) -> CardLinkProvisioningResult:
        """Replace an unverified token; the previous physical link stops matching."""

        base_url = self._public_card_base_url
        if base_url is None:
            raise CardProvisioningError("public card base URL is not configured")
        card = await self._require_card(card_id)
        if (
            card.status is not CardStatus.UNASSIGNED
            or card.owner_id is not None
            or card.encoding_verified_at is not None
            or card.issued_at is not None
            or card.token_hash is None
        ):
            raise CardLinkAlreadyProvisionedError("card link cannot be reprovisioned in this state")
        return await self._persist_link(card, base_url=base_url, reprovision=True, now=now)

    async def confirm_encoding(
        self,
        *,
        card_id: ObjectId | str,
        public_url: str,
        admin_id: ObjectId | str,
        now: datetime | None = None,
    ) -> CardDocument:
        """Verify a physical read-back URL without returning token material."""

        token = self._token_from_url(public_url)
        card = await self._require_card(card_id)
        if card.token_hash is None or card.provisioned_at is None:
            raise CardEncodingNotVerifiedError("card link has not been provisioned")
        token_hash = hash_public_token(token)
        if card.encoding_verified_at is not None:
            if card.token_hash != token_hash:
                raise CardEncodingMismatchError("encoded card link does not match")
            return card
        confirmed = await self._repository.confirm_encoding(
            card_id=card.id,
            token_hash=token_hash,
            admin_id=admin_id,
            now=now,
        )
        if confirmed is None:
            raise CardEncodingMismatchError("encoded card link does not match")
        return confirmed

    async def assign_verified_to_user(
        self,
        *,
        card_id: ObjectId | str,
        user_id: ObjectId | str,
        admin_id: ObjectId | str | None = None,
        now: datetime | None = None,
    ) -> CardDocument:
        """Assign only a verified, unissued card to an existing user."""

        user = await self._user_repository.find_by_id(user_id)
        if user is None or getattr(user, "role", None) != "user":
            raise CardAssignmentTargetInvalidError("card assignment target is invalid")
        card = await self._require_card(card_id)
        if card.encoding_verified_at is None or card.token_hash is None:
            raise CardEncodingNotVerifiedError("card encoding has not been verified")

        async def mutate(session: Any | None = None) -> CardDocument:
            assigned = await self._repository.assign_verified_to_user(
                card_id=card.id, user_id=user_id, now=now, session=session
            )
            if assigned is None:
                raise CardAlreadyAssignedError("card assignment was lost to a concurrent update")
            if self._custody_event_repository is not None:
                if admin_id is None:
                    raise CardProvisioningError("assignment administrator is required")
                await self._custody_event_repository.append(
                    card_id=card.id,
                    event_type="assigned",
                    previous_owner_id=None,
                    new_owner_id=assigned.owner_id,
                    performed_by_admin_id=_as_object_id(admin_id),
                    reason=None,
                    now=now or utc_now(),
                    session=session,
                )
            return assigned

        if self._custody_event_repository is None:
            return await mutate()
        return await self._repository.with_transaction(mutate)

    async def reassign_before_issue(
        self,
        *,
        card_id: ObjectId | str,
        new_owner_id: ObjectId | str,
        admin_id: ObjectId | str | None = None,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> CardDocument:
        user = await self._user_repository.find_by_id(new_owner_id)
        if user is None or getattr(user, "role", None) != "user":
            raise CardAssignmentTargetInvalidError("card assignment target is invalid")
        card = await self._require_card(card_id)
        if (
            card.status is not CardStatus.ASSIGNED
            or card.issued_at is not None
            or card.activated_at is not None
        ):
            raise CardReassignmentNotAllowedError("card cannot be reassigned in this state")

        async def mutate(session: Any | None = None) -> CardDocument:
            reassigned = await self._repository.reassign_before_issue(
                card_id=card.id, new_owner_id=new_owner_id, now=now, session=session
            )
            if reassigned is None:
                raise CardReassignmentNotAllowedError(
                    "card reassignment was lost to a concurrent update"
                )
            if self._custody_event_repository is not None:
                if admin_id is None:
                    raise CardProvisioningError("reassignment administrator is required")
                await self._custody_event_repository.append(
                    card_id=card.id,
                    event_type="reassigned",
                    previous_owner_id=card.owner_id,
                    new_owner_id=reassigned.owner_id,
                    performed_by_admin_id=_as_object_id(admin_id),
                    reason=reason,
                    now=now or utc_now(),
                    session=session,
                )
            return reassigned

        if self._custody_event_repository is None:
            return await mutate()
        return await self._repository.with_transaction(mutate)

    async def unassign_before_issue(
        self,
        *,
        card_id: ObjectId | str,
        admin_id: ObjectId | str | None = None,
        now: datetime | None = None,
    ) -> CardDocument:
        card = await self._require_card(card_id)
        if (
            card.status is not CardStatus.ASSIGNED
            or card.issued_at is not None
            or card.activated_at is not None
        ):
            raise CardReassignmentNotAllowedError("card cannot be unassigned in this state")

        async def mutate(session: Any | None = None) -> CardDocument:
            unassigned = await self._repository.unassign_before_issue(
                card_id=card.id, now=now, session=session
            )
            if unassigned is None:
                raise CardReassignmentNotAllowedError(
                    "card unassignment was lost to a concurrent update"
                )
            if self._custody_event_repository is not None:
                if admin_id is None:
                    raise CardProvisioningError("unassignment administrator is required")
                await self._custody_event_repository.append(
                    card_id=card.id,
                    event_type="unassigned",
                    previous_owner_id=card.owner_id,
                    new_owner_id=None,
                    performed_by_admin_id=_as_object_id(admin_id),
                    reason=None,
                    now=now or utc_now(),
                    session=session,
                )
            return unassigned

        if self._custody_event_repository is None:
            return await mutate()
        return await self._repository.with_transaction(mutate)

    async def issue(
        self,
        *,
        card_id: ObjectId | str,
        admin_id: ObjectId | str,
        now: datetime | None = None,
    ) -> CardDocument:
        card = await self._require_card(card_id)
        if card.issued_at is not None:
            return card
        if card.status is not CardStatus.ASSIGNED or card.owner_id is None:
            raise CardAlreadyIssuedError("card is not eligible for issuance")
        if card.encoding_verified_at is None or card.activated_at is not None:
            raise CardEncodingNotVerifiedError("card is not eligible for issuance")

        async def mutate(session: Any | None = None) -> CardDocument:
            issued = await self._repository.issue(
                card_id=card.id, admin_id=admin_id, now=now, session=session
            )
            if issued is None:
                raise CardAlreadyIssuedError("card issuance was lost to a concurrent update")
            if self._custody_event_repository is not None:
                await self._custody_event_repository.append(
                    card_id=card.id,
                    event_type="issued",
                    previous_owner_id=issued.owner_id,
                    new_owner_id=issued.owner_id,
                    performed_by_admin_id=_as_object_id(admin_id),
                    reason=None,
                    now=now or utc_now(),
                    session=session,
                )
            return issued

        if self._custody_event_repository is None:
            return await mutate()
        return await self._repository.with_transaction(mutate)

    async def void(
        self,
        *,
        card_id: ObjectId | str,
        admin_id: ObjectId | str | None = None,
        now: datetime | None = None,
    ) -> CardDocument:
        card = await self._require_card(card_id)
        if card.status is CardStatus.VOID:
            return card
        if card.issued_at is not None or card.activated_at is not None:
            raise CardTerminalStateError("issued or activated cards cannot be voided")

        async def mutate(session: Any | None = None) -> CardDocument:
            voided = await self._repository.void_before_issue(
                card_id=card.id, now=now, session=session
            )
            if voided is None:
                raise CardTerminalStateError("card voiding was lost to a concurrent update")
            if self._custody_event_repository is not None:
                if admin_id is None:
                    raise CardProvisioningError("voiding administrator is required")
                await self._custody_event_repository.append(
                    card_id=card.id,
                    event_type="voided",
                    previous_owner_id=card.owner_id,
                    new_owner_id=None,
                    performed_by_admin_id=_as_object_id(admin_id),
                    reason=None,
                    now=now or utc_now(),
                    session=session,
                )
            return voided

        if self._custody_event_repository is None:
            return await mutate()
        return await self._repository.with_transaction(mutate)

    async def _persist_link(
        self,
        card: CardDocument,
        *,
        base_url: str,
        reprovision: bool,
        now: datetime | None,
    ) -> CardLinkProvisioningResult:
        timestamp = now or utc_now()
        public_token = self._token_generator()
        token_hash = hash_public_token(public_token)
        method = (
            self._repository.reprovision_link if reprovision else self._repository.provision_link
        )
        persisted = await method(card_id=card.id, token_hash=token_hash, now=timestamp)
        if persisted is None:
            raise CardLinkAlreadyProvisionedError(
                "card link operation was lost to a concurrent update"
            )
        return CardLinkProvisioningResult(
            card=persisted,
            public_token=public_token,
            public_url=f"{base_url}/{public_token}",
        )

    async def _require_card(self, card_id: ObjectId | str) -> CardDocument:
        card = await self._repository.find_by_id(card_id)
        if card is None:
            raise CardNotFoundError("card does not exist")
        return card

    def _token_from_url(self, public_url: str) -> str:
        base_url = self._public_card_base_url
        if base_url is None:
            raise CardProvisioningError("public card base URL is not configured")
        expected = urlsplit(base_url)
        actual = urlsplit(public_url)
        if (
            actual.scheme != expected.scheme
            or actual.netloc != expected.netloc
            or actual.query
            or actual.fragment
            or not actual.path.startswith(expected.path + "/")
        ):
            raise CardEncodingMismatchError("encoded card link does not match configured URL")
        token = actual.path[len(expected.path) + 1 :]
        if not token or "/" in token:
            raise CardEncodingMismatchError("encoded card link does not match configured URL")
        return token

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
