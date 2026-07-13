"""Application service for authenticated medical-profile operations."""

from typing import Protocol

from pymongo.errors import PyMongoError

from emercard.db.repositories import RepositoryError
from emercard.modules.profiles.exceptions import (
    ProfileProvisioningInconsistentError,
    ProfileServiceUnavailableError,
)
from emercard.modules.profiles.models import (
    ProfileDocument,
    ProfileUpsertInput,
    ProfileView,
    PublicProfileOutput,
    to_profile_view,
    to_public_profile,
)


class ProfileRepositoryProtocol(Protocol):
    async def find_by_user_id(self, user_id: str) -> ProfileDocument | None: ...

    async def upsert_for_user(
        self,
        *,
        user_id: str,
        profile: ProfileUpsertInput,
    ) -> ProfileDocument: ...


class ProfileService:
    """Coordinate owner-scoped profile persistence and sanitized projections."""

    def __init__(self, repository: ProfileRepositoryProtocol) -> None:
        self._repository = repository

    async def get_profile(self, *, user_id: str) -> ProfileView:
        profile = await self._load_optional(user_id=user_id)
        return to_profile_view(profile)

    async def replace_profile(
        self,
        *,
        user_id: str,
        request: ProfileUpsertInput,
    ) -> ProfileView:
        try:
            profile = await self._repository.upsert_for_user(user_id=user_id, profile=request)
        except (RepositoryError, PyMongoError) as error:
            raise ProfileServiceUnavailableError from error
        return to_profile_view(profile)

    async def get_public_preview(self, *, user_id: str) -> PublicProfileOutput:
        profile = await self._load_required(user_id=user_id)
        return to_public_profile(profile)

    async def _load_optional(self, *, user_id: str) -> ProfileDocument | None:
        try:
            profile = await self._repository.find_by_user_id(user_id)
        except (RepositoryError, PyMongoError) as error:
            raise ProfileServiceUnavailableError from error
        return profile

    async def _load_required(self, *, user_id: str) -> ProfileDocument:
        profile = await self._load_optional(user_id=user_id)
        if profile is None:
            raise ProfileProvisioningInconsistentError
        return profile
