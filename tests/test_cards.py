from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from emercard.core.config import Settings
from emercard.db.repositories import RepositoryError
from emercard.modules.cards import (
    CardAssignmentTargetInvalidError,
    CardDocument,
    CardEncodingMismatchError,
    CardEncodingNotVerifiedError,
    CardInvalidTransitionError,
    CardInvariantError,
    CardLinkAlreadyProvisionedError,
    CardProvisioningError,
    CardReassignmentNotAllowedError,
    CardReplacementError,
    CardRepository,
    CardSerialConflictError,
    CardService,
    CardStatus,
    CardTerminalStateError,
    CardTokenHashConflictError,
    generate_public_token,
    generate_serial,
    hash_public_token,
    normalize_serial,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
OWNER_ID = ObjectId("507f1f77bcf86cd799439011")
SECOND_OWNER_ID = ObjectId("507f1f77bcf86cd799439012")
ADMIN_ID = ObjectId("507f1f77bcf86cd799439013")


def card_document(
    *,
    status: CardStatus = CardStatus.UNASSIGNED,
    owner_id: ObjectId | None = None,
    serial: str | None = None,
    replaces_card_id: ObjectId | None = None,
) -> CardDocument:
    return CardDocument(
        _id=ObjectId(),
        serial=serial or generate_serial(),
        owner_id=owner_id,
        token_hash=hash_public_token(generate_public_token()),
        provisioned_at=NOW,
        status=status,
        is_current=status in {CardStatus.ASSIGNED, CardStatus.ACTIVE, CardStatus.DISABLED},
        assigned_at=NOW if status is not CardStatus.UNASSIGNED else None,
        activated_at=NOW if status is CardStatus.ACTIVE else None,
        disabled_at=NOW if status is CardStatus.DISABLED else None,
        lost_at=NOW if status is CardStatus.LOST else None,
        replaced_at=NOW if status is CardStatus.REPLACED else None,
        replaces_card_id=replaces_card_id,
        created_at=NOW,
        updated_at=NOW,
    )


def test_serial_is_canonical_case_insensitive_and_checksum_validated() -> None:
    serial = generate_serial()

    assert len(serial) == 20
    assert normalize_serial(serial.lower()) == serial
    with pytest.raises(CardInvariantError, match="checksum"):
        normalize_serial(f"{serial[:-1]}0" if serial[-1] != "0" else f"{serial[:-1]}1")


def test_public_token_has_expected_entropy_shape_and_versioned_hash() -> None:
    token = generate_public_token()
    token_hash = hash_public_token(token)

    assert len(token) == 43
    assert "=" not in token
    assert all(character.isalnum() or character in "-_" for character in token)
    assert token_hash.startswith("v1$")
    assert len(token_hash) == 67
    assert hash_public_token(token) == token_hash


def test_blank_card_document_has_no_token_material_or_provisioning_metadata() -> None:
    card = CardDocument(
        _id=ObjectId(),
        serial=generate_serial(),
        token_hash=None,
        status=CardStatus.UNASSIGNED,
        is_current=False,
        created_at=NOW,
        updated_at=NOW,
    )

    assert card.token_hash is None
    assert card.provisioned_at is None
    assert card.encoding_verified_at is None


def test_card_document_enforces_status_and_current_invariants() -> None:
    for status in CardStatus:
        if status is CardStatus.UNASSIGNED:
            continue
        card_document(status=status, owner_id=OWNER_ID)

    with pytest.raises(ValueError, match="ownerless"):
        CardDocument(
            _id=ObjectId(),
            serial=generate_serial(),
            owner_id=OWNER_ID,
            token_hash=hash_public_token(generate_public_token()),
            status=CardStatus.UNASSIGNED,
            is_current=True,
            created_at=NOW,
            updated_at=NOW,
        )

    payload = card_document(status=CardStatus.ACTIVE, owner_id=OWNER_ID).model_dump(by_alias=True)
    payload["is_current"] = False
    with pytest.raises(ValueError, match="current"):
        CardDocument.model_validate(payload)


@pytest.mark.asyncio
async def test_card_repository_persists_only_token_hash() -> None:
    database = MagicMock()
    collection = MagicMock()
    collection.insert_one = AsyncMock()
    database.__getitem__.return_value = collection
    repository = CardRepository(database, Settings(environment="test"))
    token = generate_public_token()

    card = await repository.create_unassigned_card(
        serial=generate_serial(), token_hash=hash_public_token(token), now=NOW
    )

    persisted = collection.insert_one.await_args.args[0]
    assert card.status is CardStatus.UNASSIGNED
    assert persisted["token_hash"] == hash_public_token(token)
    assert token not in persisted.values()
    assert "public_token" not in persisted
    assert hash_public_token(token) not in repr(card)
    assert hash_public_token(token) not in str(card)


@pytest.mark.asyncio
async def test_card_repository_creates_blank_card_without_token_hash() -> None:
    database = MagicMock()
    collection = MagicMock()
    collection.insert_one = AsyncMock()
    database.__getitem__.return_value = collection
    repository = CardRepository(database, Settings(environment="test"))

    card = await repository.create_blank_card(serial=generate_serial(), now=NOW)

    assert card.token_hash is None
    persisted = collection.insert_one.await_args.args[0]
    assert persisted["token_hash"] is None
    assert "public_token" not in persisted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "error_type"),
    [("serial", CardSerialConflictError), ("token_hash", CardTokenHashConflictError)],
)
async def test_card_repository_translates_identity_duplicate_keys(
    field: str, error_type: type[Exception]
) -> None:
    database = MagicMock()
    collection = MagicMock()
    collection.insert_one = AsyncMock(side_effect=DuplicateKeyError({"keyPattern": {field: 1}}))
    database.__getitem__.return_value = collection
    repository = CardRepository(database, Settings(environment="test"))

    with pytest.raises(error_type):
        await repository.create_unassigned_card(
            serial=generate_serial(),
            token_hash=hash_public_token(generate_public_token()),
            now=NOW,
        )


