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
                "phone": "+84 90 123 4567",
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
            {"name": "Sam Example", "relationship": "Friend", "phone": "+84 90 123 4567"}
        ],
    )

    result = await repository.upsert_for_user(user_id=USER_ID, profile=profile)

    assert result.user_id == USER_ID
    update = collection.find_one_and_update.await_args.args[1]
    assert update["$set"]["emergency_contacts"][0]["id"]
    assert update["$setOnInsert"]["user_id"] == USER_ID
    assert "user_id" not in update["$set"]


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
