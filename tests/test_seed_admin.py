from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId
from pydantic import SecretStr

from emercard.core.config import Settings
from emercard.db.seed_admin import AdminSeedError, configured_credentials, seed_admin
from emercard.modules.auth.security import verify_password
from emercard.modules.users.repository import UserRepository

USER_ID = ObjectId("507f1f77bcf86cd799439011")


def repository_with_collection() -> tuple[UserRepository, MagicMock]:
    collection = MagicMock()
    database = MagicMock()
    database.__getitem__.return_value = collection
    return UserRepository(database, Settings(environment="test")), collection


def existing_user(*, role: str) -> dict[str, object]:
    return {
        "_id": USER_ID,
        "email": "admin@example.com",
        "password_hash": "argon2-hash",
        "role": role,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_seed_admin_creates_admin_with_hashed_password() -> None:
    repository, collection = repository_with_collection()
    collection.find_one = AsyncMock(return_value=None)
    collection.insert_one = AsyncMock()

    result = await seed_admin(
        repository,
        email=" Admin@Example.com ",
        password="password-123",
    )

    assert result.status == "created"
    assert result.email == "admin@example.com"
    inserted = collection.insert_one.await_args.args[0]
    assert inserted["role"] == "admin"
    assert verify_password("password-123", inserted["password_hash"])
    assert inserted["password_hash"] != "password-123"


@pytest.mark.asyncio
async def test_seed_admin_is_idempotent_for_existing_admin() -> None:
    repository, collection = repository_with_collection()
    collection.find_one = AsyncMock(return_value=existing_user(role="admin"))
    collection.insert_one = AsyncMock()

    result = await seed_admin(
        repository,
        email="admin@example.com",
        password="new-password-123",
    )

    assert result == type(result)(status="already_exists", email="admin@example.com")
    collection.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_seed_admin_does_not_elevate_existing_user() -> None:
    repository, collection = repository_with_collection()
    collection.find_one = AsyncMock(return_value=existing_user(role="user"))

    with pytest.raises(AdminSeedError, match="already exists as a user"):
        await seed_admin(
            repository,
            email="admin@example.com",
            password="password-123",
        )

    collection.insert_one.assert_not_called()


def test_configured_credentials_require_both_environment_values() -> None:
    with pytest.raises(AdminSeedError, match="EMERCARD_ADMIN_EMAIL"):
        configured_credentials(Settings(environment="test"))

    settings = Settings(
        environment="test",
        admin_email="admin@example.com",
        admin_password=SecretStr("password-123"),
    )
    assert configured_credentials(settings) == ("admin@example.com", "password-123")


def test_configured_credentials_reject_invalid_environment_values() -> None:
    settings = Settings(
        environment="test",
        admin_email="not-an-email",
        admin_password=SecretStr("short"),
    )

    with pytest.raises(AdminSeedError, match="EMERCARD_ADMIN_EMAIL"):
        configured_credentials(settings)
