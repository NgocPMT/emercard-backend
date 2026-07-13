from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pydantic import SecretStr

from emercard.api.user_card_routes import get_user_card_service
from emercard.core.config import Settings
from emercard.db.repositories import InvalidIdentifierError, RepositoryError
from emercard.main import create_app
from emercard.modules.auth.security import hash_password
from emercard.modules.card_link_assignments import (
    CardLinkAssignmentDocument,
    CardLinkAssignmentStatus,
)
from emercard.modules.cards import (
    CardDocument,
    CardEncodingNotVerifiedError,
    CardInvalidTransitionError,
    CardNotFoundError,
    CardNotIssuedError,
    CardProfileNotReadyError,
    CardService,
    CardServiceUnavailableError,
    CardStatus,
    CardTerminalStateError,
    generate_public_token,
    generate_serial,
    hash_public_token,
)
from emercard.modules.profiles.models import ProfileDocument
from emercard.modules.public_links import (
    PublicAccessLinkDocument,
    PublicAccessLinkStatus,
    PublicLinkPurpose,
)
from emercard.modules.users.models import UserDocument
from tests.conftest import FakeDatabase

NOW = datetime(2026, 1, 1, tzinfo=UTC)
OWNER_ID = ObjectId("507f1f77bcf86cd799439011")
ADMIN_ID = ObjectId("507f1f77bcf86cd799439013")


class UserRepository:
    def __init__(self, user: UserDocument) -> None:
        self.user = user

    async def find_by_email(self, email: str) -> UserDocument | None:
        return self.user if self.user.email == email else None

    async def find_by_id(self, user_id: str) -> UserDocument | None:
        return self.user if str(self.user.id) == user_id else None


class ProfileRepository:
    def __init__(self, profile: ProfileDocument | None) -> None:
        self.profile = profile
        self.failure: Exception | None = None

    async def find_by_user_id(self, user_id: ObjectId | str) -> ProfileDocument | None:
        if self.failure is not None:
            raise self.failure
        if self.profile is None:
            return None
        return self.profile if str(self.profile.user_id) == str(user_id) else None


