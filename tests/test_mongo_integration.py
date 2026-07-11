"""Real MongoDB checks for indexes and atomic repository behavior."""

import asyncio
import os
from unittest.mock import AsyncMock

import pytest
from pymongo.errors import OperationFailure

from emercard.core.config import Settings
from emercard.db import Database, initialize_indexes
from emercard.db.indexes import (
    CARDS_OWNER_CURRENT_INDEX,
    CARDS_OWNER_INDEX,
    CARDS_OWNER_STATUS_INDEX,
    CARDS_REPLACEMENT_INDEX,
    CARDS_REPLACES_INDEX,
    CARDS_SERIAL_INDEX,
    CARDS_STATUS_INDEX,
    CARDS_TOKEN_HASH_INDEX,
    PROFILES_PUBLIC_TOKEN_INDEX,
)
from emercard.db.repositories import RepositoryConflictError
from emercard.modules.cards import (
    CardDocument,
    CardNotFoundError,
    CardReplacementError,
    CardRepository,
    CardSerialConflictError,
    CardService,
    CardStatus,
    CardTerminalStateError,
    CustodyEventRepository,
    generate_serial,
    hash_public_token,
)
from emercard.modules.profiles import ProfileRepository, ProfileUpsertInput
from emercard.modules.users import UserRepository


@pytest.fixture
async def mongo_context():
    uri = os.getenv("EMERCARD_TEST_MONGODB_URI")
    if not uri:
        pytest.skip("EMERCARD_TEST_MONGODB_URI is not configured")

    settings = Settings(
        environment="test",
        mongodb_uri=uri,
        mongodb_database="emercard_test_integration",
        mongodb_index_initialization_mode="disabled",
        mongodb_max_pool_size=5,
    )
    database = Database(settings)
    await database.start()
    if not await database.ping():
        await database.close()
        pytest.skip("configured MongoDB test server is unavailable")

    await initialize_indexes(database.database, settings)
    database_handle = database.database
    try:
        yield database_handle, settings
    finally:
        await database_handle.client.drop_database(settings.mongodb_database)
        await database.close()


@pytest.mark.mongo
@pytest.mark.asyncio
async def _create_verified_assigned_card(
    cards: CardRepository,
    users: UserRepository,
    *,
    owner_id,
    admin_id,
) -> CardDocument:
    provisioning = await CardService(cards, users).provision_unassigned()
    confirmed = await cards.confirm_encoding(
        card_id=provisioning.card.id,
        token_hash=hash_public_token(provisioning.public_token),
        admin_id=admin_id,
    )
    assert confirmed is not None
    assigned = await CardService(cards, users).assign_verified_to_user(
        card_id=provisioning.card.id,
        user_id=owner_id,
        admin_id=admin_id,
    )
    return assigned


async def _create_issued_card(
    cards: CardRepository,
    users: UserRepository,
    *,
    owner_id,
    admin_id,
) -> CardDocument:
    assigned = await _create_verified_assigned_card(
        cards, users, owner_id=owner_id, admin_id=admin_id
    )
    return await CardService(cards, users).issue(card_id=assigned.id, admin_id=admin_id)


@pytest.mark.mongo
@pytest.mark.asyncio
async def test_real_mongo_user_card_visibility_filters_and_orders_cards(mongo_context) -> None:
    database, settings = mongo_context
    users = UserRepository(database, settings)
    cards = CardRepository(database, settings)
    profiles = ProfileRepository(database, settings)
    owner = await users.create(email="user-controls-owner@example.com", password_hash="argon2-hash")
    other = await users.create(email="user-controls-other@example.com", password_hash="argon2-hash")
    admin = await users.create(
        email="user-controls-admin@example.com", password_hash="argon2-hash", role="admin"
    )
    await profiles.upsert_for_user(
        user_id=owner.id,
        profile=ProfileUpsertInput(
            display_name="Owner",
            birth_year=1995,
            gender="male",
            blood_type="O+",
            emergency_contacts=[
                {"name": "Contact", "relationship": "Family", "phone": "0900000000"}
            ],
        ),
    )
    user_service = CardService(cards, users, profile_repository=profiles)
    active = await _create_issued_card(cards, users, owner_id=owner.id, admin_id=admin.id)
    disabled = await _create_issued_card(cards, users, owner_id=owner.id, admin_id=admin.id)
    assigned = await _create_issued_card(cards, users, owner_id=owner.id, admin_id=admin.id)
    unissued = await _create_verified_assigned_card(
        cards, users, owner_id=owner.id, admin_id=admin.id
    )
    lost = await _create_issued_card(cards, users, owner_id=owner.id, admin_id=admin.id)
    foreign = await _create_issued_card(cards, users, owner_id=other.id, admin_id=admin.id)

    await user_service.activate_user_card(card_id=active.id, user_id=owner.id)
    await user_service.activate_user_card(card_id=disabled.id, user_id=owner.id)
    await user_service.disable_user_card(card_id=disabled.id, user_id=owner.id)
    await user_service.mark_lost(card_id=lost.id)

    visible = await user_service.list_user_cards(user_id=owner.id)
    assert [card.status for card in visible] == [
        CardStatus.ACTIVE,
        CardStatus.DISABLED,
        CardStatus.ASSIGNED,
    ]
    assert {card.id for card in visible} == {active.id, disabled.id, assigned.id}
    for hidden in [unissued, lost, foreign]:
        with pytest.raises(CardNotFoundError):
            await user_service.get_user_card(card_id=hidden.id, user_id=owner.id)


