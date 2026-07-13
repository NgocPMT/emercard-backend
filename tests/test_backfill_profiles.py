from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from emercard.core.config import Settings
from emercard.db.backfill_profiles import backfill


class FakeDatabase:
    instances: list[FakeDatabase] = []

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = object()
        self.started = False
        self.closed = False
        FakeDatabase.instances.append(self)

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_backfill_creates_only_missing_profiles_and_returns_safe_counts() -> None:
    FakeDatabase.instances.clear()
    settings = Settings(environment="test", auth_secret=SecretStr("x" * 32))
    users_repository = MagicMock()
    users_repository.find_all_ids = AsyncMock(return_value=["user-1", "user-2"])

    profiles_repository = MagicMock()

    async def find_by_user_id(user_id: str) -> object | None:
        return object() if user_id == "user-2" else None

    profiles_repository.find_by_user_id = AsyncMock(side_effect=find_by_user_id)
    profiles_repository.ensure_for_user = AsyncMock()

    with (
        patch("emercard.db.backfill_profiles.get_settings", return_value=settings),
        patch("emercard.db.backfill_profiles.Database", FakeDatabase),
        patch("emercard.db.backfill_profiles.UserRepository", return_value=users_repository),
        patch(
            "emercard.db.backfill_profiles.ProfileRepository",
            return_value=profiles_repository,
        ),
    ):
        result = await backfill()

    assert result == {"users_scanned": 2, "profiles_created": 1}
    assert FakeDatabase.instances[0].started is True
    assert FakeDatabase.instances[0].closed is True
    users_repository.find_all_ids.assert_awaited_once()
    profiles_repository.find_by_user_id.assert_any_await("user-1")
    profiles_repository.find_by_user_id.assert_any_await("user-2")
    profiles_repository.ensure_for_user.assert_awaited_once_with(user_id="user-1")


@pytest.mark.asyncio
async def test_backfill_is_idempotent_when_all_profiles_exist() -> None:
    FakeDatabase.instances.clear()
    settings = Settings(environment="test", auth_secret=SecretStr("x" * 32))
    users_repository = MagicMock()
    users_repository.find_all_ids = AsyncMock(return_value=["user-1"])

    profiles_repository = MagicMock()
    profiles_repository.find_by_user_id = AsyncMock(return_value=object())
    profiles_repository.ensure_for_user = AsyncMock()

    with (
        patch("emercard.db.backfill_profiles.get_settings", return_value=settings),
        patch("emercard.db.backfill_profiles.Database", FakeDatabase),
        patch("emercard.db.backfill_profiles.UserRepository", return_value=users_repository),
        patch(
            "emercard.db.backfill_profiles.ProfileRepository",
            return_value=profiles_repository,
        ),
    ):
        result = await backfill()

    assert result == {"users_scanned": 1, "profiles_created": 0}
    profiles_repository.ensure_for_user.assert_not_awaited()
