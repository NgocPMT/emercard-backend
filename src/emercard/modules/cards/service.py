"""Application service for secure card provisioning and lifecycle changes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol, cast
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
    CardDirectOwnershipError,
    CardEncodingMismatchError,
    CardEncodingNotVerifiedError,
    CardError,
    CardInvalidTransitionError,
    CardLinkAlreadyProvisionedError,
    CardLinkNotBoundError,
    CardLinkRebindTargetInvalidError,
    CardLinkTerminalError,
    CardNotFoundError,
    CardNotIssuedError,
    CardOwnershipMismatchError,
    CardPostDeliveryRebindError,
    CardProfileLinkInvalidError,
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

    async def create_blank_card(
        self,
        *,
        serial: str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument: ...

    async def mark_link_bound(
        self,
        *,
        card_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None: ...

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

    async def confirm_encoding_without_token_hash(
        self,
        *,
        card_id: ObjectId | str,
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

    async def mark_replaced_without_owner(
        self,
        *,
        card_id: ObjectId | str,
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

    async def find_by_id(self, profile_id: ObjectId | str) -> Any | None: ...


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

    async def create_link(
        self,
        *,
        profile_id: ObjectId | str,
        purpose: PublicLinkPurpose,
        token_hash: str,
        label: str | None = None,
        status: PublicAccessLinkStatus = PublicAccessLinkStatus.PENDING,
        created_by: ObjectId | str | None = None,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument: ...

    async def rotate_link(
        self,
        *,
        link_id: ObjectId | str,
        token_hash: str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument: ...

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

    async def attach_link(
        self,
        *,
        card_id: ObjectId | str,
        public_access_link_id: ObjectId | str,
        attached_by_admin_id: ObjectId | str | None = None,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardLinkAssignmentDocument: ...

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

    async def with_transaction(self, operation: Callable[[Any], Awaitable[Any]]) -> Any: ...


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
        public_profile_base_url: str | None = None,
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
        self._public_profile_base_url = (
            public_profile_base_url.rstrip("/") if public_profile_base_url else None
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

    async def list_user_links(
        self,
        *,
        user_id: ObjectId | str,
        purpose: PublicLinkPurpose | None = None,
    ) -> list[PublicAccessLinkDocument]:
        profile = await self._load_user_profile(user_id)
        repository = self._public_access_link_repository
        if repository is None:
            return []
        try:
            return await repository.list_by_profile_id(profile.id, purpose=purpose)
        except (InvalidIdentifierError, RepositoryError, PyMongoError, ValueError) as error:
            raise CardServiceUnavailableError("card service is unavailable") from error

    async def describe_admin_card(
        self, *, card_id: ObjectId | str
    ) -> tuple[CardDocument, PublicAccessLinkDocument | None, CardLinkAssignmentDocument | None]:
        card = await self._require_card(card_id)
        assignment = await self._load_card_assignment(card.id, allow_disabled=True)
        link = (
            await self._load_public_link(assignment.public_access_link_id)
            if assignment is not None
            else None
        )
        return card, link, assignment

    async def describe_user_card(
        self, *, card_id: ObjectId | str, user_id: ObjectId | str
    ) -> tuple[CardDocument, PublicAccessLinkDocument | None, CardLinkAssignmentDocument | None]:
        card = await self._load_user_action_card(
            card_id=card_id,
            user_id=user_id,
            allow_disabled_link=True,
        )
        assignment = await self._load_card_assignment(card.id, allow_disabled=True)
        link = (
            await self._load_public_link(assignment.public_access_link_id)
            if assignment is not None
            else None
        )
        if link is not None:
            profile = await self._load_user_profile(user_id)
            if link.profile_id != profile.id:
                raise CardNotFoundError("card does not exist")
        return card, link, assignment

    async def create_user_link(
        self,
        *,
        user_id: ObjectId | str,
        purpose: PublicLinkPurpose,
        label: str | None = None,
        now: datetime | None = None,
    ) -> tuple[PublicAccessLinkDocument, str]:
        profile = await self._load_ready_profile(user_id)
        return await self._create_profile_link(
            profile_id=profile.id,
            purpose=purpose,
            label=label,
            status=PublicAccessLinkStatus.PENDING,
            now=now,
        )

    async def attach_card_link(
        self,
        *,
        card_id: ObjectId | str,
        public_access_link_id: ObjectId | str,
        admin_id: ObjectId | str | None = None,
        now: datetime | None = None,
    ) -> CardDocument:
        card = await self._require_card(card_id)
        if card.issued_at is not None:
            raise CardPostDeliveryRebindError("delivered cards cannot be rebound")
        link = await self._load_public_link(public_access_link_id)
        if link is None:
            raise CardProfileLinkInvalidError("profile link does not exist")
        if link.purpose is not PublicLinkPurpose.CARD:
            raise CardProfileLinkInvalidError("only card-purpose links can be bound")
        if link.status is not PublicAccessLinkStatus.PENDING:
            if link.status in {
                PublicAccessLinkStatus.REVOKED,
                PublicAccessLinkStatus.EXPIRED,
            }:
                raise CardLinkTerminalError("profile link is terminal")
            raise CardLinkRebindTargetInvalidError("only pending profile links can be bound")
        profile = await self._load_ready_profile_by_id(link.profile_id)
        if profile is None:
            raise CardProfileLinkInvalidError("profile link is not attached to a profile")
        assignment_repository = self._card_link_assignment_repository
        link_repository = self._public_access_link_repository
        if assignment_repository is None or link_repository is None:
            raise CardServiceUnavailableError("card service is unavailable")

        async def bind(session: Any | None = None) -> CardDocument:
            existing = await self._load_card_assignment(
                card.id, allow_disabled=True, session=session
            )
            if existing is not None and existing.public_access_link_id == link.id:
                return card
            if existing is not None:
                if card.issued_at is not None:
                    raise CardPostDeliveryRebindError("delivered cards cannot be rebound")
                old_link = await self._load_public_link(
                    existing.public_access_link_id, session=session
                )
                if old_link is not None and old_link.status in {
                    PublicAccessLinkStatus.PENDING,
                    PublicAccessLinkStatus.ACTIVE,
                    PublicAccessLinkStatus.DISABLED,
                }:
                    revoked = await link_repository.revoke_link(
                        link_id=old_link.id, now=now, session=session
                    )
                    if revoked is None:
                        raise CardReassignmentNotAllowedError(
                            "previous card link could not be revoked"
                        )
                detached = await assignment_repository.detach_assignment(
                    assignment_id=existing.id,
                    detached_by_admin_id=_as_object_id(admin_id) if admin_id is not None else None,
                    detach_reason="rebound before card delivery",
                    now=now,
                    session=session,
                )
                if detached is None:
                    raise CardReassignmentNotAllowedError(
                        "card link rebind was lost to a concurrent update"
                    )
            if (
                await assignment_repository.find_active_by_public_access_link_id(
                    link.id, session=session
                )
                is not None
            ):
                raise CardAlreadyAssignedError("public access link is already assigned")
            try:
                await assignment_repository.attach_link(
                    card_id=card.id,
                    public_access_link_id=link.id,
                    attached_by_admin_id=admin_id,
                    now=now,
                    session=session,
                )
                mark_link_bound = getattr(self._repository, "mark_link_bound", None)
                if card.status is CardStatus.UNASSIGNED:
                    if not callable(mark_link_bound):
                        raise CardServiceUnavailableError("card service is unavailable")
                    bound_card = await cast(
                        Callable[..., Awaitable[CardDocument | None]], mark_link_bound
                    )(card_id=card.id, now=now, session=session)
                    if bound_card is None:
                        raise CardInvalidTransitionError("card binding state could not be updated")
                    return bound_card
            except RepositoryConflictError as error:
                raise CardAlreadyAssignedError("card link assignment already exists") from error
            return card

        try:
            if hasattr(assignment_repository, "with_transaction"):
                return await assignment_repository.with_transaction(bind)
            return await bind()
        except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
            raise CardServiceUnavailableError("card service is unavailable") from error

    async def detach_card_link(
        self,
        *,
        card_id: ObjectId | str,
        admin_id: ObjectId | str | None = None,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> CardDocument:
        raise CardReassignmentNotAllowedError(
            "card links cannot be detached; rebind only before delivery"
        )

    async def activate_card_link(
        self,
        *,
        card_id: ObjectId | str,
        admin_id: ObjectId | str | None = None,
        now: datetime | None = None,
    ) -> CardDocument:
        card = await self._require_card(card_id)
        if card.issued_at is None or card.encoding_verified_at is None:
            raise CardNotIssuedError("card must be encoded and delivered before activation")
        assignment = await self._load_card_assignment(card.id, allow_disabled=True)
        if assignment is None or assignment.status is not CardLinkAssignmentStatus.ACTIVE:
            raise CardLinkNotBoundError("card has no active profile-link binding")
        link = await self._load_public_link(assignment.public_access_link_id)
        if link is None:
            raise CardProfileLinkInvalidError("card link does not exist")
        if link.status is PublicAccessLinkStatus.ACTIVE:
            return card
        if link.status not in {PublicAccessLinkStatus.PENDING, PublicAccessLinkStatus.DISABLED}:
            raise CardLinkTerminalError("card link is terminal")
        link_repository = self._public_access_link_repository
        assert link_repository is not None

        async def mutate(session: Any | None = None) -> CardDocument:
            activated = await link_repository.activate_link(
                link_id=link.id, now=now, session=session
            )
            if activated is None:
                raise CardInvalidTransitionError("card lifecycle transition was not applied")
            return card

        if hasattr(self._repository, "with_transaction"):
            try:
                return await self._repository.with_transaction(mutate)
            except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
                if isinstance(error, InvalidIdentifierError):
                    raise CardNotFoundError("card does not exist") from error
                raise CardServiceUnavailableError("card service is unavailable") from error
        try:
            activated = await link_repository.activate_link(link_id=link.id, now=now)
        except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
            if isinstance(error, InvalidIdentifierError):
                raise CardNotFoundError("card does not exist") from error
            raise CardServiceUnavailableError("card service is unavailable") from error
        if activated is not None:
            return card
        raise CardInvalidTransitionError("card lifecycle transition was not applied")

    async def disable_card_link(
        self,
        *,
        card_id: ObjectId | str,
        admin_id: ObjectId | str | None = None,
        now: datetime | None = None,
    ) -> CardDocument:
        card = await self._require_card(card_id)
        assignment = await self._load_card_assignment(card.id, allow_disabled=True)
        if assignment is None or assignment.status is not CardLinkAssignmentStatus.ACTIVE:
            raise CardLinkNotBoundError("card has no active profile-link binding")
        link = await self._load_public_link(assignment.public_access_link_id)
        if link is None:
            raise CardProfileLinkInvalidError("card link does not exist")
        if link.status is PublicAccessLinkStatus.DISABLED:
            return card
        if link.status is PublicAccessLinkStatus.PENDING:
            raise CardInvalidTransitionError("card lifecycle transition is not allowed")
        if link.status in {PublicAccessLinkStatus.REVOKED, PublicAccessLinkStatus.EXPIRED}:
            raise CardLinkTerminalError("card link is terminal")
        link_repository = self._public_access_link_repository
        assert link_repository is not None

        async def mutate(session: Any | None = None) -> CardDocument:
            disabled = await link_repository.disable_link(link_id=link.id, now=now, session=session)
            if disabled is None:
                raise CardInvalidTransitionError("card lifecycle transition was not applied")
            return card

        if hasattr(self._repository, "with_transaction"):
            try:
                return await self._repository.with_transaction(mutate)
            except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
                if isinstance(error, InvalidIdentifierError):
                    raise CardNotFoundError("card does not exist") from error
                raise CardServiceUnavailableError("card service is unavailable") from error
        try:
            disabled = await link_repository.disable_link(link_id=link.id, now=now)
        except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
            if isinstance(error, InvalidIdentifierError):
                raise CardNotFoundError("card does not exist") from error
            raise CardServiceUnavailableError("card service is unavailable") from error
        if disabled is not None:
            return card
        raise CardInvalidTransitionError("card lifecycle transition was not applied")

    async def revoke_card_link(
        self,
        *,
        card_id: ObjectId | str,
        admin_id: ObjectId | str | None = None,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> CardDocument:
        card = await self._require_card(card_id)
        assignment = await self._load_card_assignment(card.id, allow_disabled=True)
        if assignment is None:
            raise CardLinkNotBoundError("card has no active profile-link binding")
        link = await self._load_public_link(assignment.public_access_link_id)
        if link is None:
            raise CardProfileLinkInvalidError("card link does not exist")
        repository = self._public_access_link_repository
        if repository is None:
            raise CardServiceUnavailableError("card service is unavailable")
        if link.status is PublicAccessLinkStatus.REVOKED:
            return card
        revoked = await repository.revoke_link(link_id=link.id, now=now)
        if revoked is None:
            raise CardInvalidTransitionError("card link revocation was not applied")
        return card

    async def provision_link(
        self, *, card_id: ObjectId | str, now: datetime | None = None
    ) -> CardLinkProvisioningResult:
        """Reject card-local provisioning; links are created for profiles first."""

        del now
        card = await self._require_card(card_id)
        if card.issued_at is not None:
            raise CardAlreadyIssuedError("card has already been issued")
        raise CardLinkAlreadyProvisionedError(
            "create a pending profile link and bind it to the card instead"
        )

    async def reprovision_link(
        self, *, card_id: ObjectId | str, now: datetime | None = None
    ) -> CardLinkProvisioningResult:
        """Reject token rotation; rebind to another pending profile link instead."""

        del now
        card = await self._require_card(card_id)
        if card.encoding_verified_at is not None or card.issued_at is not None:
            raise CardLinkAlreadyProvisionedError("card link cannot be reprovisioned in this state")
        raise CardLinkAlreadyProvisionedError(
            "rebind the card to another pending profile link instead"
        )

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
        token_hash = hash_public_token(token)
        assignment = await self._load_card_assignment(card.id, allow_disabled=True)
        if assignment is None:
            raise CardLinkNotBoundError("card has no active profile-link binding")
        link = await self._load_public_link(assignment.public_access_link_id)
        if link is None:
            raise CardProfileLinkInvalidError("card link does not exist")
        if link.token_hash != token_hash:
            raise CardEncodingMismatchError("encoded card link does not match")
        if card.encoding_verified_at is not None:
            return card
        confirmed = await self._repository.confirm_encoding_without_token_hash(
            card_id=card.id,
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
        """Retain the legacy operation only for migration-compatible services."""

        if (
            self._profile_repository is not None
            and self._public_access_link_repository is not None
            and self._card_link_assignment_repository is not None
        ):
            raise CardDirectOwnershipError(
                "direct verified card-user assignment is retired; bind a profile link"
            )
        user = await self._user_repository.find_by_id(user_id)
        if user is None or getattr(user, "role", None) != "user":
            raise CardAssignmentTargetInvalidError("card assignment target is invalid")
        card = await self._require_card(card_id)
        if card.encoding_verified_at is None:
            raise CardEncodingNotVerifiedError("card encoding has not been verified")
        assignment = await self._load_card_assignment(card.id, allow_disabled=True)
        if self._profile_repository is None and self._card_link_assignment_repository is None:
            if card.legacy_token_hash is None:
                raise CardEncodingNotVerifiedError("card encoding has not been verified")
        else:
            profile = await self._load_user_profile(user_id)
            if assignment is None:
                raise CardAssignmentTargetInvalidError("card must be bound to a profile link first")
            link = await self._load_public_link(assignment.public_access_link_id)
            if link is None or link.profile_id != profile.id:
                raise CardAssignmentTargetInvalidError("card and profile do not match")

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
        if (
            self._profile_repository is not None
            and self._public_access_link_repository is not None
            and self._card_link_assignment_repository is not None
        ):
            raise CardDirectOwnershipError(
                "direct card reassignment is retired; rebind a pending profile link"
            )
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
        if (
            self._profile_repository is not None
            and self._card_link_assignment_repository is not None
        ):
            new_profile = await self._load_user_profile(new_owner_id)
            assignment = await self._load_card_assignment(card.id, allow_disabled=True)
            if assignment is None:
                raise CardReassignmentNotAllowedError("card must remain bound to a profile link")
            link = await self._load_public_link(assignment.public_access_link_id)
            if link is None or link.profile_id != new_profile.id:
                raise CardReassignmentNotAllowedError(
                    "rebind the card to the new profile link before delivery"
                )

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
        if (
            self._profile_repository is not None
            and self._public_access_link_repository is not None
            and self._card_link_assignment_repository is not None
        ):
            raise CardDirectOwnershipError(
                "direct card unassignment is retired; rebind or void the card"
            )
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
                    if link is not None and link.status in {
                        PublicAccessLinkStatus.PENDING,
                        PublicAccessLinkStatus.ACTIVE,
                    }:
                        revoked = await self._public_access_link_repository.revoke_link(
                            link_id=link.id, now=now, session=session
                        )
                        if revoked is None:
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
        if card.status not in {CardStatus.UNASSIGNED, CardStatus.ASSIGNED}:
            raise CardAlreadyIssuedError("card is not eligible for issuance")
        if card.encoding_verified_at is None or card.activated_at is not None:
            raise CardEncodingNotVerifiedError("card is not eligible for issuance")
        assignment = await self._load_card_assignment(card.id, allow_disabled=True)
        if assignment is None:
            raise CardLinkNotBoundError("card must be bound to a profile link first")
        link = await self._load_public_link(assignment.public_access_link_id)
        if link is None:
            raise CardProfileLinkInvalidError("card link does not exist")
        if link.status is not PublicAccessLinkStatus.PENDING:
            raise CardLinkRebindTargetInvalidError("card link must remain pending until delivery")

        async def mutate(session: Any | None = None) -> CardDocument:
            issued = await self._repository.issue(
                card_id=card.id, admin_id=admin_id, now=now, session=session
            )
            if issued is None:
                raise CardAlreadyIssuedError("card issuance was lost to a concurrent update")
            # Link availability is controlled independently by user/admin card-link actions.
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
                    if link is not None and link.status in {
                        PublicAccessLinkStatus.PENDING,
                        PublicAccessLinkStatus.ACTIVE,
                    }:
                        revoked = await self._public_access_link_repository.revoke_link(
                            link_id=link.id, now=now, session=session
                        )
                        if revoked is None:
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

    async def _create_profile_link(
        self,
        *,
        profile_id: ObjectId | str,
        purpose: PublicLinkPurpose,
        label: str | None = None,
        status: PublicAccessLinkStatus = PublicAccessLinkStatus.ACTIVE,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> tuple[PublicAccessLinkDocument, str]:
        repository = self._public_access_link_repository
        if repository is None:
            raise CardServiceUnavailableError("card service is unavailable")
        timestamp = now or utc_now()
        for _ in range(_MAX_IDENTITY_RETRIES):
            public_token = self._token_generator()
            try:
                persisted = await repository.create_link(
                    profile_id=profile_id,
                    purpose=purpose,
                    token_hash=hash_public_token(public_token),
                    label=label,
                    status=status,
                    now=timestamp,
                    session=session,
                )
            except RepositoryConflictError:
                continue
            except (RepositoryError, PyMongoError) as error:
                raise CardServiceUnavailableError("card service is unavailable") from error
            return persisted, public_token
        raise CardServiceUnavailableError("card service is unavailable")

    async def _require_card(self, card_id: ObjectId | str) -> CardDocument:
        card = await self._repository.find_by_id(card_id)
        if card is None:
            raise CardNotFoundError("card does not exist")
        return card

    def _token_from_url(self, public_url: str) -> str:
        bases = [
            value
            for value in (self._public_card_base_url, self._public_profile_base_url)
            if value is not None
        ]
        actual = urlsplit(public_url)
        if actual.query or actual.fragment:
            raise CardEncodingMismatchError("encoded card link does not match configured URL")
        for base_url in bases:
            expected = urlsplit(base_url)
            if (
                actual.scheme == expected.scheme
                and actual.netloc == expected.netloc
                and actual.path.startswith(expected.path + "/")
            ):
                token = actual.path[len(expected.path) + 1 :]
                if token and "/" not in token:
                    return token
        raise CardEncodingMismatchError("encoded card link does not match configured URL")

    async def provision_unassigned(self, *, now: datetime | None = None) -> CardProvisioningResult:
        """Persist a legacy card token only when link-first dependencies are absent."""

        if (
            self._profile_repository is not None
            and self._public_access_link_repository is not None
            and self._card_link_assignment_repository is not None
        ):
            raise CardDirectOwnershipError(
                "card-local provisioning is retired; create and bind a profile link"
            )
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
        if (
            self._profile_repository is not None
            and self._public_access_link_repository is not None
            and self._card_link_assignment_repository is not None
        ):
            raise CardDirectOwnershipError(
                "direct card-user assignment is retired; bind a profile link"
            )
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
        """Return current cards visible through the user's card-purpose links."""

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
            links = await self._public_access_link_repository.list_by_profile_id(profile.id)
        except (InvalidIdentifierError, RepositoryError, PyMongoError, ValueError) as error:
            raise CardServiceUnavailableError("card service is unavailable") from error

        cards_by_id: dict[str, CardDocument] = {}
        link_status_by_card_id: dict[str, PublicAccessLinkStatus] = {}
        assignment_repository = self._card_link_assignment_repository
        for link in links:
            try:
                assignment = await assignment_repository.find_active_by_public_access_link_id(
                    link.id
                )
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
                or card.encoding_verified_at is None
                or not card.is_current
                or card.status in {CardStatus.LOST, CardStatus.REPLACED, CardStatus.VOID}
            ):
                continue
            cards_by_id[str(card.id)] = card
            link_status_by_card_id[str(card.id)] = link.status

        link_status_order = {
            PublicAccessLinkStatus.ACTIVE: 0,
            PublicAccessLinkStatus.DISABLED: 1,
            PublicAccessLinkStatus.PENDING: 2,
            PublicAccessLinkStatus.REVOKED: 3,
            PublicAccessLinkStatus.EXPIRED: 4,
        }
        ordered_cards = list(cards_by_id.values())

        def sort_key(card: CardDocument) -> tuple[int, float, str]:
            link_status = link_status_by_card_id.get(str(card.id))
            status_rank = link_status_order[link_status] if link_status is not None else 99
            return (
                status_rank,
                -(card.issued_at.timestamp() if card.issued_at is not None else 0),
                str(card.id),
            )

        return sorted(ordered_cards, key=sort_key)

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
        """Activate a user's issued card link without changing the physical card record."""

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
            card_id=card_id, user_id=user_id, require_ready=True
        )
        assignment = await self._load_card_assignment(card.id, allow_disabled=True)
        if assignment is None:
            raise CardNotFoundError("card does not exist")
        profile = await self._load_user_profile(user_id)
        link = await self._load_public_link(assignment.public_access_link_id)
        if link is None or link.profile_id != profile.id:
            raise CardNotFoundError("card does not exist")
        if link.status is PublicAccessLinkStatus.ACTIVE:
            return card
        if link.status not in {PublicAccessLinkStatus.PENDING, PublicAccessLinkStatus.DISABLED}:
            raise CardTerminalStateError("terminal cards cannot change state")
        link_repository = self._public_access_link_repository
        assert link_repository is not None

        async def mutate(session: Any | None = None) -> CardDocument:
            activated = await link_repository.activate_link(
                link_id=link.id, now=now, session=session
            )
            if activated is None:
                raise CardInvalidTransitionError("card lifecycle transition was not applied")
            return card

        if hasattr(self._repository, "with_transaction"):
            try:
                return await self._repository.with_transaction(mutate)
            except CardInvalidTransitionError:
                return await self._resolve_user_link_transition_race(
                    card_id=card.id,
                    user_id=user_id,
                    expected_status=PublicAccessLinkStatus.ACTIVE,
                )
            except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
                if isinstance(error, InvalidIdentifierError):
                    raise CardNotFoundError("card does not exist") from error
                raise CardServiceUnavailableError("card service is unavailable") from error
        link_repository = self._public_access_link_repository
        assert link_repository is not None
        try:
            activated = await link_repository.activate_link(link_id=link.id, now=now)
        except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
            if isinstance(error, InvalidIdentifierError):
                raise CardNotFoundError("card does not exist") from error
            raise CardServiceUnavailableError("card service is unavailable") from error
        if activated is not None:
            return card
        return await self._resolve_user_link_transition_race(
            card_id=card.id,
            user_id=user_id,
            expected_status=PublicAccessLinkStatus.ACTIVE,
        )

    async def disable_user_card(
        self,
        *,
        card_id: ObjectId | str,
        user_id: ObjectId | str,
        now: datetime | None = None,
    ) -> CardDocument:
        """Temporarily disable a user's card link without mutating the card record."""

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
        assignment = await self._load_card_assignment(card.id, allow_disabled=True)
        if assignment is None:
            raise CardNotFoundError("card does not exist")
        profile = await self._load_user_profile(user_id)
        link = await self._load_public_link(assignment.public_access_link_id)
        if link is None or link.profile_id != profile.id:
            raise CardNotFoundError("card does not exist")
        if link.status is PublicAccessLinkStatus.DISABLED:
            return card
        if link.status is PublicAccessLinkStatus.PENDING:
            raise CardInvalidTransitionError("card lifecycle transition is not allowed")
        if link.status in {PublicAccessLinkStatus.REVOKED, PublicAccessLinkStatus.EXPIRED}:
            raise CardTerminalStateError("terminal cards cannot change state")
        link_repository = self._public_access_link_repository
        assert link_repository is not None

        async def mutate(session: Any | None = None) -> CardDocument:
            disabled = await link_repository.disable_link(link_id=link.id, now=now, session=session)
            if disabled is None:
                raise CardInvalidTransitionError("card lifecycle transition was not applied")
            return card

        if hasattr(self._repository, "with_transaction"):
            try:
                return await self._repository.with_transaction(mutate)
            except CardInvalidTransitionError:
                return await self._resolve_user_link_transition_race(
                    card_id=card.id,
                    user_id=user_id,
                    expected_status=PublicAccessLinkStatus.DISABLED,
                )
            except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
                if isinstance(error, InvalidIdentifierError):
                    raise CardNotFoundError("card does not exist") from error
                raise CardServiceUnavailableError("card service is unavailable") from error
        link_repository = self._public_access_link_repository
        assert link_repository is not None
        try:
            disabled = await link_repository.disable_link(link_id=link.id, now=now)
        except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
            if isinstance(error, InvalidIdentifierError):
                raise CardNotFoundError("card does not exist") from error
            raise CardServiceUnavailableError("card service is unavailable") from error
        if disabled is not None:
            return card
        return await self._resolve_user_link_transition_race(
            card_id=card.id,
            user_id=user_id,
            expected_status=PublicAccessLinkStatus.DISABLED,
        )

    async def revoke_user_card_link(
        self,
        *,
        card_id: ObjectId | str,
        user_id: ObjectId | str,
        now: datetime | None = None,
    ) -> CardDocument:
        """Revoke the authenticated user's attached profile link permanently."""

        _card, link, _assignment = await self.describe_user_card(
            card_id=card_id,
            user_id=user_id,
        )
        if link is None:
            raise CardNotFoundError("card does not exist")
        if link.status is PublicAccessLinkStatus.REVOKED:
            return _card
        if link.status is PublicAccessLinkStatus.EXPIRED:
            raise CardTerminalStateError("terminal links cannot change state")
        repository = self._public_access_link_repository
        if repository is None:
            raise CardServiceUnavailableError("card service is unavailable")
        try:
            revoked = await repository.revoke_link(link_id=link.id, now=now)
        except (InvalidIdentifierError, RepositoryError, PyMongoError) as error:
            if isinstance(error, InvalidIdentifierError):
                raise CardNotFoundError("card does not exist") from error
            raise CardServiceUnavailableError("card service is unavailable") from error
        if revoked is None:
            raise CardInvalidTransitionError("card link revocation was not applied")
        return _card

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
        assignment = await self._load_card_assignment(card.id, allow_disabled=True)
        if assignment is None:
            raise CardNotFoundError("card does not exist")
        link = await self._load_public_link(assignment.public_access_link_id)
        if link is None or link.profile_id != profile.id:
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

    async def _load_ready_profile(self, user_id: ObjectId | str) -> Any:
        profile = await self._load_user_profile(user_id)
        if profile_state(profile) != "ready_to_publish":
            raise CardProfileNotReadyError("profile is not ready for card activation")
        return profile

    async def _load_ready_profile_by_id(self, profile_id: ObjectId | str) -> Any | None:
        repository = self._profile_repository
        if repository is None:
            raise CardServiceUnavailableError("card service is unavailable")
        try:
            profile = await repository.find_by_id(profile_id)
        except (RepositoryError, PyMongoError, ValueError) as error:
            raise CardServiceUnavailableError("card service is unavailable") from error
        if profile is None:
            return None
        if profile_state(profile) != "ready_to_publish":
            raise CardProfileNotReadyError("profile is not ready for card activation")
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

    async def _resolve_user_link_transition_race(
        self,
        *,
        card_id: ObjectId,
        user_id: ObjectId | str,
        expected_status: PublicAccessLinkStatus,
    ) -> CardDocument:
        card = await self._load_user_action_card(card_id=card_id, user_id=user_id)
        assignment = await self._load_card_assignment(card.id, allow_disabled=True)
        if assignment is None:
            raise CardNotFoundError("card does not exist")
        link = await self._load_public_link(assignment.public_access_link_id)
        if link is None:
            raise CardNotFoundError("card does not exist")
        if link.status is expected_status:
            return card
        if link.status in {PublicAccessLinkStatus.REVOKED, PublicAccessLinkStatus.EXPIRED}:
            raise CardTerminalStateError("card lifecycle transition is not allowed")
        raise CardInvalidTransitionError("card lifecycle transition was not applied")

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
        if (
            self._profile_repository is not None
            and self._public_access_link_repository is not None
            and self._card_link_assignment_repository is not None
        ):
            raise CardDirectOwnershipError(
                "direct card status mutation is retired; use attached-link lifecycle actions"
            )
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
                # A delivered card keeps its historical link binding. Losing the
                # card revokes access but never detaches the link.
                link = await self._load_public_link(
                    assignment.public_access_link_id, session=session
                )
                if link is not None and link.status in {
                    PublicAccessLinkStatus.PENDING,
                    PublicAccessLinkStatus.ACTIVE,
                    PublicAccessLinkStatus.DISABLED,
                }:
                    revoked = await link_repository.revoke_link(
                        link_id=link.id, now=now, session=session
                    )
                    if revoked is None:
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
        if old_card.status not in {
            CardStatus.ASSIGNED,
            CardStatus.ACTIVE,
            CardStatus.DISABLED,
        }:
            raise CardReplacementError("card is not eligible for replacement")

        use_link_first = (
            self._profile_repository is not None
            and self._card_link_assignment_repository is not None
            and self._public_access_link_repository is not None
        )
        owner_id = old_card.owner_id
        old_assignment: CardLinkAssignmentDocument | None = None
        old_link: PublicAccessLinkDocument | None = None
        replacement_profile: Any | None = None
        replacement_profile_id: ObjectId | None = None
        if use_link_first:
            old_assignment = await self._load_card_assignment(card_id, allow_disabled=True)
            if (
                old_assignment is None
                or old_assignment.status is not CardLinkAssignmentStatus.ACTIVE
            ):
                raise CardLinkNotBoundError("card must have an active profile-link binding")
            old_link = await self._load_public_link(old_assignment.public_access_link_id)
            if old_link is None or old_link.purpose is not PublicLinkPurpose.CARD:
                raise CardProfileLinkInvalidError("card link does not exist")
            replacement_profile = await self._load_ready_profile_by_id(old_link.profile_id)
            if replacement_profile is None:
                raise CardProfileLinkInvalidError("card link is not attached to a ready profile")
            assert replacement_profile is not None
            replacement_profile_id = replacement_profile.id
        elif owner_id is None:
            raise CardReplacementError("card is not eligible for replacement")

        timestamp = now or utc_now()
        for _ in range(_MAX_IDENTITY_RETRIES):
            public_token = self._token_generator()

            async def operation(
                session: Any, public_token: str = public_token
            ) -> CardProvisioningResult:
                new_card = await self._repository.create_unassigned_card(
                    serial=self._serial_generator(),
                    token_hash=None if use_link_first else hash_public_token(public_token),
                    replaces_card_id=old_card.id,
                    now=timestamp,
                    session=session,
                )
                assignment_repository = self._card_link_assignment_repository
                link_repository = self._public_access_link_repository
                if use_link_first:
                    assert assignment_repository is not None
                    assert link_repository is not None
                    mark_link_bound = getattr(self._repository, "mark_link_bound", None)
                    if not callable(mark_link_bound):
                        raise CardServiceUnavailableError("card service is unavailable")
                    assigned = await cast(
                        Callable[..., Awaitable[CardDocument | None]], mark_link_bound
                    )(card_id=new_card.id, now=timestamp, session=session)
                    if assigned is None:
                        raise CardReplacementError("replacement card binding failed")
                    assert replacement_profile_id is not None
                    access_link = await link_repository.create_link(
                        profile_id=replacement_profile_id,
                        purpose=PublicLinkPurpose.CARD,
                        token_hash=hash_public_token(public_token),
                        label="Card access",
                        status=PublicAccessLinkStatus.PENDING,
                        now=timestamp,
                        session=session,
                    )
                    await assignment_repository.attach_link(
                        card_id=assigned.id,
                        public_access_link_id=access_link.id,
                        attached_by_admin_id=None,
                        now=timestamp,
                        session=session,
                    )
                    mark_replaced_without_owner = getattr(
                        self._repository, "mark_replaced_without_owner", None
                    )
                    if callable(mark_replaced_without_owner):
                        replaced = await cast(
                            Callable[..., Awaitable[CardDocument | None]],
                            mark_replaced_without_owner,
                        )(card_id=old_card.id, now=timestamp, session=session)
                    else:
                        replaced = await self._repository.transition_status(
                            card_id=old_card.id,
                            from_statuses={
                                CardStatus.ASSIGNED,
                                CardStatus.ACTIVE,
                                CardStatus.DISABLED,
                            },
                            to_status=CardStatus.REPLACED,
                            now=timestamp,
                            session=session,
                        )
                else:
                    assert owner_id is not None
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
                if assignment_repository is not None and link_repository is not None:
                    link_to_revoke = old_link
                    if link_to_revoke is None:
                        assignment = await self._load_card_assignment(
                            old_card.id, allow_disabled=True, session=session
                        )
                        if assignment is not None:
                            link_to_revoke = await self._load_public_link(
                                assignment.public_access_link_id, session=session
                            )
                    if link_to_revoke is not None and link_to_revoke.status in {
                        PublicAccessLinkStatus.PENDING,
                        PublicAccessLinkStatus.ACTIVE,
                        PublicAccessLinkStatus.DISABLED,
                    }:
                        revoked = await link_repository.revoke_link(
                            link_id=link_to_revoke.id, now=timestamp, session=session
                        )
                        if revoked is None:
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