@pytest.mark.mongo
@pytest.mark.asyncio
async def test_real_mongo_user_card_controls_are_atomic_and_idempotent(mongo_context) -> None:
    database, settings = mongo_context
    hello = await database.client.admin.command("hello")
    if not hello.get("setName"):
        pytest.skip("MongoDB user-control race tests require a replica set")

    users = UserRepository(database, settings)
    cards = CardRepository(database, settings)
    profiles = ProfileRepository(database, settings)
    owner = await users.create(email="race-owner@example.com", password_hash="argon2-hash")
    admin = await users.create(
        email="race-admin@example.com", password_hash="argon2-hash", role="admin"
    )
    await profiles.upsert_for_user(
        user_id=owner.id,
        profile=ProfileUpsertInput(
            display_name="Owner",
            birth_year=1995,
            gender="male",
            blood_type="O+",
            emergency_contacts=[
                {"name": "Contact", "relationship": "Family", "phone": "0900000000"}
            ],
        ),
    )
    user_service = CardService(cards, users, profile_repository=profiles)
    admin_service = CardService(cards, users)
    card = await _create_issued_card(cards, users, owner_id=owner.id, admin_id=admin.id)

    results = await asyncio.gather(
        *[user_service.activate_user_card(card_id=card.id, user_id=owner.id) for _ in range(2)]
    )
    assert [result.status for result in results] == [CardStatus.ACTIVE, CardStatus.ACTIVE]
    stored = await cards.find_by_id(card.id)
    assert stored is not None
    assert stored.status is CardStatus.ACTIVE

    conflicting = await asyncio.gather(
        user_service.activate_user_card(card_id=card.id, user_id=owner.id),
        user_service.disable_user_card(card_id=card.id, user_id=owner.id),
        return_exceptions=True,
    )
    assert any(isinstance(result, CardDocument) for result in conflicting)
    concurrent_state = await cards.find_by_id(card.id)
    assert concurrent_state is not None
    assert concurrent_state.status in {CardStatus.ACTIVE, CardStatus.DISABLED}

    first_disabled = await user_service.disable_user_card(card_id=card.id, user_id=owner.id)
    repeated = await user_service.disable_user_card(card_id=card.id, user_id=owner.id)
    assert first_disabled.disabled_at is not None
    assert repeated.disabled_at == first_disabled.disabled_at

    terminal = await _create_issued_card(cards, users, owner_id=owner.id, admin_id=admin.id)
    await admin_service.mark_lost(card_id=terminal.id)
    with pytest.raises(CardTerminalStateError):
        await user_service.activate_user_card(card_id=terminal.id, user_id=owner.id)
    terminal_stored = await cards.find_by_id(terminal.id)
    assert terminal_stored is not None
    assert terminal_stored.status is CardStatus.LOST

    raced_terminal = await _create_issued_card(cards, users, owner_id=owner.id, admin_id=admin.id)
    race_results = await asyncio.gather(
        user_service.activate_user_card(card_id=raced_terminal.id, user_id=owner.id),
        admin_service.mark_lost(card_id=raced_terminal.id),
        return_exceptions=True,
    )
    assert any(isinstance(result, CardDocument) for result in race_results)
    raced_terminal_stored = await cards.find_by_id(raced_terminal.id)
    assert raced_terminal_stored is not None
    assert raced_terminal_stored.status in {CardStatus.ACTIVE, CardStatus.LOST}


