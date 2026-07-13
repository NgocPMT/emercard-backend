"""Application service for anonymous emergency lookup via public links."""

from __future__ import annotations

import re
from typing import Protocol

from bson.objectid import ObjectId
from pymongo.errors import PyMongoError

from emercard.db.repositories import InvalidIdentifierError, RepositoryError
from emercard.modules.cards.identity import hash_public_token
from emercard.modules.emergency.errors import (
    EmergencyProfileNotFoundError,
    EmergencyProfileServiceUnavailableError,
)
from emercard.modules.profiles.models import (
    ProfileDocument,
    PublicProfileOutput,
    profile_readiness,
    to_public_profile,
)
from emercard.modules.public_links.models import (
    PublicAccessLinkDocument,
    PublicAccessLinkStatus,
    PublicLinkPurpose,
)

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class PublicAccessLinkRepositoryProtocol(Protocol):
    async def find_by_token_hash(self, token_hash: str) -> PublicAccessLinkDocument | None: ...


class PublicProfileRepositoryProtocol(Protocol):
    async def find_by_id(self, profile_id: ObjectId | str) -> ProfileDocument | None: ...


class EmergencyLookupService:
    """Resolve a bearer token without disclosing public-link or account state."""

    def __init__(
        self,
        link_repository: PublicAccessLinkRepositoryProtocol,
        profile_repository: PublicProfileRepositoryProtocol,
        *,
        token_max_length: int = 128,
    ) -> None:
        self._link_repository = link_repository
        self._profile_repository = profile_repository
        self._token_max_length = token_max_length

    async def lookup(self, raw_token: str) -> PublicProfileOutput:
        """Return the allowlisted profile or a privacy-preserving public error."""

        if not self._valid_token_shape(raw_token):
            raise EmergencyProfileNotFoundError

        try:
            link = await self._link_repository.find_by_token_hash(hash_public_token(raw_token))
        except (InvalidIdentifierError, RepositoryError, PyMongoError, ValueError) as error:
            raise EmergencyProfileServiceUnavailableError from error

        if (
            link is None
            or link.purpose is not PublicLinkPurpose.CARD
            or link.status is not PublicAccessLinkStatus.ACTIVE
        ):
            raise EmergencyProfileNotFoundError

        try:
            profile = await self._profile_repository.find_by_id(link.profile_id)
        except (InvalidIdentifierError, RepositoryError, PyMongoError, ValueError) as error:
            raise EmergencyProfileServiceUnavailableError from error

        if profile is None or profile_readiness(profile).status != "ready":
            raise EmergencyProfileNotFoundError
        try:
            return to_public_profile(profile)
        except ValueError as error:
            raise EmergencyProfileNotFoundError from error

    def _valid_token_shape(self, raw_token: str) -> bool:
        return bool(
            raw_token
            and len(raw_token) <= self._token_max_length
            and _TOKEN_PATTERN.fullmatch(raw_token)
        )
