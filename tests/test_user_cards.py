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
        token_revision=1,
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
        token_revision=1,
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


def test_user_card_routes_are_authenticated_and_safe() -> None:
    owner = user()
    user_repository = UserRepository(owner)
    service = AsyncMock(spec=CardService)
    safe_card = card(status=CardStatus.ACTIVE)
    service.list_user_cards.return_value = [safe_card]
    service.get_user_card.return_value = safe_card
    service.activate_user_card.return_value = safe_card
    service.disable_user_card.return_value = safe_card
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

    assert [response.status_code for response in unauthenticated] == [401] * 4
    assert login.status_code == 200
    assert empty.status_code == 200
    assert empty.json() == {"cards": []}
    assert [
        listed.status_code,
        detail.status_code,
        activated.status_code,
        disabled.status_code,
    ] == [200] * 4
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