def managed_card(
    *,
    status: CardStatus = CardStatus.UNASSIGNED,
    owner_id: ObjectId | None = None,
    token: str = "managed-token",
) -> CardDocument:
    return CardDocument(
        _id=ObjectId(),
        serial=generate_serial(),
        owner_id=owner_id,
        token_hash=hash_public_token(token),
        status=status,
        is_current=status in {CardStatus.ASSIGNED, CardStatus.ACTIVE, CardStatus.DISABLED},
        provisioned_at=NOW,
        encoding_verified_at=NOW,
        encoded_by_admin_id=ADMIN_ID,
        assigned_at=NOW
        if status in {CardStatus.ASSIGNED, CardStatus.ACTIVE, CardStatus.DISABLED}
        else None,
        activated_at=NOW if status is CardStatus.ACTIVE else None,
        created_at=NOW,
        updated_at=NOW,
    )


class AdminFakeUserRepository:
    def __init__(self) -> None:
        self.users = {
            OWNER_ID: SimpleNamespace(id=OWNER_ID, role="user"),
            SECOND_OWNER_ID: SimpleNamespace(id=SECOND_OWNER_ID, role="user"),
        }

    async def find_by_id(self, user_id: ObjectId | str) -> object | None:
        return self.users.get(ObjectId(user_id))

    async def find_by_email(self, email: str) -> object | None:
        del email
        return None


class AdminFakeIdempotencyRepository:
    def __init__(self) -> None:
        self.cards: dict[str, ObjectId] = {}

    async def find_card_id(self, operation_key: str, **kwargs: Any) -> ObjectId | None:
        return self.cards.get(operation_key)

    async def save_card_id(self, *, operation_key: str, card_id: ObjectId, **kwargs: Any) -> None:
        self.cards[operation_key] = card_id


class AdminFakeCustodyRepository:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.fail = False

    async def append(self, **event: Any) -> ObjectId:
        if self.fail:
            raise RepositoryError("custody event write failed")
        self.events.append(event)
        return ObjectId()