@pytest.mark.mongo
@pytest.mark.asyncio
async def test_real_mongo_indexes_enforce_user_uniqueness(mongo_context) -> None:
    database, settings = mongo_context
    repository = UserRepository(database, settings)

    await repository.create(email="Person@example.com", password_hash="argon2-hash")
    with pytest.raises(RepositoryConflictError):
        await repository.create(email=" person@example.com ", password_hash="another-hash")

    index_info = await database[settings.mongodb_users_collection].index_information()
    assert "users_email_unique" in index_info
    assert index_info["users_email_unique"]["unique"] is True


@pytest.mark.mongo
@pytest.mark.asyncio
async def test_real_mongo_profile_upsert_has_one_profile_per_user(mongo_context) -> None:
    database, settings = mongo_context
    users = UserRepository(database, settings)
    profiles = ProfileRepository(database, settings)
    user = await users.create(email="owner@example.com", password_hash="argon2-hash")
    profile_input = ProfileUpsertInput(display_name="Owner")

    results = await asyncio.gather(
        *[profiles.upsert_for_user(user_id=user.id, profile=profile_input) for _ in range(2)]
    )

    stored = await profiles.find_by_user_id(user.id)
    assert stored is not None
    assert len({result.id for result in results}) == 1
    assert stored.user_id == user.id
    assert stored.created_at.tzinfo is not None
    assert stored.updated_at.tzinfo is not None
    assert await profiles.find_by_user_id("507f1f77bcf86cd799439099") is None
    assert await database[settings.mongodb_profiles_collection].count_documents({}) == 1


@pytest.mark.mongo
@pytest.mark.asyncio
async def test_real_mongo_profile_replace_preserves_legacy_public_access(mongo_context) -> None:
    database, settings = mongo_context
    users = UserRepository(database, settings)
    profiles = ProfileRepository(database, settings)
    user = await users.create(email="replace@example.com", password_hash="argon2-hash")
    await profiles.upsert_for_user(
        user_id=user.id,
        profile=ProfileUpsertInput(display_name="Before", emergency_contacts=[]),
    )
    published = await profiles.publish(user_id=user.id)
    assert published is not None
    created_at = published.created_at
    token = published.public_access.token

    replaced = await profiles.replace_for_user(
        user_id=user.id,
        profile=ProfileUpsertInput(display_name="After", emergency_contacts=[]),
    )

    assert replaced is not None
    assert replaced.display_name == "After"
    assert replaced.created_at == created_at
    assert replaced.public_access.token == token
    assert replaced.public_access.enabled is True
    assert replaced.updated_at >= created_at


@pytest.mark.mongo
@pytest.mark.asyncio
async def test_real_mongo_public_token_state_transitions_and_index(mongo_context) -> None:
    database, settings = mongo_context
    users = UserRepository(database, settings)
    profiles = ProfileRepository(database, settings)
    user = await users.create(email="public@example.com", password_hash="argon2-hash")
    await profiles.upsert_for_user(
        user_id=user.id, profile=ProfileUpsertInput(display_name="Public")
    )

    published = await profiles.publish(user_id=user.id)
    assert published is not None
    assert published.public_access.token is not None
    old_token = published.public_access.token
    assert await profiles.find_enabled_by_token(old_token) is not None

    disabled = await profiles.disable(user_id=user.id)
    assert disabled is not None
    assert await profiles.find_enabled_by_token(old_token) is None

    reenabled = await profiles.enable(user_id=user.id)
    assert reenabled is not None
    assert await profiles.find_enabled_by_token(old_token) is not None

    regenerated = await profiles.regenerate_token(user_id=user.id)
    assert regenerated is not None
    assert regenerated.public_access.token != old_token
    assert await profiles.find_enabled_by_token(old_token) is None
    assert await profiles.find_enabled_by_token(regenerated.public_access.token or "") is not None

    index_info = await database[settings.mongodb_profiles_collection].index_information()
    assert PROFILES_PUBLIC_TOKEN_INDEX in index_info


@pytest.mark.mongo
@pytest.mark.asyncio
async def test_real_mongo_blank_cards_allow_null_hashes_and_partial_token_index(
    mongo_context,
) -> None:
    database, settings = mongo_context
    cards = CardRepository(database, settings)

    first = await cards.create_blank_card(serial=generate_serial())
    second = await cards.create_blank_card(serial=generate_serial())
    assert first.token_hash is None
    assert second.token_hash is None

    index_info = await database[settings.mongodb_cards_collection].index_information()
    token_index = index_info[CARDS_TOKEN_HASH_INDEX]
    assert token_index["unique"] is True
    assert token_index["partialFilterExpression"] == {"token_hash": {"$type": "string"}}


