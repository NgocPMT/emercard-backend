from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from emercard.core.config import Settings
from emercard.db.repositories import RepositoryConflictError
from emercard.modules.profiles import ProfileRepository, ProfileUpsertInput
from emercard.modules.users import UserRepository

USER_ID = ObjectId("507f1f77bcf86cd799439011")
PROFILE_ID = ObjectId("507f1f77bcf86cd799439012")


def fake_database() -> tuple[MagicMock, MagicMock]:
    collection = MagicMock()
    database = MagicMock()
    database.__getitem__.return_value = collection
    return database, collection


def raw_profile(*, token: str | None = None, enabled: bool = False) -> dict[str, object]:
    return {
        "_id": PROFILE_ID,
        "user_id": USER_ID,
        "display_name": "Alex Example",
        "birth_year": 1995,
        "gender": "prefer_not_to_say",
        "blood_type": "O+",
        "critical_allergies": [],
        "important_conditions": [],
        "critical_medications": [],
        "emergency_note": None,
        "emergency_contacts": [
            {
                "id": "contact-1",
                "name": "Sam Example",
                "relationship": "Friend",
                "phone": "0901234567",
            }
        ],
        "public_access": {
            "token": token,
            "enabled": enabled,
            "published_at": "2026-01-01T00:00:00Z" if enabled else None,
            "regenerated_at": None,
        },
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_user_repository_stores_bson_object_id_for_new_users() -> None:
    database, collection = fake_database()
    collection.find_one = AsyncMock(return_value=None)
    collection.insert_one = AsyncMock()
    repository = UserRepository(database, Settings(environment="test"))

    user = await repository.create(email="person@example.com", password_hash="argon2-hash")

    inserted = collection.insert_one.await_args.args[0]
    assert inserted["_id"] == user.id
    assert isinstance(inserted["_id"], ObjectId)


@pytest.mark.asyncio
async def test_user_repository_rejects_existing_email_without_database_index() -> None:
    database, collection = fake_database()
    collection.find_one = AsyncMock(
        return_value={
            "_id": USER_ID,
            "email": "person@example.com",
            "password_hash": "argon2-hash",
            "role": "user",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    collection.insert_one = AsyncMock()
    repository = UserRepository(database, Settings(environment="test"))

    with pytest.raises(RepositoryConflictError):
        await repository.create(email=" Person@Example.COM ", password_hash="another-hash")

    assert collection.find_one.await_args.args[0] == {"email": "person@example.com"}
    collection.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_repository_reads_email_verification_metadata_from_legacy_accounts() -> None:
    database, collection = fake_database()
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(
        return_value=[
            {
                "_id": USER_ID,
                "email": "person@example.com",
                "password_hash": "argon2-hash",
                "role": "user",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "email_verified_at": None,
                "email_verification_token_hash": "token-hash",
                "email_verification_token_expires_at": "2026-01-02T00:00:00Z",
                "email_verification_last_sent_at": "2026-01-01T12:00:00Z",
            }
        ]
    )
    collection.find.return_value = cursor
    repository = UserRepository(database, Settings(environment="test"))

    users = await repository.list_current_users(limit=100)

    assert len(users) == 1
    assert users[0].email_verification_token_hash == "token-hash"
    assert users[0].email_verification_last_sent_at is not None


@pytest.mark.asyncio
async def test_user_repository_canonicalizes_lookup_and_maps_duplicate_email() -> None:
    database, collection = fake_database()
    collection.find_one = AsyncMock(return_value=None)
    collection.insert_one = AsyncMock(side_effect=DuplicateKeyError("duplicate"))
    repository = UserRepository(database, Settings(environment="test"))

    assert await repository.find_by_email(" Person@Example.COM ") is None
    assert collection.find_one.await_args.args[0] == {"email": "person@example.com"}

    with pytest.raises(RepositoryConflictError):
        await repository.create(email="person@example.com", password_hash="argon2-hash")


@pytest.mark.asyncio
async def test_profile_ensure_creates_empty_profile_without_overwriting_existing_data() -> None:
    database, collection = fake_database()
    collection.find_one_and_update = AsyncMock(return_value=raw_profile())
    repository = ProfileRepository(database, Settings(environment="test"))

    result = await repository.ensure_for_user(user_id=USER_ID)

    assert result.user_id == USER_ID
    update = collection.find_one_and_update.await_args.args[1]
    assert "$setOnInsert" in update
    assert update["$setOnInsert"]["user_id"] == USER_ID
    assert "display_name" not in update["$setOnInsert"]


@pytest.mark.asyncio
async def test_profile_repository_reads_legacy_phone_values() -> None:
    database, collection = fake_database()
    document = raw_profile()
    document["emergency_contacts"][0]["phone"] = "036493303822"  # type: ignore[index]
    collection.find_one = AsyncMock(return_value=document)
    repository = ProfileRepository(database, Settings(environment="test"))

    result = await repository.find_by_user_id(USER_ID)

    assert result is not None
    assert result.emergency_contacts[0].phone == "036493303822"


@pytest.mark.asyncio
async def test_profile_upsert_is_keyed_by_authenticated_user_and_generates_contact_id() -> None:
    database, collection = fake_database()
    collection.find_one_and_update = AsyncMock(return_value=raw_profile())
    repository = ProfileRepository(database, Settings(environment="test"))
    profile = ProfileUpsertInput(
        display_name="Alex Example",
        birth_year=1995,
        gender="prefer_not_to_say",
        blood_type="O+",
        emergency_contacts=[
            {"name": "Sam Example", "relationship": "Friend", "phone": "0901234567"}
        ],
    )

    result = await repository.upsert_for_user(user_id=USER_ID, profile=profile)

    assert result.user_id == USER_ID
    update = collection.find_one_and_update.await_args.args[1]
    assert update["$set"]["emergency_contacts"][0]["id"]
    assert update["$setOnInsert"]["user_id"] == USER_ID
    assert "user_id" not in update["$set"]


@pytest.mark.asyncio
async def test_profile_upsert_preserves_omitted_private_envelope_and_allows_explicit_removal(
) -> None:
    database, collection = fake_database()
    existing = raw_profile()
    existing["private_profile_envelope"] = {
        "version": 1,
        "kdf": {
            "algorithm": "argon2id",
            "salt": "c2FsdC1mb3ItcHJpdmF0ZQ==",
            "memory_cost_kib": 65_536,
            "time_cost": 3,
            "parallelism": 1,
        },
        "nonce": "bm9uY2UtMTIzNDU2Nzg=",
        "ciphertext": "ZW5jcnlwdGVk",
        "access_code_wrap": {
            "algorithm": "aes-256-gcm",
            "nonce": "d3JhcC1ub25jZS0xMjM=",
            "ciphertext": "YWNjZXNz",
        },
        "recovery_key_wrap": {
            "algorithm": "aes-256-gcm",
            "nonce": "cmVjb3Zlcnktbm9uY2Ux",
            "ciphertext": "cmVjb3Zlcnk=",
        },
    }
    collection.find_one_and_update = AsyncMock(return_value=existing)
    repository = ProfileRepository(database, Settings(environment="test"))
    ordinary = ProfileUpsertInput(display_name="Updated", emergency_contacts=[])

    await repository.upsert_for_user(user_id=USER_ID, profile=ordinary)
    ordinary_update = collection.find_one_and_update.await_args.args[1]
    assert "private_profile_envelope" not in ordinary_update["$set"]

    explicit_remove = ProfileUpsertInput(
        display_name="Updated",
        emergency_contacts=[],
        private_profile_envelope=None,
    )
    await repository.upsert_for_user(user_id=USER_ID, profile=explicit_remove)
    removal_update = collection.find_one_and_update.await_args.args[1]
    assert removal_update["$set"]["private_profile_envelope"] is None


@pytest.mark.asyncio
async def test_profile_replace_does_not_upsert_or_touch_server_controlled_fields() -> None:
    database, collection = fake_database()
    collection.find_one_and_update = AsyncMock(return_value=raw_profile(token="legacy-token"))
    repository = ProfileRepository(database, Settings(environment="test"))
    profile = ProfileUpsertInput(
        display_name="Updated",
        emergency_contacts=[
            {"name": "New Contact", "relationship": "Family", "phone": "0900000000"}
        ],
    )

    result = await repository.replace_for_user(user_id=USER_ID, profile=profile)

    assert result is not None
    call = collection.find_one_and_update.await_args
    assert call.kwargs.get("upsert") is None
    assert call.args[0] == {"user_id": USER_ID}
    assert "user_id" not in call.args[1]["$set"]
    assert "public_access" not in call.args[1]["$set"]
    assert call.args[1]["$set"]["display_name"] == "Updated"
    assert call.args[1]["$set"]["emergency_contacts"][0]["id"]


@pytest.mark.asyncio
async def test_public_token_lookup_requires_enabled_state_and_regeneration_replaces_token() -> None:
    database, collection = fake_database()
    collection.find_one = AsyncMock(return_value=raw_profile(token="new-token", enabled=True))
    collection.find_one_and_update = AsyncMock(
        return_value=raw_profile(token="new-token", enabled=True)
    )
    repository = ProfileRepository(database, Settings(environment="test"))

    result = await repository.find_enabled_by_token("new-token")
    assert result is not None
    assert collection.find_one.await_args.args[0] == {
        "public_access.token": "new-token",
        "public_access.enabled": True,
    }

    regenerated = await repository.regenerate_token(user_id=USER_ID)
    assert regenerated is not None
    update = collection.find_one_and_update.await_args.args[1]
    assert update["$set"]["public_access.token"]
    assert update["$set"]["public_access.token"] != "new-token"
