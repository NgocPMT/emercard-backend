"""Anonymous public-profile lookup service."""

from __future__ import annotations

import re
from typing import Protocol

from bson.errors import InvalidId
from bson.objectid import ObjectId
from pymongo.errors import PyMongoError

from emercard.db.repositories import InvalidIdentifierError, RepositoryError
from emercard.modules.cards.identity import hash_public_token
from emercard.modules.profiles.models import (
    ProfileDocument,
    PublicProfileOutput,
    profile_readiness,
    to_public_profile,
)
from emercard.modules.public_links.errors import (
    PublicProfileDisabledError,
    PublicProfileExpiredError,
    PublicProfileNotFoundError,
    PublicProfileNotReadyError,
    PublicProfileRevokedError,
    PublicProfileServiceUnavailableError,
)
from emercard.modules.public_links.models import (
    PublicAccessLinkDocument,
    PublicAccessLinkStatus,
)

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class PublicAccessLinkRepositoryProtocol(Protocol):
    async def find_by_token_hash(
        self, token_hash: str, *, session: object | None = None
    ) -> PublicAccessLinkDocument | None: ...


class ProfileRepositoryProtocol(Protocol):
    async def find_by_id(self, profile_id: ObjectId | str) -> ProfileDocument | None: ...


class PublicProfileLookupService:
    """Resolve a bearer token without disclosing link or account state."""

    def __init__(
        self,
        link_repository: PublicAccessLinkRepositoryProtocol,
        profile_repository: ProfileRepositoryProtocol,
        *,
        token_max_length: int = 128,
    ) -> None:
        self._link_repository = link_repository
        self._profile_repository = profile_repository
        self._token_max_length = token_max_length

    async def lookup(self, raw_token: str) -> PublicProfileOutput:
        if not self._valid_token_shape(raw_token):
            raise PublicProfileNotFoundError

        try:
            link = await self._link_repository.find_by_token_hash(hash_public_token(raw_token))
        except (RepositoryError, PyMongoError) as error:
            raise PublicProfileServiceUnavailableError from error
        except (InvalidIdentifierError, InvalidId, ValueError) as error:
            raise PublicProfileNotFoundError from error

        if link is None:
            raise PublicProfileNotFoundError
        if link.status is PublicAccessLinkStatus.DISABLED:
            raise PublicProfileDisabledError
        if link.status is PublicAccessLinkStatus.REVOKED:
            raise PublicProfileRevokedError
        if link.status is PublicAccessLinkStatus.EXPIRED:
            raise PublicProfileExpiredError
        if link.status is PublicAccessLinkStatus.PENDING:
            raise PublicProfileNotReadyError

        try:
            profile = await self._profile_repository.find_by_id(link.profile_id)
        except (RepositoryError, PyMongoError) as error:
            raise PublicProfileServiceUnavailableError from error
        except (InvalidIdentifierError, InvalidId, ValueError) as error:
            raise PublicProfileNotFoundError from error

        if profile is None or profile_readiness(profile).status != "ready":
            raise PublicProfileNotReadyError

        try:
            return to_public_profile(profile)
        except ValueError as error:
            raise PublicProfileNotReadyError from error

    def _valid_token_shape(self, raw_token: str) -> bool:
        return bool(
            raw_token
            and len(raw_token) <= self._token_max_length
            and _TOKEN_PATTERN.fullmatch(raw_token)
        )
