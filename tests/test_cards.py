from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from emercard.core.config import Settings
from emercard.db.repositories import RepositoryError
from emercard.modules.cards import (
    CardDocument,
    CardInvalidTransitionError,
    CardInvariantError,
    CardProvisioningError,
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