class AdminFakeCardRepository:
    def __init__(self) -> None:
        self.cards: dict[ObjectId, CardDocument] = {}
        self.create_count = 0

    async def create_blank_card(self, *, serial: str, **kwargs: Any) -> CardDocument:
        self.create_count += 1
        card = CardDocument(
            _id=ObjectId(),
            serial=serial,
            token_hash=None,
            status=CardStatus.UNASSIGNED,
            is_current=False,
            created_at=NOW,
            updated_at=NOW,
        )
        self.cards[card.id] = card
        return card

    async def find_by_id(self, card_id: ObjectId | str, **kwargs: Any) -> CardDocument | None:
        return self.cards.get(ObjectId(card_id))

    async def provision_link(
        self, *, card_id: ObjectId | str, token_hash: str, **kwargs: Any
    ) -> CardDocument | None:
        card = self.cards[ObjectId(card_id)]
        updated = card.model_copy(update={"legacy_token_hash": token_hash, "provisioned_at": NOW})
        self.cards[updated.id] = updated
        return updated

    async def reprovision_link(
        self, *, card_id: ObjectId | str, token_hash: str, **kwargs: Any
    ) -> CardDocument | None:
        card = self.cards[ObjectId(card_id)]
        updated = card.model_copy(
            update={
                "legacy_token_hash": token_hash,
                "provisioned_at": NOW,
            }
        )
        self.cards[updated.id] = updated
        return updated

    async def confirm_encoding(
        self,
        *,
        card_id: ObjectId | str,
        token_hash: str,
        admin_id: ObjectId | str,
        **kwargs: Any,
    ) -> CardDocument | None:
        card = self.cards[ObjectId(card_id)]
        if card.legacy_token_hash != token_hash:
            return None
        updated = card.model_copy(
            update={"encoding_verified_at": NOW, "encoded_by_admin_id": ObjectId(admin_id)}
        )
        self.cards[updated.id] = updated
        return updated

    async def confirm_encoding_without_token_hash(
        self,
        *,
        card_id: ObjectId | str,
        admin_id: ObjectId | str,
        **kwargs: Any,
    ) -> CardDocument | None:
        del kwargs
        card = self.cards[ObjectId(card_id)]
        updated = card.model_copy(
            update={"encoding_verified_at": NOW, "encoded_by_admin_id": ObjectId(admin_id)}
        )
        self.cards[updated.id] = updated
        return updated

    async def assign_verified_to_user(
        self, *, card_id: ObjectId | str, user_id: ObjectId | str, **kwargs: Any
    ) -> CardDocument | None:
        card = self.cards[ObjectId(card_id)]
        if card.status is not CardStatus.UNASSIGNED:
            return None
        updated = card.model_copy(
            update={
                "owner_id": ObjectId(user_id),
                "status": CardStatus.ASSIGNED,
                "is_current": True,
                "assigned_at": NOW,
            }
        )
        self.cards[updated.id] = updated
        return updated

    async def reassign_before_issue(
        self, *, card_id: ObjectId | str, new_owner_id: ObjectId | str, **kwargs: Any
    ) -> CardDocument | None:
        card = self.cards[ObjectId(card_id)]
        if card.status is not CardStatus.ASSIGNED or card.issued_at is not None:
            return None
        updated = card.model_copy(update={"owner_id": ObjectId(new_owner_id), "assigned_at": NOW})
        self.cards[updated.id] = updated
        return updated

    async def unassign_before_issue(
        self, *, card_id: ObjectId | str, **kwargs: Any
    ) -> CardDocument | None:
        card = self.cards[ObjectId(card_id)]
        if card.status is not CardStatus.ASSIGNED or card.issued_at is not None:
            return None
        updated = card.model_copy(
            update={"owner_id": None, "status": CardStatus.UNASSIGNED, "is_current": False}
        )
        self.cards[updated.id] = updated
        return updated

    async def issue(
        self, *, card_id: ObjectId | str, admin_id: ObjectId | str, **kwargs: Any
    ) -> CardDocument | None:
        card = self.cards[ObjectId(card_id)]
        if card.issued_at is not None or card.status is not CardStatus.ASSIGNED:
            return None
        updated = card.model_copy(
            update={"issued_at": NOW, "issued_by_admin_id": ObjectId(admin_id)}
        )
        self.cards[updated.id] = updated
        return updated

    async def void_before_issue(
        self, *, card_id: ObjectId | str, **kwargs: Any
    ) -> CardDocument | None:
        card = self.cards[ObjectId(card_id)]
        if card.issued_at is not None:
            return None
        updated = card.model_copy(
            update={
                "owner_id": None,
                "status": CardStatus.VOID,
                "is_current": False,
                "voided_at": NOW,
            }
        )
        self.cards[updated.id] = updated
        return updated

    async def with_transaction(self, operation: Any) -> Any:
        snapshot = self.cards.copy()
        try:
            return await operation(object())
        except Exception:
            self.cards = snapshot
            raise


