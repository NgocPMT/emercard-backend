"""Application service for authenticated medical-profile operations."""

from typing import Protocol

from pymongo.errors import PyMongoError

from emercard.db.repositories import RepositoryError
from emercard.modules.profiles.exceptions import (
    ProfileProvisioningInconsistentError,
    ProfileServiceUnavailableError,
)
from emercard.modules.profiles.models import (
    AuthenticatedProfileOutput,
    ProfileDocument,
    ProfileUpsertInput,
    PublicProfileOutput,
    to_authenticated_profile,
    to_public_profile,
)


class ProfileRepositoryProtocol(Protocol):
    async def find_by_user_id(self, user_id: str) -> ProfileDocument | None: ...

    async def replace_for_user(
        self,
        *,
        user_id: str,
        profile: ProfileUpsertInput,
    ) -> ProfileDocument | None: ...


class ProfileService:
    """Coordinate owner-scoped profile persistence and sanitized projections."""

    def __init__(self, repository: ProfileRepositoryProtocol) -> None:
        self._repository = repository

    async def get_profile(self, *, user_id: str) -> AuthenticatedProfileOutput:
        profile = await self._load(user_id=user_id)
        return to_authenticated_profile(profile)

    async def replace_profile(
        self,
        *,
        user_id: str,
        request: ProfileUpsertInput,
    ) -> AuthenticatedProfileOutput:
        # The explicit read prevents the legacy upsert path from repairing an
        # inconsistent registration state before the non-upserting replacement.
        await self._load(user_id=user_id)
        try:
            profile = await self._repository.replace_for_user(
                user_id=user_id,
                profile=request,
            )
        except (RepositoryError, PyMongoError) as error:
            raise ProfileServiceUnavailableError from error
        if profile is None:
            raise ProfileProvisioningInconsistentError
        return to_authenticated_profile(profile)

    async def get_public_preview(self, *, user_id: str) -> PublicProfileOutput:
        profile = await self._load(user_id=user_id)
        return to_public_profile(profile)

    async def _load(self, *, user_id: str) -> ProfileDocument:
        try:
            profile = await self._repository.find_by_user_id(user_id)
        except (RepositoryError, PyMongoError) as error:
            raise ProfileServiceUnavailableError from error
        if profile is None:
            raise ProfileProvisioningInconsistentError
        return profile