class CardRepository:
    def __init__(self, cards: list[CardDocument]) -> None:
        self.cards = {card.id: card for card in cards}

    async def find_by_id(self, card_id: ObjectId | str) -> CardDocument | None:
        try:
            return self.cards.get(ObjectId(card_id))
        except Exception as error:
            raise InvalidIdentifierError("invalid card identifier") from error

    async def list_user_controllable(self, user_id: ObjectId | str) -> list[CardDocument]:
        cards = [
            card
            for card in self.cards.values()
            if card.owner_id == ObjectId(user_id)
            and card.is_current
            and card.issued_at is not None
            and card.status in {CardStatus.ASSIGNED, CardStatus.ACTIVE, CardStatus.DISABLED}
        ]
        status_order = {
            CardStatus.ACTIVE: 0,
            CardStatus.DISABLED: 1,
            CardStatus.ASSIGNED: 2,
        }
        return sorted(cards, key=lambda item: (status_order[item.status], str(item.id)))

    async def find_user_controllable(
        self, *, card_id: ObjectId | str, user_id: ObjectId | str
    ) -> CardDocument | None:
        card = await self.find_by_id(card_id)
        if card is None:
            return None
        if (
            card.owner_id != ObjectId(user_id)
            or not card.is_current
            or card.issued_at is None
            or card.status not in {CardStatus.ASSIGNED, CardStatus.ACTIVE, CardStatus.DISABLED}
        ):
            return None
        return card

    async def activate_for_user(
        self, *, card_id: ObjectId | str, user_id: ObjectId | str, now: datetime | None = None
    ) -> CardDocument | None:
        card = await self.find_by_id(card_id)
        if (
            card is None
            or card.owner_id != ObjectId(user_id)
            or not card.is_current
            or card.issued_at is None
            or card.encoding_verified_at is None
            or card.status not in {CardStatus.ASSIGNED, CardStatus.DISABLED}
        ):
            return None
        updated = card.model_copy(
            update={
                "status": CardStatus.ACTIVE,
                "activated_at": now or NOW,
                "disabled_at": None,
                "updated_at": now or NOW,
            }
        )
        self.cards[updated.id] = updated
        return updated

    async def disable_for_user(
        self, *, card_id: ObjectId | str, user_id: ObjectId | str, now: datetime | None = None
    ) -> CardDocument | None:
        card = await self.find_by_id(card_id)
        if (
            card is None
            or card.owner_id != ObjectId(user_id)
            or not card.is_current
            or card.issued_at is None
            or card.status is not CardStatus.ACTIVE
        ):
            return None
        updated = card.model_copy(
            update={
                "status": CardStatus.DISABLED,
                "disabled_at": now or NOW,
                "updated_at": now or NOW,
            }
        )
        self.cards[updated.id] = updated
        return updated

    async def transition_status(
        self,
        *,
        card_id: ObjectId | str,
        from_statuses: set[CardStatus],
        to_status: CardStatus,
        owner_id: ObjectId | str | None = None,
        now: datetime | None = None,
        session: object | None = None,
    ) -> CardDocument | None:
        del session
        card = await self.find_by_id(card_id)
        if card is None or card.status not in from_statuses:
            return None
        if owner_id is not None and card.owner_id != ObjectId(owner_id):
            return None
        timestamp = now or NOW
        update = {"status": to_status, "updated_at": timestamp}
        if to_status is CardStatus.ACTIVE:
            update["activated_at"] = timestamp
            update["disabled_at"] = None
        elif to_status is CardStatus.DISABLED:
            update["disabled_at"] = timestamp
        elif to_status is CardStatus.LOST:
            update["lost_at"] = timestamp
            update["is_current"] = False
        elif to_status is CardStatus.REPLACED:
            update["replaced_at"] = timestamp
            update["is_current"] = False
        updated = card.model_copy(update=update)
        self.cards[updated.id] = updated
        return updated

    async def create_unassigned_card(
        self,
        *,
        serial: str,
        token_hash: str | None,
        replaces_card_id: ObjectId | str | None = None,
        now: datetime | None = None,
        session: object | None = None,
    ) -> CardDocument:
        del session
        timestamp = now or NOW
        card = CardDocument.model_validate(
            {
                "_id": ObjectId(),
                "serial": serial,
                "owner_id": None,
                "token_hash": token_hash,
                "provisioned_at": timestamp if token_hash is not None else None,
                "replaces_card_id": replaces_card_id,
                "status": CardStatus.UNASSIGNED,
                "is_current": False,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
        self.cards[card.id] = card
        return card

    async def assign_to_user(
        self,
        *,
        card_id: ObjectId | str,
        user_id: ObjectId | str,
        now: datetime | None = None,
        session: object | None = None,
    ) -> CardDocument | None:
        del session
        card = await self.find_by_id(card_id)
        if card is None or card.status is not CardStatus.UNASSIGNED or card.owner_id is not None:
            return None
        timestamp = now or NOW
        updated = card.model_copy(
            update={
                "owner_id": ObjectId(user_id),
                "status": CardStatus.ASSIGNED,
                "is_current": True,
                "assigned_at": timestamp,
                "updated_at": timestamp,
            }
        )
        self.cards[updated.id] = updated
        return updated

    async def issue(
        self,
        *,
        card_id: ObjectId | str,
        admin_id: ObjectId | str,
        now: datetime | None = None,
        session: object | None = None,
    ) -> CardDocument | None:
        del admin_id, session
        card = await self.find_by_id(card_id)
        if card is None or card.status is not CardStatus.ASSIGNED or card.issued_at is not None:
            return None
        timestamp = now or NOW
        updated = card.model_copy(update={"issued_at": timestamp, "updated_at": timestamp})
        self.cards[updated.id] = updated
        return updated

    async def confirm_encoding_without_token_hash(
        self,
        *,
        card_id: ObjectId | str,
        admin_id: ObjectId | str,
        now: datetime | None = None,
        session: object | None = None,
    ) -> CardDocument | None:
        del session
        card = await self.find_by_id(card_id)
        if card is None or card.status is not CardStatus.UNASSIGNED or card.issued_at is not None:
            return None
        updated = card.model_copy(
            update={
                "encoding_verified_at": now or NOW,
                "encoded_by_admin_id": ObjectId(admin_id),
                "updated_at": now or NOW,
            }
        )
        self.cards[updated.id] = updated
        return updated

    async def void_before_issue(
        self,
        *,
        card_id: ObjectId | str,
        now: datetime | None = None,
        session: object | None = None,
    ) -> CardDocument | None:
        del session
        card = await self.find_by_id(card_id)
        if card is None or card.status not in {CardStatus.UNASSIGNED, CardStatus.ASSIGNED}:
            return None
        timestamp = now or NOW
        updated = card.model_copy(
            update={
                "status": CardStatus.VOID,
                "is_current": False,
                "assigned_at": None,
                "updated_at": timestamp,
            }
        )
        self.cards[updated.id] = updated
        return updated

    async def mark_replaced(
        self,
        *,
        card_id: ObjectId | str,
        owner_id: ObjectId | str,
        now: datetime | None = None,
        session: object | None = None,
    ) -> CardDocument | None:
        del session
        card = await self.find_by_id(card_id)
        if card is None or card.owner_id != ObjectId(owner_id):
            return None
        return await self.transition_status(
            card_id=card_id,
            from_statuses={CardStatus.ASSIGNED, CardStatus.ACTIVE, CardStatus.DISABLED},
            to_status=CardStatus.REPLACED,
            owner_id=owner_id,
            now=now,
        )

    async def link_replacement(
        self,
        *,
        card_id: ObjectId | str,
        replacement_card_id: ObjectId | str,
        now: datetime | None = None,
        session: object | None = None,
    ) -> CardDocument | None:
        del session
        card = await self.find_by_id(card_id)
        if card is None or card.status is not CardStatus.REPLACED:
            return None
        updated = card.model_copy(
            update={"replacement_card_id": ObjectId(replacement_card_id), "updated_at": now or NOW}
        )
        self.cards[updated.id] = updated
        return updated

    async def with_transaction(self, operation):
        return await operation(None)


class PublicAccessLinkRepository:
    def __init__(self, links: list[PublicAccessLinkDocument]) -> None:
        self.links = {link.id: link for link in links}

    async def find_by_id(
        self, link_id: ObjectId | str, *, session: object | None = None
    ) -> PublicAccessLinkDocument | None:
        del session
        return self.links.get(ObjectId(link_id))

    async def list_by_profile_id(
        self,
        profile_id: ObjectId | str,
        *,
        purpose: PublicLinkPurpose | None = None,
        session: object | None = None,
    ) -> list[PublicAccessLinkDocument]:
        del session
        links = [link for link in self.links.values() if link.profile_id == ObjectId(profile_id)]
        if purpose is not None:
            links = [link for link in links if link.purpose is purpose]
        return links

    async def activate_link(
        self,
        *,
        link_id: ObjectId | str,
        now: datetime | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument | None:
        del now, session
        link = self.links.get(ObjectId(link_id))
        if link is None or link.status is not PublicAccessLinkStatus.DISABLED:
            return None
        updated = link.model_copy(
            update={"status": PublicAccessLinkStatus.ACTIVE, "disabled_at": None}
        )
        self.links[updated.id] = updated
        return updated

    async def disable_link(
        self,
        *,
        link_id: ObjectId | str,
        now: datetime | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument | None:
        del session
        link = self.links.get(ObjectId(link_id))
        if link is None or link.status is not PublicAccessLinkStatus.ACTIVE:
            return None
        updated = link.model_copy(
            update={"status": PublicAccessLinkStatus.DISABLED, "disabled_at": now or NOW}
        )
        self.links[updated.id] = updated
        return updated

    async def create_link(
        self,
        *,
        profile_id: ObjectId | str,
        purpose: PublicLinkPurpose,
        token_hash: str,
        label: str | None = None,
        status: PublicAccessLinkStatus = PublicAccessLinkStatus.ACTIVE,
        created_by: ObjectId | str | None = None,
        now: datetime | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument:
        del created_by, session
        link = PublicAccessLinkDocument.model_validate(
            {
                "_id": ObjectId(),
                "profile_id": ObjectId(profile_id),
                "purpose": purpose,
                "label": label,
                "token_hash": token_hash,
                "status": status,
                "created_at": now or NOW,
                "updated_at": now or NOW,
                "activated_at": (now or NOW) if status is PublicAccessLinkStatus.ACTIVE else None,
                "disabled_at": (now or NOW) if status is PublicAccessLinkStatus.DISABLED else None,
                "revoked_at": None,
            }
        )
        self.links[link.id] = link
        return link

    async def revoke_link(
        self,
        *,
        link_id: ObjectId | str,
        now: datetime | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument | None:
        del now, session
        link = self.links.get(ObjectId(link_id))
        if link is None:
            return None
        updated = link.model_copy(update={"status": PublicAccessLinkStatus.REVOKED})
        self.links[updated.id] = updated
        return updated


class CardLinkAssignmentRepository:
    def __init__(self, assignments: list[CardLinkAssignmentDocument]) -> None:
        self.assignments = {assignment.id: assignment for assignment in assignments}

    async def list_by_card_id(
        self, card_id: ObjectId | str, *, session: object | None = None
    ) -> list[CardLinkAssignmentDocument]:
        del session
        return [
            assignment
            for assignment in self.assignments.values()
            if assignment.card_id == ObjectId(card_id)
        ]

    async def find_active_by_card_id(
        self, card_id: ObjectId | str, *, session: object | None = None
    ) -> CardLinkAssignmentDocument | None:
        del session
        for assignment in self.assignments.values():
            if (
                assignment.card_id == ObjectId(card_id)
                and assignment.status is CardLinkAssignmentStatus.ACTIVE
            ):
                return assignment
        return None

    async def find_active_by_public_access_link_id(
        self, public_access_link_id: ObjectId | str, *, session: object | None = None
    ) -> CardLinkAssignmentDocument | None:
        del session
        for assignment in self.assignments.values():
            if (
                assignment.public_access_link_id == ObjectId(public_access_link_id)
                and assignment.status is CardLinkAssignmentStatus.ACTIVE
            ):
                return assignment
        return None

    async def list_by_public_access_link_id(
        self, public_access_link_id: ObjectId | str, *, session: object | None = None
    ) -> list[CardLinkAssignmentDocument]:
        del session
        return [
            assignment
            for assignment in self.assignments.values()
            if assignment.public_access_link_id == ObjectId(public_access_link_id)
        ]

    async def activate_assignment(
        self,
        *,
        assignment_id: ObjectId | str,
        now: datetime | None = None,
        session: object | None = None,
    ) -> CardLinkAssignmentDocument | None:
        del now, session
        assignment = self.assignments.get(ObjectId(assignment_id))
        if assignment is None or assignment.status is not CardLinkAssignmentStatus.DISABLED:
            return None
        updated = assignment.model_copy(update={"status": CardLinkAssignmentStatus.ACTIVE})
        self.assignments[updated.id] = updated
        return updated

    async def attach_link(
        self,
        *,
        card_id: ObjectId | str,
        public_access_link_id: ObjectId | str,
        attached_by_admin_id: ObjectId | str | None = None,
        now: datetime | None = None,
        session: object | None = None,
    ) -> CardLinkAssignmentDocument:
        del attached_by_admin_id, session
        assignment = CardLinkAssignmentDocument.model_validate(
            {
                "_id": ObjectId(),
                "card_id": ObjectId(card_id),
                "public_access_link_id": ObjectId(public_access_link_id),
                "status": CardLinkAssignmentStatus.ACTIVE,
                "attached_at": now or NOW,
                "updated_at": now or NOW,
            }
        )
        self.assignments[assignment.id] = assignment
        return assignment

    async def deactivate_assignment(
        self,
        *,
        assignment_id: ObjectId | str,
        disabled_by_admin_id: ObjectId | str | None = None,
        now: datetime | None = None,
        session: object | None = None,
    ) -> CardLinkAssignmentDocument | None:
        del disabled_by_admin_id, session
        assignment = self.assignments.get(ObjectId(assignment_id))
        if assignment is None or assignment.status is not CardLinkAssignmentStatus.ACTIVE:
            return None
        updated = assignment.model_copy(
            update={"status": CardLinkAssignmentStatus.DISABLED, "disabled_at": now or NOW}
        )
        self.assignments[updated.id] = updated
        return updated

    async def detach_assignment(
        self,
        *,
        assignment_id: ObjectId | str,
        detached_by_admin_id: ObjectId | str | None = None,
        detach_reason: str | None = None,
        now: datetime | None = None,
        session: object | None = None,
    ) -> CardLinkAssignmentDocument | None:
        del session
        assignment = self.assignments.get(ObjectId(assignment_id))
        if assignment is None or assignment.status not in {
            CardLinkAssignmentStatus.ACTIVE,
            CardLinkAssignmentStatus.DISABLED,
        }:
            return None
        updated = assignment.model_copy(
            update={
                "status": CardLinkAssignmentStatus.DETACHED,
                "detached_at": now or NOW,
                "detached_by_admin_id": detached_by_admin_id,
                "detach_reason": detach_reason,
            }
        )
        self.assignments[updated.id] = updated
        return updated


def user() -> UserDocument:
    return UserDocument(
        _id=OWNER_ID,
        email="person@example.com",
        password_hash=hash_password("password-123"),
        role="user",
        created_at=NOW,
        updated_at=NOW,
    )


def profile(*, ready: bool) -> ProfileDocument:
    return ProfileDocument.model_validate(
        {
            "_id": ObjectId(),
            "user_id": OWNER_ID,
            "display_name": "Alex" if ready else None,
            "birth_year": 1995 if ready else None,
            "gender": "male" if ready else None,
            "blood_type": "O+" if ready else None,
            "critical_allergies": [],
            "important_conditions": [],
            "critical_medications": [],
            "emergency_contacts": (
                [{"name": "Sam", "relationship": "Family", "phone": "0900000000"}] if ready else []
            ),
            "created_at": NOW,
            "updated_at": NOW,
        }
    )


def card(*, status: CardStatus) -> CardDocument:
    return CardDocument(
        _id=ObjectId(),
        serial=generate_serial(),
        owner_id=OWNER_ID,
        token_hash=hash_public_token(generate_public_token()),
        provisioned_at=NOW,
        encoding_verified_at=NOW,
        encoded_by_admin_id=ADMIN_ID,
        assigned_at=NOW,
        activated_at=NOW if status is CardStatus.ACTIVE else None,
        disabled_at=NOW if status is CardStatus.DISABLED else None,
        issued_at=NOW,
        status=status,
        is_current=True,
        created_at=NOW,
        updated_at=NOW,
    )


def settings() -> Settings:
    return Settings(
        environment="test",
        auth_secret=SecretStr("test-auth-secret-012345678901234567890"),
        cors_origins=["http://localhost:4321"],
    )


@pytest.mark.asyncio
async def test_user_card_service_requires_ready_profile_and_is_idempotent() -> None:
    assigned = card(status=CardStatus.ASSIGNED)
    repository = CardRepository([assigned])
    profiles = ProfileRepository(profile(ready=False))
    service = CardService(repository, UserRepository(user()), profile_repository=profiles)

    with pytest.raises(CardProfileNotReadyError):
        await service.activate_user_card(card_id=assigned.id, user_id=OWNER_ID, now=NOW)

    profiles.profile = profile(ready=True)
    activated = await service.activate_user_card(card_id=assigned.id, user_id=OWNER_ID, now=NOW)
    repeated = await service.activate_user_card(card_id=assigned.id, user_id=OWNER_ID)
    disabled = card(status=CardStatus.DISABLED)
    repository.cards[disabled.id] = disabled
    reactivated = await service.activate_user_card(card_id=disabled.id, user_id=OWNER_ID, now=NOW)

    assert activated.status is CardStatus.ACTIVE
    assert repeated.activated_at == NOW
    assert reactivated.status is CardStatus.ACTIVE
    assert reactivated.disabled_at is None


@pytest.mark.asyncio
async def test_user_card_service_disables_one_card_without_changing_sibling() -> None:
    active = card(status=CardStatus.ACTIVE)
    sibling = card(status=CardStatus.ACTIVE)
    repository = CardRepository([active, sibling])
    service = CardService(
        repository,
        UserRepository(user()),
        profile_repository=ProfileRepository(profile(ready=True)),
    )

    disabled = await service.disable_user_card(card_id=active.id, user_id=OWNER_ID, now=NOW)

    assert disabled.status is CardStatus.DISABLED
    assert repository.cards[sibling.id].status is CardStatus.ACTIVE


@pytest.mark.asyncio
async def test_user_card_service_rejects_unverified_and_terminal_cards() -> None:
    unverified = card(status=CardStatus.ASSIGNED).model_copy(
        update={"encoding_verified_at": None, "encoded_by_admin_id": None}
    )
    terminal = CardDocument(
        _id=ObjectId(),
        serial=generate_serial(),
        owner_id=OWNER_ID,
        token_hash=hash_public_token(generate_public_token()),
        provisioned_at=NOW,
        encoding_verified_at=NOW,
        encoded_by_admin_id=ADMIN_ID,
        assigned_at=NOW,
        issued_at=NOW,
        status=CardStatus.LOST,
        is_current=False,
        lost_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    repository = CardRepository([unverified, terminal])
    service = CardService(
        repository,
        UserRepository(user()),
        profile_repository=ProfileRepository(profile(ready=True)),
    )

    with pytest.raises(CardEncodingNotVerifiedError):
        await service.activate_user_card(card_id=unverified.id, user_id=OWNER_ID)
    with pytest.raises(CardTerminalStateError):
        await service.activate_user_card(card_id=terminal.id, user_id=OWNER_ID)


@pytest.mark.asyncio
async def test_user_card_service_maps_profile_failures_safely() -> None:
    repository = CardRepository([card(status=CardStatus.ASSIGNED)])
    profiles = ProfileRepository(profile(ready=True))
    profiles.failure = RepositoryError("medical database details")
    service = CardService(repository, UserRepository(user()), profile_repository=profiles)

    with pytest.raises(CardServiceUnavailableError) as error:
        await service.activate_user_card(card_id=next(iter(repository.cards)), user_id=OWNER_ID)

    assert "medical database details" not in str(error.value)


@pytest.mark.asyncio
async def test_user_card_service_classifies_hidden_action_resources() -> None:
    unissued = card(status=CardStatus.ASSIGNED).model_copy(
        update={"issued_at": None, "issued_by_admin_id": None}
    )
    foreign = card(status=CardStatus.ASSIGNED).model_copy(
        update={"owner_id": ObjectId("507f1f77bcf86cd799439099")}
    )
    missing_profile_card = card(status=CardStatus.ASSIGNED)
    repository = CardRepository([unissued, foreign, missing_profile_card])
    service = CardService(
        repository,
        UserRepository(user()),
        profile_repository=ProfileRepository(None),
    )

    with pytest.raises(CardNotIssuedError):
        await service.activate_user_card(card_id=unissued.id, user_id=OWNER_ID)
    with pytest.raises(CardNotFoundError):
        await service.activate_user_card(card_id=foreign.id, user_id=OWNER_ID)
    with pytest.raises(CardNotFoundError):
        await service.activate_user_card(card_id="not-an-object-id", user_id=OWNER_ID)
    with pytest.raises(CardServiceUnavailableError):
        await service.activate_user_card(card_id=missing_profile_card.id, user_id=OWNER_ID)


@pytest.mark.asyncio
async def test_user_card_visibility_filters_and_orders_cards() -> None:
    active = card(status=CardStatus.ACTIVE)
    disabled = card(status=CardStatus.DISABLED)
    assigned = card(status=CardStatus.ASSIGNED)
    unassigned = assigned.model_copy(
        update={
            "id": ObjectId(),
            "owner_id": None,
            "status": CardStatus.UNASSIGNED,
            "is_current": False,
        }
    )
    unissued = assigned.model_copy(
        update={"id": ObjectId(), "issued_at": None, "issued_by_admin_id": None}
    )
    stale = active.model_copy(update={"id": ObjectId(), "is_current": False})
    foreign = active.model_copy(
        update={"id": ObjectId(), "owner_id": ObjectId("507f1f77bcf86cd799439099")}
    )
    lost = active.model_copy(
        update={"id": ObjectId(), "status": CardStatus.LOST, "is_current": False}
    )
    replaced = active.model_copy(
        update={"id": ObjectId(), "status": CardStatus.REPLACED, "is_current": False}
    )
    void = unassigned.model_copy(update={"id": ObjectId(), "status": CardStatus.VOID})
    repository = CardRepository(
        [unassigned, unissued, stale, foreign, lost, replaced, void, assigned, disabled, active]
    )
    service = CardService(repository, UserRepository(user()))

    visible = await service.list_user_cards(user_id=OWNER_ID)

    assert [item.status for item in visible] == [
        CardStatus.ACTIVE,
        CardStatus.DISABLED,
        CardStatus.ASSIGNED,
    ]
    for hidden in [unassigned, unissued, stale, foreign, lost, replaced, void]:
        with pytest.raises(CardNotFoundError):
            await service.get_user_card(card_id=hidden.id, user_id=OWNER_ID)
    for visible_card in visible:
        assert (
            await service.get_user_card(card_id=visible_card.id, user_id=OWNER_ID)
        ).id == visible_card.id


@pytest.mark.asyncio
async def test_user_card_service_uses_link_profile_for_authorization() -> None:
    linked_profile = profile(ready=True)
    foreign_owner = ObjectId("507f1f77bcf86cd799439099")
    linked_card = card(status=CardStatus.ASSIGNED).model_copy(
        update={
            "owner_id": foreign_owner,
            "issued_at": NOW,
            "status": CardStatus.ASSIGNED,
            "is_current": True,
        }
    )
    linked_card = linked_card.model_copy(update={"encoding_verified_at": NOW})
    public_link = PublicAccessLinkDocument.model_validate(
        {
            "_id": ObjectId(),
            "profile_id": linked_profile.id,
            "purpose": PublicLinkPurpose.CARD,
            "label": "Card access",
            "token_hash": hash_public_token(generate_public_token()),
            "status": PublicAccessLinkStatus.ACTIVE,
            "created_by": ADMIN_ID,
            "created_at": NOW,
            "updated_at": NOW,
            "activated_at": NOW,
        }
    )
    assignment = CardLinkAssignmentDocument.model_validate(
        {
            "_id": ObjectId(),
            "card_id": linked_card.id,
            "public_access_link_id": public_link.id,
            "status": CardLinkAssignmentStatus.ACTIVE,
            "attached_at": NOW,
            "updated_at": NOW,
            "attached_by_admin_id": ADMIN_ID,
        }
    )
    repository = CardRepository([linked_card])
    service = CardService(
        repository,
        UserRepository(user()),
        profile_repository=ProfileRepository(linked_profile),
        public_access_link_repository=PublicAccessLinkRepository([public_link]),
        card_link_assignment_repository=CardLinkAssignmentRepository([assignment]),
    )

    visible = await service.list_user_cards(user_id=OWNER_ID)
    detail = await service.get_user_card(card_id=linked_card.id, user_id=OWNER_ID)
    activated = await service.activate_user_card(card_id=linked_card.id, user_id=OWNER_ID, now=NOW)

    assert [item.id for item in visible] == [linked_card.id]
    assert detail.id == linked_card.id
    assert activated.status is CardStatus.ACTIVE
    assert repository.cards[linked_card.id].status is CardStatus.ACTIVE
    assert repository.cards[linked_card.id].owner_id == foreign_owner


@pytest.mark.asyncio
async def test_user_card_service_ignores_disabled_links_for_authorization() -> None:
    linked_profile = profile(ready=True)
    linked_card = card(status=CardStatus.ASSIGNED).model_copy(
        update={"issued_at": NOW, "status": CardStatus.ASSIGNED, "is_current": True}
    )
    disabled_link = PublicAccessLinkDocument.model_validate(
        {
            "_id": ObjectId(),
            "profile_id": linked_profile.id,
            "purpose": PublicLinkPurpose.CARD,
            "label": "Card access",
            "token_hash": hash_public_token(generate_public_token()),
            "status": PublicAccessLinkStatus.DISABLED,
            "created_by": ADMIN_ID,
            "created_at": NOW,
            "updated_at": NOW,
            "disabled_at": NOW,
        }
    )
    assignment = CardLinkAssignmentDocument.model_validate(
        {
            "_id": ObjectId(),
            "card_id": linked_card.id,
            "public_access_link_id": disabled_link.id,
            "status": CardLinkAssignmentStatus.ACTIVE,
            "attached_at": NOW,
            "updated_at": NOW,
            "attached_by_admin_id": ADMIN_ID,
        }
    )
    service = CardService(
        CardRepository([linked_card]),
        UserRepository(user()),
        profile_repository=ProfileRepository(linked_profile),
        public_access_link_repository=PublicAccessLinkRepository([disabled_link]),
        card_link_assignment_repository=CardLinkAssignmentRepository([assignment]),
    )

    assert await service.list_user_cards(user_id=OWNER_ID) == []
    with pytest.raises(CardNotFoundError):
        await service.get_user_card(card_id=linked_card.id, user_id=OWNER_ID)


@pytest.mark.asyncio
async def test_user_card_service_issues_linked_card_and_activates_access_state() -> None:
    linked_card = card(status=CardStatus.ASSIGNED).model_copy(
        update={"issued_at": None, "activated_at": None, "status": CardStatus.ASSIGNED}
    )
    disabled_link = PublicAccessLinkDocument.model_validate(
        {
            "_id": ObjectId(),
            "profile_id": OWNER_ID,
            "purpose": PublicLinkPurpose.CARD,
            "label": "Card access",
            "token_hash": hash_public_token(generate_public_token()),
            "status": PublicAccessLinkStatus.DISABLED,
            "created_by": ADMIN_ID,
            "created_at": NOW,
            "updated_at": NOW,
            "disabled_at": NOW,
        }
    )
    disabled_assignment = CardLinkAssignmentDocument.model_validate(
        {
            "_id": ObjectId(),
            "card_id": linked_card.id,
            "public_access_link_id": disabled_link.id,
            "status": CardLinkAssignmentStatus.DISABLED,
            "attached_at": NOW,
            "updated_at": NOW,
            "attached_by_admin_id": ADMIN_ID,
            "disabled_at": NOW,
            "disabled_by_admin_id": ADMIN_ID,
        }
    )
    repository = CardRepository([linked_card])
    link_repository = PublicAccessLinkRepository([disabled_link])
    assignment_repository = CardLinkAssignmentRepository([disabled_assignment])
    service = CardService(
        repository,
        UserRepository(user()),
        public_access_link_repository=link_repository,
        card_link_assignment_repository=assignment_repository,
    )

    issued = await service.issue(card_id=linked_card.id, admin_id=ADMIN_ID, now=NOW)

    assert issued.issued_at == NOW
    assert repository.cards[linked_card.id].issued_at == NOW
    assert link_repository.links[disabled_link.id].status is PublicAccessLinkStatus.ACTIVE
    assert (
        assignment_repository.assignments[disabled_assignment.id].status
        is CardLinkAssignmentStatus.ACTIVE
    )


@pytest.mark.asyncio
async def test_user_card_service_marks_lost_and_replaces_card_access_state() -> None:
    lost_card = card(status=CardStatus.ACTIVE)
    lost_link = PublicAccessLinkDocument.model_validate(
        {
            "_id": ObjectId(),
            "profile_id": OWNER_ID,
            "purpose": PublicLinkPurpose.CARD,
            "label": "Card access",
            "token_hash": hash_public_token(generate_public_token()),
            "status": PublicAccessLinkStatus.ACTIVE,
            "created_by": ADMIN_ID,
            "created_at": NOW,
            "updated_at": NOW,
            "activated_at": NOW,
        }
    )
    lost_assignment = CardLinkAssignmentDocument.model_validate(
        {
            "_id": ObjectId(),
            "card_id": lost_card.id,
            "public_access_link_id": lost_link.id,
            "status": CardLinkAssignmentStatus.ACTIVE,
            "attached_at": NOW,
            "updated_at": NOW,
            "attached_by_admin_id": ADMIN_ID,
        }
    )
    lost_repository = CardRepository([lost_card])
    lost_link_repository = PublicAccessLinkRepository([lost_link])
    lost_assignment_repository = CardLinkAssignmentRepository([lost_assignment])
    lost_service = CardService(
        lost_repository,
        UserRepository(user()),
        public_access_link_repository=lost_link_repository,
        card_link_assignment_repository=lost_assignment_repository,
    )

    marked_lost = await lost_service.mark_lost(card_id=lost_card.id, now=NOW)

    assert marked_lost.status is CardStatus.LOST
    assert lost_repository.cards[lost_card.id].status is CardStatus.LOST
    assert lost_link_repository.links[lost_link.id].status is PublicAccessLinkStatus.DISABLED
    assert (
        lost_assignment_repository.assignments[lost_assignment.id].status
        is CardLinkAssignmentStatus.DISABLED
    )

    replacement_card = card(status=CardStatus.ACTIVE)
    replacement_link = PublicAccessLinkDocument.model_validate(
        {
            "_id": ObjectId(),
            "profile_id": OWNER_ID,
            "purpose": PublicLinkPurpose.CARD,
            "label": "Replacement card access",
            "token_hash": hash_public_token(generate_public_token()),
            "status": PublicAccessLinkStatus.ACTIVE,
            "created_by": ADMIN_ID,
            "created_at": NOW,
            "updated_at": NOW,
            "activated_at": NOW,
        }
    )
    replacement_assignment = CardLinkAssignmentDocument.model_validate(
        {
            "_id": ObjectId(),
            "card_id": replacement_card.id,
            "public_access_link_id": replacement_link.id,
            "status": CardLinkAssignmentStatus.ACTIVE,
            "attached_at": NOW,
            "updated_at": NOW,
            "attached_by_admin_id": ADMIN_ID,
        }
    )
    replacement_repository = CardRepository([replacement_card])
    replacement_link_repository = PublicAccessLinkRepository([replacement_link])
    replacement_assignment_repository = CardLinkAssignmentRepository([replacement_assignment])
    replacement_service = CardService(
        replacement_repository,
        UserRepository(user()),
        public_access_link_repository=replacement_link_repository,
        card_link_assignment_repository=replacement_assignment_repository,
    )

    replacement = await replacement_service.replace(card_id=replacement_card.id, now=NOW)

    assert replacement.card.status is CardStatus.ASSIGNED
    assert replacement_repository.cards[replacement_card.id].status is CardStatus.REPLACED
    assert (
        replacement_link_repository.links[replacement_link.id].status
        is PublicAccessLinkStatus.DISABLED
    )
    assert (
        replacement_assignment_repository.assignments[replacement_assignment.id].status
        is CardLinkAssignmentStatus.DISABLED
    )


@pytest.mark.asyncio
async def test_user_card_service_replacement_creates_new_link_for_replacement_card() -> None:
    linked_profile = profile(ready=True)
    old_card = card(status=CardStatus.ACTIVE)
    old_link = PublicAccessLinkDocument.model_validate(
        {
            "_id": ObjectId(),
            "profile_id": linked_profile.id,
            "purpose": PublicLinkPurpose.CARD,
            "label": "Card access",
            "token_hash": hash_public_token(generate_public_token()),
            "status": PublicAccessLinkStatus.ACTIVE,
            "created_by": ADMIN_ID,
            "created_at": NOW,
            "updated_at": NOW,
            "activated_at": NOW,
        }
    )
    old_assignment = CardLinkAssignmentDocument.model_validate(
        {
            "_id": ObjectId(),
            "card_id": old_card.id,
            "public_access_link_id": old_link.id,
            "status": CardLinkAssignmentStatus.ACTIVE,
            "attached_at": NOW,
            "updated_at": NOW,
            "attached_by_admin_id": ADMIN_ID,
        }
    )
    repository = CardRepository([old_card])
    link_repository = PublicAccessLinkRepository([old_link])
    assignment_repository = CardLinkAssignmentRepository([old_assignment])
    service = CardService(
        repository,
        UserRepository(user()),
        profile_repository=ProfileRepository(linked_profile),
        public_access_link_repository=link_repository,
        card_link_assignment_repository=assignment_repository,
    )

    replacement = await service.replace(card_id=old_card.id, now=NOW)

    assert repository.cards[replacement.card.id].token_hash is None
    assert repository.cards[replacement.card.id].status is CardStatus.ASSIGNED
    assert link_repository.links[old_link.id].status is PublicAccessLinkStatus.DISABLED
    assert (
        assignment_repository.assignments[old_assignment.id].status
        is CardLinkAssignmentStatus.DISABLED
    )
    new_assignment = await assignment_repository.find_active_by_card_id(replacement.card.id)
    assert new_assignment is not None
    new_link = await link_repository.find_by_id(new_assignment.public_access_link_id)
    assert new_link is not None
    assert new_link.profile_id == linked_profile.id
    assert new_link.purpose is PublicLinkPurpose.CARD
    assert new_link.status is PublicAccessLinkStatus.PENDING


@pytest.mark.asyncio
async def test_user_card_service_confirms_encoding_through_assigned_link() -> None:
    linked_profile = profile(ready=True)
    linked_card = CardDocument.model_validate(
        {
            "_id": ObjectId(),
            "serial": generate_serial(),
            "owner_id": None,
            "token_hash": None,
            "status": CardStatus.UNASSIGNED,
            "is_current": False,
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    public_token = generate_public_token()
    linked = PublicAccessLinkDocument.model_validate(
        {
            "_id": ObjectId(),
            "profile_id": linked_profile.id,
            "purpose": PublicLinkPurpose.CARD,
            "label": "Card access",
            "token_hash": hash_public_token(public_token),
            "status": PublicAccessLinkStatus.PENDING,
            "created_by": ADMIN_ID,
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    assignment = CardLinkAssignmentDocument.model_validate(
        {
            "_id": ObjectId(),
            "card_id": linked_card.id,
            "public_access_link_id": linked.id,
            "status": CardLinkAssignmentStatus.ACTIVE,
            "attached_at": NOW,
            "updated_at": NOW,
            "attached_by_admin_id": ADMIN_ID,
        }
    )
    repository = CardRepository([linked_card])
    link_repository = PublicAccessLinkRepository([linked])
    assignment_repository = CardLinkAssignmentRepository([assignment])
    service = CardService(
        repository,
        UserRepository(user()),
        public_card_base_url="https://app.example/e",
        profile_repository=ProfileRepository(linked_profile),
        public_access_link_repository=link_repository,
        card_link_assignment_repository=assignment_repository,
    )

    confirmed = await service.confirm_encoding(
        card_id=linked_card.id,
        public_url=f"https://app.example/e/{public_token}",
        admin_id=ADMIN_ID,
        now=NOW,
    )

    assert confirmed.encoding_verified_at == NOW
    assert repository.cards[linked_card.id].encoded_by_admin_id == ADMIN_ID


@pytest.mark.asyncio
async def test_user_card_service_restores_and_revokes_link_state_with_card_actions() -> None:
    linked_profile = profile(ready=True)
    disabled_card = card(status=CardStatus.DISABLED).model_copy(
        update={"issued_at": NOW, "encoding_verified_at": NOW, "is_current": True}
    )
    disabled_link = PublicAccessLinkDocument.model_validate(
        {
            "_id": ObjectId(),
            "profile_id": linked_profile.id,
            "purpose": PublicLinkPurpose.CARD,
            "label": "Card access",
            "token_hash": hash_public_token(generate_public_token()),
            "status": PublicAccessLinkStatus.DISABLED,
            "created_by": ADMIN_ID,
            "created_at": NOW,
            "updated_at": NOW,
            "disabled_at": NOW,
        }
    )
    disabled_assignment = CardLinkAssignmentDocument.model_validate(
        {
            "_id": ObjectId(),
            "card_id": disabled_card.id,
            "public_access_link_id": disabled_link.id,
            "status": CardLinkAssignmentStatus.DISABLED,
            "attached_at": NOW,
            "updated_at": NOW,
            "attached_by_admin_id": ADMIN_ID,
            "disabled_at": NOW,
            "disabled_by_admin_id": ADMIN_ID,
        }
    )
    repository = CardRepository([disabled_card])
    link_repository = PublicAccessLinkRepository([disabled_link])
    assignment_repository = CardLinkAssignmentRepository([disabled_assignment])
    service = CardService(
        repository,
        UserRepository(user()),
        profile_repository=ProfileRepository(linked_profile),
        public_access_link_repository=link_repository,
        card_link_assignment_repository=assignment_repository,
    )

    activated = await service.activate_user_card(
        card_id=disabled_card.id, user_id=OWNER_ID, now=NOW
    )
    assert activated.status is CardStatus.ACTIVE
    assert link_repository.links[disabled_link.id].status is PublicAccessLinkStatus.ACTIVE
    assert (
        assignment_repository.assignments[disabled_assignment.id].status
        is CardLinkAssignmentStatus.ACTIVE
    )

    disabled = await service.disable_user_card(card_id=disabled_card.id, user_id=OWNER_ID, now=NOW)

    assert repository.cards[disabled_card.id].status is CardStatus.DISABLED
    assert link_repository.links[disabled_link.id].status is PublicAccessLinkStatus.DISABLED
    assert (
        assignment_repository.assignments[disabled_assignment.id].status
        is CardLinkAssignmentStatus.DISABLED
    )
    assert disabled.status is CardStatus.DISABLED


def test_user_card_routes_are_authenticated_and_safe() -> None:
    owner = user()
    user_repository = UserRepository(owner)
    service = AsyncMock(spec=CardService)
    safe_card = card(status=CardStatus.ACTIVE)
    service.list_user_cards.return_value = [safe_card]
    service.get_user_card.return_value = safe_card
    service.describe_user_card.return_value = (safe_card, None, None)
    service.activate_user_card.return_value = safe_card
    service.disable_user_card.return_value = safe_card
    service.mark_lost.return_value = safe_card
    app = create_app(
        settings=settings(),
        database=FakeDatabase(ready=True),
        auth_repository=user_repository,
        profile_repository=ProfileRepository(profile(ready=True)),
    )
    app.dependency_overrides[get_user_card_service] = lambda: service

    with TestClient(app) as client:
        unauthenticated = [
            client.get("/api/v1/me/cards"),
            client.get(f"/api/v1/me/cards/{safe_card.id}"),
            client.post(f"/api/v1/me/cards/{safe_card.id}/activate"),
            client.post(f"/api/v1/me/cards/{safe_card.id}/disable"),
            client.post(f"/api/v1/me/cards/{safe_card.id}/lost"),
        ]
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "person@example.com", "password": "password-123"},
        )
        service.list_user_cards.return_value = []
        empty = client.get("/api/v1/me/cards")
        service.list_user_cards.return_value = [safe_card]
        listed = client.get("/api/v1/me/cards")
        detail = client.get(f"/api/v1/me/cards/{safe_card.id}")
        activated = client.post(f"/api/v1/me/cards/{safe_card.id}/activate")
        disabled = client.post(f"/api/v1/me/cards/{safe_card.id}/disable")
        lost = client.post(f"/api/v1/me/cards/{safe_card.id}/lost")

    assert [response.status_code for response in unauthenticated] == [401] * 5
    assert login.status_code == 200
    assert empty.status_code == 200
    assert empty.json() == {"cards": []}
    assert [
        listed.status_code,
        detail.status_code,
        activated.status_code,
        disabled.status_code,
        lost.status_code,
    ] == [200] * 5
    assert set(listed.json()["cards"][0]) == {
        "id",
        "serial",
        "status",
        "is_current",
        "issued_at",
        "activated_at",
        "disabled_at",
        "created_at",
        "updated_at",
        "can_activate",
        "can_disable",
        "link_status",
        "link_purpose",
        "link_label",
    }
    assert "token_hash" not in listed.text
    assert "public_token" not in listed.text
    assert service.list_user_cards.await_count == 2
    service.list_user_cards.assert_awaited_with(user_id=str(OWNER_ID))


@pytest.mark.parametrize(
    ("method", "service_method", "code"),
    [
        ("activate", "activate_user_card", "card.not_issued"),
        ("activate", "activate_user_card", "card.encoding_not_verified"),
        ("activate", "activate_user_card", "card.profile_not_ready"),
        ("disable", "disable_user_card", "card.invalid_state_transition"),
        ("disable", "disable_user_card", "card.terminal"),
        ("detail", "get_user_card", "card.not_found"),
    ],
)
def test_user_card_route_error_codes(method: str, service_method: str, code: str) -> None:
    errors = {
        "card.not_issued": CardNotIssuedError,
        "card.encoding_not_verified": CardEncodingNotVerifiedError,
        "card.profile_not_ready": CardProfileNotReadyError,
        "card.invalid_state_transition": CardInvalidTransitionError,
        "card.terminal": CardTerminalStateError,
        "card.not_found": CardNotFoundError,
    }
    owner = user()
    service = AsyncMock(spec=CardService)
    getattr(service, service_method).side_effect = errors[code]("private details")
    service.describe_user_card.side_effect = (
        errors[code]("private details") if method == "detail" else None
    )
    app = create_app(
        settings=settings(),
        database=FakeDatabase(ready=True),
        auth_repository=UserRepository(owner),
        profile_repository=ProfileRepository(profile(ready=True)),
    )
    app.dependency_overrides[get_user_card_service] = lambda: service
    card_id = str(ObjectId())

    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"email": "person@example.com", "password": "password-123"},
            ).status_code
            == 200
        )
        if method == "activate":
            response = client.post(f"/api/v1/me/cards/{card_id}/activate")
        elif method == "disable":
            response = client.post(f"/api/v1/me/cards/{card_id}/disable")
        else:
            response = client.get(f"/api/v1/me/cards/{card_id}")

    assert response.status_code == (404 if code == "card.not_found" else 409)
    assert response.json()["error"]["code"] == code
    assert "private details" not in response.text