class FakeUserRepository:
    async def find_by_id(self, user_id: ObjectId | str) -> object:
        return object() if str(user_id) == str(OWNER_ID) else None


class FakeCardRepository:
    def __init__(self, cards: list[CardDocument] | None = None) -> None:
        self.cards = {card.id: card for card in cards or []}
        self.fail_create = False
        self.fail_link = False

    async def create_unassigned_card(self, **kwargs: Any) -> CardDocument:
        if self.fail_create:
            raise RepositoryError("database unavailable")
        card = card_document(
            serial=kwargs["serial"],
            replaces_card_id=kwargs.get("replaces_card_id"),
        )
        self.cards[card.id] = card
        return card

    async def find_by_id(self, card_id: ObjectId | str, **kwargs: Any) -> CardDocument | None:
        return self.cards.get(ObjectId(card_id))

    async def assign_to_user(
        self, *, card_id: ObjectId | str, user_id: ObjectId | str, **kwargs: Any
    ) -> CardDocument | None:
        card = self.cards[ObjectId(card_id)]
        if card.status is not CardStatus.UNASSIGNED:
            return None
        updated = card.model_copy(
            update={
                "owner_id": ObjectId(user_id),
                "status": CardStatus.ASSIGNED,
                "is_current": True,
                "assigned_at": NOW,
                "updated_at": NOW,
            }
        )
        self.cards[updated.id] = updated
        return updated

    async def transition_status(
        self, *, card_id: ObjectId | str, to_status: CardStatus, **kwargs: Any
    ) -> CardDocument | None:
        card = self.cards[ObjectId(card_id)]
        if card.status not in kwargs["from_statuses"]:
            return None
        updated = card.model_copy(
            update={
                "status": to_status,
                "is_current": to_status
                in {CardStatus.ASSIGNED, CardStatus.ACTIVE, CardStatus.DISABLED},
                "updated_at": NOW,
            }
        )
        self.cards[updated.id] = updated
        return updated

    async def mark_replaced(self, *, card_id: ObjectId | str, **kwargs: Any) -> CardDocument | None:
        return await self.transition_status(
            card_id=card_id,
            to_status=CardStatus.REPLACED,
            from_statuses={CardStatus.ASSIGNED, CardStatus.ACTIVE, CardStatus.DISABLED},
        )

    async def link_replacement(
        self, *, card_id: ObjectId | str, replacement_card_id: ObjectId | str, **kwargs: Any
    ) -> CardDocument | None:
        if self.fail_link:
            return None
        card = self.cards[ObjectId(card_id)]
        updated = card.model_copy(update={"replacement_card_id": ObjectId(replacement_card_id)})
        self.cards[updated.id] = updated
        return updated

    async def with_transaction(self, operation: Any) -> Any:
        snapshot = self.cards.copy()
        try:
            return await operation(object())
        except Exception:
            self.cards = snapshot
            raise


@pytest.mark.asyncio
async def test_admin_service_replays_blank_creation_and_invalidates_reprovisioned_link() -> None:
    repository = AdminFakeCardRepository()
    idempotency = AdminFakeIdempotencyRepository()
    user_repository = AdminFakeUserRepository()
    tokens = iter(("first-token", "second-token"))
    service = CardService(
        repository,
        user_repository,
        token_generator=lambda: next(tokens),
        public_card_base_url="https://app.example/e",
        idempotency_repository=idempotency,
    )

    first_blank = await service.create_blank_card(operation_key="operation-1", now=NOW)
    replayed_blank = await service.create_blank_card(operation_key="operation-1", now=NOW)
    assert replayed_blank.id == first_blank.id
    assert repository.create_count == 1
    assert first_blank.token_hash is None

    provisioned = await service.provision_link(card_id=first_blank.id, now=NOW)
    assert provisioned.public_url == "https://app.example/e/first-token"
    assert provisioned.public_token not in repr(provisioned)
    old_hash = repository.cards[first_blank.id].token_hash

    reprovisioned = await service.reprovision_link(card_id=first_blank.id, now=NOW)
    assert reprovisioned.public_url.endswith("/second-token")
    assert repository.cards[first_blank.id].token_hash != old_hash
    with pytest.raises(CardEncodingMismatchError):
        await service.confirm_encoding(
            card_id=first_blank.id,
            public_url=provisioned.public_url,
            admin_id=ADMIN_ID,
            now=NOW,
        )

    confirmed = await service.confirm_encoding(
        card_id=first_blank.id,
        public_url=reprovisioned.public_url,
        admin_id=ADMIN_ID,
        now=NOW,
    )
    assert confirmed.encoding_verified_at == NOW
    with pytest.raises(CardLinkAlreadyProvisionedError):
        await service.reprovision_link(card_id=first_blank.id, now=NOW)


