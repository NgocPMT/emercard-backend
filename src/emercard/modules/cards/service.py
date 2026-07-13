"""Application service for secure card provisioning and lifecycle changes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

from bson.objectid import ObjectId
from pymongo.errors import PyMongoError

from emercard.core.types import utc_now
from emercard.db.repositories import (
    InvalidIdentifierError,
    RepositoryConflictError,
    RepositoryError,
)
from emercard.modules.card_link_assignments.models import (
    CardLinkAssignmentDocument,
    CardLinkAssignmentStatus,
)
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
    CardNotIssuedError,
    CardOwnershipMismatchError,
    CardProfileNotReadyError,
    CardProvisioningError,
    CardReassignmentNotAllowedError,
    CardReplacementError,
    CardSerialConflictError,
    CardServiceUnavailableError,
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
from emercard.modules.profiles.models import profile_state
from emercard.modules.public_links.models import (
    PublicAccessLinkDocument,
    PublicAccessLinkStatus,
    PublicLinkPurpose,
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

    async def find_publicly_resolvable_by_token_hash(
        self, token_hash: str, *, session: Any | None = None
    ) -> CardDocument | None: ...

    async def create_blank_card(
        self,
        *,
        serial: str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument: ...

    async def list_user_controllable(
        self, user_id: ObjectId | str, *, session: Any | None = None
    ) -> list[CardDocument]: ...

    async def find_user_controllable(
        self,
        *,
        card_id: ObjectId | str,
        user_id: ObjectId | str,
        session: Any | None = None,
    ) -> CardDocument | None: ...

    async def activate_for_user(
        self,
        *,
        card_id: ObjectId | str,
        user_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None: ...

    async def disable_for_user(
        self,
        *,
        card_id: ObjectId | str,
        user_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None: ...

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


class ProfileRepositoryProtocol(Protocol):
    async def find_by_user_id(self, user_id: ObjectId | str) -> Any | None: ...


class PublicAccessLinkRepositoryProtocol(Protocol):
    async def find_by_id(
        self, link_id: ObjectId | str, *, session: Any | None = None
    ) -> PublicAccessLinkDocument | None: ...

    async def list_by_profile_id(
        self,
        profile_id: ObjectId | str,
        *,
        purpose: PublicLinkPurpose | None = None,
        session: Any | None = None,
    ) -> list[PublicAccessLinkDocument]: ...

    async def activate_link(
        self,
        *,
        link_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument | None: ...

    async def disable_link(
        self,
        *,
        link_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument | None: ...

    async def revoke_link(
        self,
        *,
        link_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument | None: ...


class CardLinkAssignmentRepositoryProtocol(Protocol):
    async def list_by_card_id(
        self, card_id: ObjectId | str, *, session: Any | None = None
    ) -> list[CardLinkAssignmentDocument]: ...

    async def find_active_by_card_id(
        self, card_id: ObjectId | str, *, session: Any | None = None
    ) -> CardLinkAssignmentDocument | None: ...

    async def find_active_by_public_access_link_id(
        self, public_access_link_id: ObjectId | str, *, session: Any | None = None
    ) -> CardLinkAssignmentDocument | None: ...

    async def list_by_public_access_link_id(
        self, public_access_link_id: ObjectId | str, *, session: Any | None = None
    ) -> list[CardLinkAssignmentDocument]: ...

    async def activate_assignment(
        self,
        *,
        assignment_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardLinkAssignmentDocument | None: ...

    async def deactivate_assignment(
        self,
        *,
        assignment_id: ObjectId | str,
        disabled_by_admin_id: ObjectId | str | None = None,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardLinkAssignmentDocument | None: ...

    async def detach_assignment(
        self,
        *,
        assignment_id: ObjectId | str,
        detached_by_admin_id: ObjectId | str | None = None,
        detach_reason: str | None = None,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardLinkAssignmentDocument | None: ...


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
        profile_repository: ProfileRepositoryProtocol | None = None,
        public_access_link_repository: PublicAccessLinkRepositoryProtocol | None = None,
        card_link_assignment_repository: CardLinkAssignmentRepositoryProtocol | None = None,
        idempotency_repository: IdempotencyRepositoryProtocol | None = None,
        custody_event_repository: CustodyEventRepositoryProtocol | None = None,
    ) -> None:
        self._repository = repository
        self._user_repository = user_repository
        self._profile_repository = profile_repository
        self._public_access_link_repository = public_access_link_repository
        self._card_link_assignment_repository = card_link_assignment_repository
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
            if (
                self._card_link_assignment_repository is not None
                and self._public_access_link_repository is not None
            ):
                assignment = await self._load_card_assignment(
                    card.id, allow_disabled=True, session=session
                )
                if assignment is not None:
                    if assignment.status in {
                        CardLinkAssignmentStatus.ACTIVE,
                        CardLinkAssignmentStatus.DISABLED,
                    }:
                        detached = await self._card_link_assignment_repository.detach_assignment(
                            assignment_id=assignment.id,
                            detached_by_admin_id=_as_object_id(admin_id)
                            if admin_id is not None
                            else None,
                            detach_reason="card unassigned before issue",
                            now=now,
                            session=session,
                        )
                        if detached is None:
                            raise CardReassignmentNotAllowedError(
                                "card unassignment was lost to a concurrent update"
                            )
                    link = await self._load_public_link(
                        assignment.public_access_link_id, session=session
                    )
                    if link is not None:
                        if link.status is PublicAccessLinkStatus.PENDING:
                            revoked = await self._public_access_link_repository.revoke_link(
                                link_id=link.id, now=now, session=session
                            )
                            if revoked is None:
                                raise CardReassignmentNotAllowedError(
                                    "card unassignment was lost to a concurrent update"
                                )
                        elif link.status is PublicAccessLinkStatus.ACTIVE:
                            disabled = await self._public_access_link_repository.disable_link(
                                link_id=link.id, now=now, session=session
                            )
                            if disabled is None:
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
            if (
                self._card_link_assignment_repository is not None
                and self._public_access_link_repository is not None
            ):
                assignment = await self._load_card_assignment(
                    card.id, allow_disabled=True, session=session
                )
                if assignment is not None:
                    if assignment.status is CardLinkAssignmentStatus.DISABLED:
                        activated_assignment = (
                            await self._card_link_assignment_repository.activate_assignment(
                                assignment_id=assignment.id, now=now, session=session
                            )
                        )
                        if activated_assignment is None:
                            raise CardAlreadyIssuedError(
                                "card issuance was lost to a concurrent update"
                            )
                    link = await self._load_public_link(
                        assignment.public_access_link_id, session=session
                    )
                    if link is not None and link.status is not PublicAccessLinkStatus.ACTIVE:
                        activated_link = await self._public_access_link_repository.activate_link(
                            link_id=link.id, now=now, session=session
                        )
                        if activated_link is None:
                            raise CardAlreadyIssuedError(
                                "card issuance was lost to a concurrent update"
                            )
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
            if (
                self._card_link_assignment_repository is not None
                and self._public_access_link_repository is not None
            ):
                assignment = await self._load_card_assignment(
                    card.id, allow_disabled=True, session=session
                )
                if assignment is not None:
                    if assignment.status in {
                        CardLinkAssignmentStatus.ACTIVE,
                        CardLinkAssignmentStatus.DISABLED,
                    }:
                        detached = await self._card_link_assignment_repository.detach_assignment(
                            assignment_id=assignment.id,
                            detached_by_admin_id=_as_object_id(admin_id)
                            if admin_id is not None
                            else None,
                            detach_reason="card voided before issue",
                            now=now,
                            session=session,
                        )
                        if detached is None:
                            raise CardTerminalStateError(
                                "card voiding was lost to a concurrent update"
                            )
                    link = await self._load_public_link(
                        assignment.public_access_link_id, session=session
                    )
                    if link is not None:
                        if link.status is PublicAccessLinkStatus.PENDING:
                            revoked = await self._public_access_link_repository.revoke_link(
                                link_id=link.id, now=now, session=session
                            )
                            if revoked is None:
                                raise CardTerminalStateError(
                                    "card voiding was lost to a concurrent update"
                                )
                        elif link.status is PublicAccessLinkStatus.ACTIVE:
                            disabled = await self._public_access_link_repository.disable_link(
                                link_id=link.id, now=now, session=session
                            )
                            if disabled is None:
                                raise CardTerminalStateError(
                                    "card voiding was lost to a concurrent update"
                                )
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

    async def list_user_cards(self, *, user_id: ObjectId | str) -> list[CardDocument]:
        """Return only issued current cards visible through the user's assigned links."""

        if (
            self._profile_repository is None
            or self._public_access_link_repository is None
            or self._card_link_assignment_repository is None
        ):
            try:
                return await self._repository.list_user_controllable(user_id)
            except InvalidIdentifierError as error:
                raise CardNotFoundError("card does not exist") from error
            except (RepositoryError, PyMongoError) as error:
                raise CardServiceUnavailableError("card service is unavailable") from error

        profile = await self._load_user_profile(user_id)
        try:
            links = await self._public_access_link_repository.list_by_profile_id(
                profile.id, purpose=PublicLinkPurpose.CARD
            )
            links = [link for link in links if link.status is PublicAccessLinkStatus.ACTIVE]
        except (InvalidIdentifierError, RepositoryError, PyMongoError, ValueError) as error:
            raise CardServiceUnavailableError("card service is unavailable") from error

        cards_by_id: dict[str, CardDocument] = {}
        for link in links:
            try:
                find_assignment = (
                    self._card_link_assignment_repository.find_active_by_public_access_link_id
                )
                assignment = await find_assignment(link.id)
            except (InvalidIdentifierError, RepositoryError, PyMongoError, ValueError) as error:
                raise CardServiceUnavailableError("card service is unavailable") from error
            if assignment is None:
                continue
            try:
                card = await self._repository.find_by_id(assignment.card_id)
            except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
                raise CardServiceUnavailableError("card service is unavailable") from error
            if (
                card is None
                or card.issued_at is None
                or not card.is_current
                or card.status not in {CardStatus.ASSIGNED, CardStatus.ACTIVE, CardStatus.DISABLED}
            ):
                continue
            cards_by_id[str(card.id)] = card
        return self._sort_user_cards(cards_by_id.values())

    async def get_user_card(
        self, *, card_id: ObjectId | str, user_id: ObjectId | str
    ) -> CardDocument:
        """Load a single visible card through the user's active card-purpose link."""

        if (
            self._profile_repository is None
            or self._public_access_link_repository is None
            or self._card_link_assignment_repository is None
        ):
            try:
                card = await self._repository.find_user_controllable(
                    card_id=card_id, user_id=user_id
                )
            except InvalidIdentifierError as error:
                raise CardNotFoundError("card does not exist") from error
            except (RepositoryError, PyMongoError) as error:
                raise CardServiceUnavailableError("card service is unavailable") from error
            if card is None:
                raise CardNotFoundError("card does not exist")
            return card

        card = await self._load_user_action_card(card_id=card_id, user_id=user_id)
        return card

    async def activate_user_card(
        self,
        *,
        card_id: ObjectId | str,
        user_id: ObjectId | str,
        now: datetime | None = None,
    ) -> CardDocument:
        """Activate a user's issued card and restore its assignment/link state."""

        if (
            self._profile_repository is None
            or self._public_access_link_repository is None
            or self._card_link_assignment_repository is None
        ):
            card = await self._load_user_action_card(
                card_id=card_id, user_id=user_id, require_ready=True
            )
            if card.status is CardStatus.ACTIVE:
                return card
            if card.status not in {CardStatus.ASSIGNED, CardStatus.DISABLED}:
                raise CardInvalidTransitionError("card lifecycle transition is not allowed")
            if card.encoding_verified_at is None:
                raise CardEncodingNotVerifiedError("card encoding has not been verified")
            try:
                activated = await self._repository.transition_status(
                    card_id=card.id,
                    from_statuses={card.status},
                    to_status=CardStatus.ACTIVE,
                    now=now,
                )
            except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
                if isinstance(error, InvalidIdentifierError):
                    raise CardNotFoundError("card does not exist") from error
                raise CardServiceUnavailableError("card service is unavailable") from error
            if activated is not None:
                return activated
            return await self._resolve_user_transition_race(
                card_id=card.id, user_id=user_id, expected_status=CardStatus.ACTIVE
            )

        card = await self._load_user_action_card(
            card_id=card_id, user_id=user_id, require_ready=True, allow_disabled_link=True
        )
        if card.status is CardStatus.ACTIVE:
            return card
        if card.status not in {CardStatus.ASSIGNED, CardStatus.DISABLED}:
            raise CardInvalidTransitionError("card lifecycle transition is not allowed")
        if card.encoding_verified_at is None:
            raise CardEncodingNotVerifiedError("card encoding has not been verified")
        assignment = await self._load_card_assignment(card.id, allow_disabled=True)
        if assignment is None:
            raise CardNotFoundError("card does not exist")
        profile = await self._load_user_profile(user_id)
        link = await self._load_public_link(assignment.public_access_link_id)
        if link is None or link.profile_id != profile.id:
            raise CardNotFoundError("card does not exist")
        if link.status is not PublicAccessLinkStatus.ACTIVE:
            try:
                link = await self._public_access_link_repository.activate_link(
                    link_id=link.id, now=now
                )
            except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
                if isinstance(error, InvalidIdentifierError):
                    raise CardNotFoundError("card does not exist") from error
                raise CardServiceUnavailableError("card service is unavailable") from error
            if link is None:
                raise CardNotFoundError("card does not exist")

        async def mutate(session: Any | None = None) -> CardDocument:
            activated = await self._repository.transition_status(
                card_id=card.id,
                from_statuses={card.status},
                to_status=CardStatus.ACTIVE,
                now=now,
                session=session,
            )
            if activated is None:
                raise CardInvalidTransitionError("card lifecycle transition was not applied")
            if (
                self._card_link_assignment_repository is not None
                and assignment.status is CardLinkAssignmentStatus.DISABLED
            ):
                updated_assignment = (
                    await self._card_link_assignment_repository.activate_assignment(
                        assignment_id=assignment.id, now=now, session=session
                    )
                )
                if updated_assignment is None:
                    raise CardInvalidTransitionError("card lifecycle transition was not applied")
            if (
                self._public_access_link_repository is not None
                and link.status is not PublicAccessLinkStatus.ACTIVE
            ):
                updated_link = await self._public_access_link_repository.activate_link(
                    link_id=link.id, now=now, session=session
                )
                if updated_link is None:
                    raise CardInvalidTransitionError("card lifecycle transition was not applied")
            return activated

        if hasattr(self._repository, "with_transaction"):
            try:
                return await self._repository.with_transaction(mutate)
            except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
                if isinstance(error, InvalidIdentifierError):
                    raise CardNotFoundError("card does not exist") from error
                raise CardServiceUnavailableError("card service is unavailable") from error
        try:
            activated = await self._repository.transition_status(
                card_id=card.id,
                from_statuses={card.status},
                to_status=CardStatus.ACTIVE,
                now=now,
            )
        except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
            if isinstance(error, InvalidIdentifierError):
                raise CardNotFoundError("card does not exist") from error
            raise CardServiceUnavailableError("card service is unavailable") from error
        if activated is not None:
            return activated
        return await self._resolve_user_transition_race(
            card_id=card.id, user_id=user_id, expected_status=CardStatus.ACTIVE
        )

    async def disable_user_card(
        self,
        *,
        card_id: ObjectId | str,
        user_id: ObjectId | str,
        now: datetime | None = None,
    ) -> CardDocument:
        """Disable a user's issued active card and revoke its assignment/link state."""

        if (
            self._profile_repository is None
            or self._public_access_link_repository is None
            or self._card_link_assignment_repository is None
        ):
            card = await self._load_user_action_card(card_id=card_id, user_id=user_id)
            if card.status is CardStatus.DISABLED:
                return card
            if card.status is not CardStatus.ACTIVE:
                raise CardInvalidTransitionError("card lifecycle transition is not allowed")
            try:
                disabled = await self._repository.transition_status(
                    card_id=card.id,
                    from_statuses={card.status},
                    to_status=CardStatus.DISABLED,
                    now=now,
                )
            except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
                if isinstance(error, InvalidIdentifierError):
                    raise CardNotFoundError("card does not exist") from error
                raise CardServiceUnavailableError("card service is unavailable") from error
            if disabled is not None:
                return disabled
            return await self._resolve_user_transition_race(
                card_id=card.id, user_id=user_id, expected_status=CardStatus.DISABLED
            )

        card = await self._load_user_action_card(card_id=card_id, user_id=user_id)
        if card.status is CardStatus.DISABLED:
            return card
        if card.status is not CardStatus.ACTIVE:
            raise CardInvalidTransitionError("card lifecycle transition is not allowed")
        assignment = await self._load_card_assignment(card.id)
        if assignment is None:
            raise CardNotFoundError("card does not exist")
        link = await self._load_public_link(assignment.public_access_link_id)
        if link is None:
            raise CardNotFoundError("card does not exist")

        async def mutate(session: Any | None = None) -> CardDocument:
            disabled = await self._repository.transition_status(
                card_id=card.id,
                from_statuses={card.status},
                to_status=CardStatus.DISABLED,
                now=now,
                session=session,
            )
            if disabled is None:
                raise CardInvalidTransitionError("card lifecycle transition was not applied")
            if (
                self._card_link_assignment_repository is not None
                and assignment.status is CardLinkAssignmentStatus.ACTIVE
            ):
                updated_assignment = (
                    await self._card_link_assignment_repository.deactivate_assignment(
                        assignment_id=assignment.id, now=now, session=session
                    )
                )
                if updated_assignment is None:
                    raise CardInvalidTransitionError("card lifecycle transition was not applied")
            if (
                self._public_access_link_repository is not None
                and link.status is PublicAccessLinkStatus.ACTIVE
            ):
                updated_link = await self._public_access_link_repository.disable_link(
                    link_id=link.id, now=now, session=session
                )
                if updated_link is None:
                    raise CardInvalidTransitionError("card lifecycle transition was not applied")
            return disabled

        if hasattr(self._repository, "with_transaction"):
            try:
                return await self._repository.with_transaction(mutate)
            except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
                if isinstance(error, InvalidIdentifierError):
                    raise CardNotFoundError("card does not exist") from error
                raise CardServiceUnavailableError("card service is unavailable") from error
        try:
            disabled = await self._repository.transition_status(
                card_id=card.id,
                from_statuses={card.status},
                to_status=CardStatus.DISABLED,
                now=now,
            )
        except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
            if isinstance(error, InvalidIdentifierError):
                raise CardNotFoundError("card does not exist") from error
            raise CardServiceUnavailableError("card service is unavailable") from error
        if disabled is not None:
            return disabled
        return await self._resolve_user_transition_race(
            card_id=card.id, user_id=user_id, expected_status=CardStatus.DISABLED
        )

    async def _load_user_action_card(
        self,
        *,
        card_id: ObjectId | str,
        user_id: ObjectId | str,
        require_ready: bool = False,
        allow_disabled_link: bool = False,
    ) -> CardDocument:
        if (
            self._profile_repository is None
            or self._public_access_link_repository is None
            or self._card_link_assignment_repository is None
        ):
            try:
                card = await self._repository.find_by_id(card_id)
            except InvalidIdentifierError as error:
                raise CardNotFoundError("card does not exist") from error
            except (RepositoryError, PyMongoError) as error:
                raise CardServiceUnavailableError("card service is unavailable") from error
            if card is None or card.owner_id != _as_object_id(user_id):
                raise CardNotFoundError("card does not exist")
            if card.status in {CardStatus.LOST, CardStatus.REPLACED, CardStatus.VOID}:
                raise CardTerminalStateError("terminal cards cannot change state")
            if not card.is_current:
                raise CardNotFoundError("card does not exist")
            if card.issued_at is None:
                raise CardNotIssuedError("card has not been issued")
            if require_ready:
                await self._require_ready_profile(user_id)
            return card

        profile = await self._load_user_profile(user_id)
        if require_ready and profile_state(profile) != "ready_to_publish":
            raise CardProfileNotReadyError("profile is not ready for card activation")
        try:
            card = await self._repository.find_by_id(card_id)
        except InvalidIdentifierError as error:
            raise CardNotFoundError("card does not exist") from error
        except (RepositoryError, PyMongoError) as error:
            raise CardServiceUnavailableError("card service is unavailable") from error
        if card is None:
            raise CardNotFoundError("card does not exist")
        assignment = await self._load_card_assignment(
            card.id, allow_disabled=allow_disabled_link
        )
        if assignment is None:
            raise CardNotFoundError("card does not exist")
        link = await self._load_public_link(assignment.public_access_link_id)
        allowed_link_statuses = (
            {PublicAccessLinkStatus.ACTIVE, PublicAccessLinkStatus.DISABLED}
            if allow_disabled_link
            else {PublicAccessLinkStatus.ACTIVE}
        )
        if (
            link is None
            or link.purpose is not PublicLinkPurpose.CARD
            or link.profile_id != profile.id
            or link.status not in allowed_link_statuses
        ):
            raise CardNotFoundError("card does not exist")
        if card.status in {CardStatus.LOST, CardStatus.REPLACED, CardStatus.VOID}:
            raise CardTerminalStateError("terminal cards cannot change state")
        if not card.is_current:
            raise CardNotFoundError("card does not exist")
        if card.issued_at is None:
            raise CardNotIssuedError("card has not been issued")
        return card

    async def _load_user_profile(self, user_id: ObjectId | str) -> Any:
        repository = self._profile_repository
        if repository is None:
            raise CardServiceUnavailableError("card service is unavailable")
        try:
            profile = await repository.find_by_user_id(user_id)
        except (RepositoryError, PyMongoError, ValueError) as error:
            raise CardServiceUnavailableError("card service is unavailable") from error
        if profile is None:
            raise CardServiceUnavailableError("card service is unavailable")
        return profile

    async def _load_card_assignment(
        self,
        card_id: ObjectId | str,
        *,
        allow_disabled: bool = False,
        session: Any | None = None,
    ) -> CardLinkAssignmentDocument | None:
        repository = self._card_link_assignment_repository
        if repository is None:
            return None
        try:
            if allow_disabled:
                assignments = await repository.list_by_card_id(card_id, session=session)
                for assignment in assignments:
                    if assignment.status in {
                        CardLinkAssignmentStatus.ACTIVE,
                        CardLinkAssignmentStatus.DISABLED,
                    }:
                        return assignment
                return None
            return await repository.find_active_by_card_id(card_id, session=session)
        except (InvalidIdentifierError, RepositoryError, PyMongoError, ValueError) as error:
            raise CardServiceUnavailableError("card service is unavailable") from error

    async def _load_public_link(
        self, link_id: ObjectId | str, *, session: Any | None = None
    ) -> PublicAccessLinkDocument | None:
        repository = self._public_access_link_repository
        if repository is None:
            return None
        try:
            return await repository.find_by_id(link_id, session=session)
        except (InvalidIdentifierError, RepositoryError, PyMongoError, ValueError) as error:
            raise CardServiceUnavailableError("card service is unavailable") from error

    def _sort_user_cards(self, cards: list[CardDocument] | Any) -> list[CardDocument]:
        status_order = {
            CardStatus.ACTIVE: 0,
            CardStatus.DISABLED: 1,
            CardStatus.ASSIGNED: 2,
        }
        ordered = list(cards)
        return sorted(
            ordered,
            key=lambda card: (
                status_order[card.status],
                -(card.issued_at.timestamp() if card.issued_at is not None else 0),
                str(card.id),
            ),
        )

    async def _require_ready_profile(self, user_id: ObjectId | str) -> None:
        profile = await self._load_user_profile(user_id)
        if profile_state(profile) != "ready_to_publish":
            raise CardProfileNotReadyError("profile is not ready for card activation")

    async def _resolve_user_transition_race(
        self,
        *,
        card_id: ObjectId,
        user_id: ObjectId | str,
        expected_status: CardStatus,
    ) -> CardDocument:
        latest = await self._load_user_action_card(card_id=card_id, user_id=user_id)
        if latest.status is expected_status:
            return latest
        if latest.status in {CardStatus.LOST, CardStatus.REPLACED, CardStatus.VOID}:
            raise CardTerminalStateError("terminal cards cannot change state")
        raise CardInvalidTransitionError("card lifecycle transition was not applied")

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
        if (
            self._public_access_link_repository is None
            or self._card_link_assignment_repository is None
        ):
            return await self.transition(card_id=card_id, to_status=CardStatus.LOST, now=now)

        card = await self._require_card(card_id)
        if card.status in {CardStatus.LOST, CardStatus.REPLACED}:
            raise CardTerminalStateError("terminal cards cannot change state")
        if card.status not in {CardStatus.ASSIGNED, CardStatus.ACTIVE, CardStatus.DISABLED}:
            raise CardInvalidTransitionError("card lifecycle transition is not allowed")

        async def mutate(session: Any | None = None) -> CardDocument:
            lost = await self._repository.transition_status(
                card_id=card.id,
                from_statuses={card.status},
                to_status=CardStatus.LOST,
                now=now,
                session=session,
            )
            if lost is None:
                raise CardInvalidTransitionError("card lifecycle transition was not applied")
            assignment_repository = self._card_link_assignment_repository
            link_repository = self._public_access_link_repository
            assert assignment_repository is not None
            assert link_repository is not None
            assignment = await self._load_card_assignment(
                card.id, allow_disabled=True, session=session
            )
            if assignment is not None:
                if assignment.status is CardLinkAssignmentStatus.ACTIVE:
                    deactivated = await assignment_repository.deactivate_assignment(
                        assignment_id=assignment.id,
                        now=now,
                        session=session,
                    )
                    if deactivated is None:
                        raise CardInvalidTransitionError(
                            "card lifecycle transition was not applied"
                        )
                link = await self._load_public_link(
                    assignment.public_access_link_id, session=session
                )
                if link is not None:
                    if link.status is PublicAccessLinkStatus.PENDING:
                        revoked = await link_repository.revoke_link(
                            link_id=link.id, now=now, session=session
                        )
                        if revoked is None:
                            raise CardInvalidTransitionError(
                                "card lifecycle transition was not applied"
                            )
                    elif link.status is PublicAccessLinkStatus.ACTIVE:
                        disabled = await link_repository.disable_link(
                            link_id=link.id, now=now, session=session
                        )
                        if disabled is None:
                            raise CardInvalidTransitionError(
                                "card lifecycle transition was not applied"
                            )
            return lost

        if hasattr(self._repository, "with_transaction"):
            try:
                return await self._repository.with_transaction(mutate)
            except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
                if isinstance(error, InvalidIdentifierError):
                    raise CardNotFoundError("card does not exist") from error
                raise CardServiceUnavailableError("card service is unavailable") from error
        try:
            lost = await self._repository.transition_status(
                card_id=card.id,
                from_statuses={card.status},
                to_status=CardStatus.LOST,
                now=now,
            )
        except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
            if isinstance(error, InvalidIdentifierError):
                raise CardNotFoundError("card does not exist") from error
            raise CardServiceUnavailableError("card service is unavailable") from error
        if lost is not None:
            return lost
        raise CardInvalidTransitionError("card lifecycle transition was not applied")

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
                if (
                    self._card_link_assignment_repository is not None
                    and self._public_access_link_repository is not None
                ):
                    assignment_repository = self._card_link_assignment_repository
                    link_repository = self._public_access_link_repository
                    assignment = await self._load_card_assignment(
                        old_card.id, allow_disabled=True, session=session
                    )
                    if assignment is not None:
                        if assignment.status is CardLinkAssignmentStatus.ACTIVE:
                            deactivated = (
                                await assignment_repository.deactivate_assignment(
                                    assignment_id=assignment.id,
                                    now=timestamp,
                                    session=session,
                                )
                            )
                            if deactivated is None:
                                raise CardReplacementError(
                                    "replacement card access could not be updated"
                                )
                        link = await self._load_public_link(
                            assignment.public_access_link_id, session=session
                        )
                        if link is not None:
                            if link.status is PublicAccessLinkStatus.PENDING:
                                revoked = await link_repository.revoke_link(
                                    link_id=link.id, now=timestamp, session=session
                                )
                                if revoked is None:
                                    raise CardReplacementError(
                                        "replacement card access could not be updated"
                                    )
                            elif link.status is PublicAccessLinkStatus.ACTIVE:
                                disabled = await link_repository.disable_link(
                                    link_id=link.id, now=timestamp, session=session
                                )
                                if disabled is None:
                                    raise CardReplacementError(
                                        "replacement card access could not be updated"
                                    )
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