@pytest.mark.mongo
@pytest.mark.asyncio
async def test_real_mongo_custody_event_is_atomic_with_assignment(mongo_context) -> None:
    database, settings = mongo_context
    hello = await database.client.admin.command("hello")
    if not hello.get("setName"):
        pytest.skip("MongoDB transactions require a replica set")

    users = UserRepository(database, settings)
    cards = CardRepository(database, settings)
    custody = CustodyEventRepository(database, settings)
    owner = await users.create(email="custody-owner@example.com", password_hash="argon2-hash")
    admin = await users.create(
        email="custody-admin@example.com", password_hash="argon2-hash", role="admin"
    )
    service = CardService(
        cards,
        users,
        public_card_base_url="https://app.example/e",
        custody_event_repository=custody,
    )

    blank = await service.create_blank_card()
    provisioned = await service.provision_link(card_id=blank.id)
    await service.confirm_encoding(
        card_id=blank.id,
        public_url=provisioned.public_url,
        admin_id=admin.id,
    )
    assigned = await service.assign_verified_to_user(
        card_id=blank.id,
        user_id=owner.id,
        admin_id=admin.id,
    )

    assert assigned.owner_id == owner.id
    events = await database[settings.mongodb_custody_events_collection].count_documents(
        {"card_id": blank.id, "event_type": "assigned"}
    )
    assert events == 1


@pytest.mark.mongo
@pytest.mark.asyncio
async def test_real_mongo_cards_support_multiple_active_cards_and_indexes(mongo_context) -> None:
    database, settings = mongo_context
    users = UserRepository(database, settings)
    cards = CardRepository(database, settings)
    service = CardService(cards, users)
    user = await users.create(email="cards@example.com", password_hash="argon2-hash")

    first = await service.provision_unassigned()
    second = await service.provision_unassigned()
    await service.assign_to_user(card_id=first.card.id, user_id=user.id)
    await service.assign_to_user(card_id=second.card.id, user_id=user.id)
    await service.activate(card_id=first.card.id, owner_id=user.id)
    await service.activate(card_id=second.card.id, owner_id=user.id)

    active = await cards.list_active_for_user(user.id)
    assert {card.id for card in active} == {first.card.id, second.card.id}
    stored = await database[settings.mongodb_cards_collection].find_one({"_id": first.card.id})
    assert stored is not None
    assert first.public_token not in stored.values()

    with pytest.raises(CardSerialConflictError):
        await cards.create_unassigned_card(
            serial=first.card.serial,
            token_hash=first.card.token_hash,
        )

    index_info = await database[settings.mongodb_cards_collection].index_information()
    assert {
        CARDS_SERIAL_INDEX,
        CARDS_TOKEN_HASH_INDEX,
        CARDS_OWNER_INDEX,
        CARDS_STATUS_INDEX,
        CARDS_OWNER_CURRENT_INDEX,
        CARDS_OWNER_STATUS_INDEX,
        CARDS_REPLACES_INDEX,
        CARDS_REPLACEMENT_INDEX,
    }.issubset(index_info)


@pytest.mark.mongo
@pytest.mark.asyncio
async def test_real_mongo_replacement_rolls_back_when_linking_fails(mongo_context) -> None:
    database, settings = mongo_context
    hello = await database.client.admin.command("hello")
    if not hello.get("setName"):
        pytest.skip("MongoDB transactions require a replica set")

    users = UserRepository(database, settings)
    cards = CardRepository(database, settings)
    service = CardService(cards, users)
    user = await users.create(email="replacement@example.com", password_hash="argon2-hash")
    original = await service.provision_unassigned()
    await service.assign_to_user(card_id=original.card.id, user_id=user.id)

    cards.link_replacement = AsyncMock(side_effect=RuntimeError("forced test failure"))  # type: ignore[method-assign]
    with pytest.raises(CardReplacementError):
        await service.replace(card_id=original.card.id)

    stored = await cards.find_by_id(original.card.id)
    assert stored is not None
    assert stored.status.value == "assigned"
    assert stored.replacement_card_id is None
    assert await database[settings.mongodb_cards_collection].count_documents({}) == 1


@pytest.mark.mongo
@pytest.mark.asyncio
async def test_real_mongo_incompatible_index_fails_visibly(mongo_context) -> None:
    database, settings = mongo_context
    collection = database[settings.mongodb_users_collection]
    await collection.drop_index("users_email_unique")
    await collection.create_index([("email", 1)], name="users_email_unique", unique=False)

    with pytest.raises(OperationFailure):
        await initialize_indexes(database, settings)