@pytest.mark.asyncio
async def test_admin_service_gates_assignment_and_records_custody_events() -> None:
    repository = AdminFakeCardRepository()
    users = AdminFakeUserRepository()
    events = AdminFakeCustodyRepository()
    service = CardService(
        repository,
        users,
        token_generator=lambda: "assign-token",
        public_card_base_url="https://app.example/e",
        custody_event_repository=events,
    )
    blank = await service.create_blank_card(now=NOW)
    provisioned = await service.provision_link(card_id=blank.id, now=NOW)

    with pytest.raises(CardEncodingNotVerifiedError):
        await service.assign_verified_to_user(
            card_id=blank.id, user_id=OWNER_ID, admin_id=ADMIN_ID, now=NOW
        )

    await service.confirm_encoding(
        card_id=blank.id,
        public_url=provisioned.public_url,
        admin_id=ADMIN_ID,
        now=NOW,
    )
    assigned = await service.assign_verified_to_user(
        card_id=blank.id, user_id=OWNER_ID, admin_id=ADMIN_ID, now=NOW
    )
    reassigned = await service.reassign_before_issue(
        card_id=assigned.id,
        new_owner_id=SECOND_OWNER_ID,
        admin_id=ADMIN_ID,
        reason="assignment_error",
        now=NOW,
    )
    assert reassigned.owner_id == SECOND_OWNER_ID
    await service.unassign_before_issue(card_id=blank.id, admin_id=ADMIN_ID, now=NOW)
    reassigned_again = await service.assign_verified_to_user(
        card_id=blank.id, user_id=OWNER_ID, admin_id=ADMIN_ID, now=NOW
    )
    issued = await service.issue(card_id=reassigned_again.id, admin_id=ADMIN_ID, now=NOW)
    assert await service.issue(card_id=issued.id, admin_id=ADMIN_ID, now=NOW) == issued
    with pytest.raises(CardReassignmentNotAllowedError):
        await service.reassign_before_issue(
            card_id=issued.id,
            new_owner_id=SECOND_OWNER_ID,
            admin_id=ADMIN_ID,
            reason="recipient_changed",
            now=NOW,
        )

    void_blank = await service.create_blank_card(now=NOW)
    voided = await service.void(card_id=void_blank.id, admin_id=ADMIN_ID, now=NOW)
    assert voided.status is CardStatus.VOID
    assert [event["event_type"] for event in events.events] == [
        "assigned",
        "reassigned",
        "unassigned",
        "assigned",
        "issued",
        "voided",
    ]


@pytest.mark.asyncio
async def test_custody_event_failure_rolls_back_card_mutation() -> None:
    repository = AdminFakeCardRepository()
    users = AdminFakeUserRepository()
    events = AdminFakeCustodyRepository()
    service = CardService(repository, users, custody_event_repository=events)
    card = managed_card()
    repository.cards[card.id] = card
    events.fail = True

    with pytest.raises(RepositoryError):
        await service.assign_verified_to_user(
            card_id=card.id, user_id=OWNER_ID, admin_id=ADMIN_ID, now=NOW
        )

    assert repository.cards[card.id].status is CardStatus.UNASSIGNED
    assert repository.cards[card.id].owner_id is None
    assert events.events == []


