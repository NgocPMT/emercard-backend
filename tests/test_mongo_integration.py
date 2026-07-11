"""Real MongoDB checks for indexes and atomic repository behavior."""

import asyncio
import os
from uuid import uuid4

import pytest
from pymongo.errors import OperationFailure

from emercard.core.config import Settings
from emercard.db import Database, initialize_indexes
from emercard.db.indexes import PROFILES_PUBLIC_TOKEN_INDEX
from emercard.db.repositories import RepositoryConflictError
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
        mongodb_database=f"emercard_test_{uuid4().hex}",
        mongodb_index_initialization_mode="disabled",
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
async def test_real_mongo_incompatible_index_fails_visibly(mongo_context) -> None:
    database, settings = mongo_context
    collection = database[settings.mongodb_users_collection]
    await collection.drop_index("users_email_unique")
    await collection.create_index([("email", 1)], name="users_email_unique", unique=False)

    with pytest.raises(OperationFailure):
        await initialize_indexes(database, settings)