@pytest.mark.asyncio
async def test_service_rejects_invalid_admin_assignment_target() -> None:
    repository = AdminFakeCardRepository()
    service = CardService(repository, AdminFakeUserRepository())
    card = managed_card()
    repository.cards[card.id] = card

    with pytest.raises(CardAssignmentTargetInvalidError):
        await service.assign_verified_to_user(
            card_id=card.id,
            user_id=ObjectId("507f1f77bcf86cd799439099"),
            admin_id=ADMIN_ID,
            now=NOW,
        )


@pytest.mark.asyncio
async def test_service_rejects_forbidden_transitions() -> None:
    repository = FakeCardRepository()
    service = CardService(repository, FakeUserRepository())
    provisioned = await service.provision_unassigned(now=NOW)
    await service.assign_to_user(card_id=provisioned.card.id, user_id=OWNER_ID, now=NOW)

    with pytest.raises(CardInvalidTransitionError):
        await service.disable(card_id=provisioned.card.id, owner_id=OWNER_ID, now=NOW)


@pytest.mark.asyncio
async def test_service_allows_independent_active_cards_and_terminal_states() -> None:
    repository = FakeCardRepository()
    service = CardService(repository, FakeUserRepository())
    first = await service.provision_unassigned(now=NOW)
    second = await service.provision_unassigned(now=NOW)
    await service.assign_to_user(card_id=first.card.id, user_id=OWNER_ID, now=NOW)
    await service.assign_to_user(card_id=second.card.id, user_id=OWNER_ID, now=NOW)

    await service.activate(card_id=first.card.id, owner_id=OWNER_ID, now=NOW)
    await service.activate(card_id=second.card.id, owner_id=OWNER_ID, now=NOW)
    disabled = await service.disable(card_id=first.card.id, owner_id=OWNER_ID, now=NOW)

    assert disabled.status is CardStatus.DISABLED
    assert repository.cards[second.card.id].status is CardStatus.ACTIVE
    await service.mark_lost(card_id=first.card.id, now=NOW)
    with pytest.raises(CardTerminalStateError):
        await service.activate(card_id=first.card.id, owner_id=OWNER_ID, now=NOW)


@pytest.mark.asyncio
async def test_service_retries_identity_conflicts_and_never_returns_failed_token() -> None:
    repository = FakeCardRepository()
    repository.create_unassigned_card = AsyncMock(
        side_effect=[CardSerialConflictError("conflict"), RepositoryError("down")]
    )
    service = CardService(repository, FakeUserRepository())

    with pytest.raises(CardProvisioningError) as error:
        await service.provision_unassigned(now=NOW)

    assert "public" not in str(error.value).lower()
    assert "token" not in str(error.value).lower()


@pytest.mark.asyncio
async def test_service_replacement_links_old_and_new_cards() -> None:
    repository = FakeCardRepository()
    service = CardService(repository, FakeUserRepository())
    provisioned = await service.provision_unassigned(now=NOW)
    await service.assign_to_user(card_id=provisioned.card.id, user_id=OWNER_ID, now=NOW)
    await service.activate(card_id=provisioned.card.id, owner_id=OWNER_ID, now=NOW)

    replacement = await service.replace(card_id=provisioned.card.id, now=NOW)

    old_card = repository.cards[provisioned.card.id]
    new_card = repository.cards[replacement.card.id]
    assert old_card.status is CardStatus.REPLACED
    assert old_card.is_current is False
    assert old_card.replacement_card_id == new_card.id
    assert new_card.replaces_card_id == old_card.id
    assert new_card.status is CardStatus.ASSIGNED
    assert new_card.provisioned_at == NOW
    assert replacement.public_token not in new_card.model_dump().values()


@pytest.mark.asyncio
async def test_service_replacement_rolls_back_on_link_failure() -> None:
    repository = FakeCardRepository()
    service = CardService(repository, FakeUserRepository())
    provisioned = await service.provision_unassigned(now=NOW)
    await service.assign_to_user(card_id=provisioned.card.id, user_id=OWNER_ID, now=NOW)
    repository.fail_link = True

    with pytest.raises(CardReplacementError):
        await service.replace(card_id=provisioned.card.id, now=NOW)

    assert set(repository.cards) == {provisioned.card.id}
    assert repository.cards[provisioned.card.id].status is CardStatus.ASSIGNED
