"""Application service for anonymous emergency lookup via public links."""

from __future__ import annotations

import re
from typing import Protocol, cast

from bson.objectid import ObjectId
from pymongo.errors import PyMongoError

from emercard.db.repositories import InvalidIdentifierError, RepositoryError
from emercard.modules.card_link_assignments.models import CardLinkAssignmentDocument
from emercard.modules.cards.identity import hash_public_token
from emercard.modules.cards.models import CardDocument
from emercard.modules.emergency.errors import (
    EmergencyProfileNotFoundError,
    EmergencyProfileServiceUnavailableError,
)
from emercard.modules.profiles.models import (
    ProfileDocument,
    profile_readiness,
    to_public_profile,
)
from emercard.modules.public_links.models import (
    PublicAccessLinkDocument,
    PublicAccessLinkStatus,
    PublicLinkPurpose,
    PublicProfileLookupResult,
)

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class PublicAccessLinkRepositoryProtocol(Protocol):
    async def find_by_token_hash(self, token_hash: str) -> PublicAccessLinkDocument | None: ...


class PublicProfileRepositoryProtocol(Protocol):
    async def find_by_id(self, profile_id: ObjectId | str) -> ProfileDocument | None: ...
    async def find_by_user_id(self, user_id: ObjectId | str) -> ProfileDocument | None: ...


class CardLinkAssignmentRepositoryProtocol(Protocol):
    async def find_active_by_public_access_link_id(
        self, public_access_link_id: ObjectId | str, *, session: object | None = None
    ) -> CardLinkAssignmentDocument | None: ...


class LegacyCardTokenRepositoryProtocol(Protocol):
    async def find_publicly_resolvable_by_token_hash(
        self, token_hash: str, *, session: object | None = None
    ) -> CardDocument | None: ...


class EmergencyLookupService:
    """Resolve a bearer token without disclosing public-link or account state."""

    def __init__(
        self,
        link_repository: PublicAccessLinkRepositoryProtocol,
        profile_repository: PublicProfileRepositoryProtocol,
        *,
        assignment_repository: CardLinkAssignmentRepositoryProtocol | None = None,
        token_max_length: int = 128,
    ) -> None:
        self._link_repository = link_repository
        self._profile_repository = profile_repository
        self._assignment_repository = assignment_repository
        self._token_max_length = token_max_length

    async def lookup(self, raw_token: str) -> PublicProfileLookupResult:
        """Return the allowlisted profile or a privacy-preserving public error."""

        if not self._valid_token_shape(raw_token):
            raise EmergencyProfileNotFoundError

        token_hash = hash_public_token(raw_token)

        legacy_resolver = getattr(
            self._link_repository, "find_publicly_resolvable_by_token_hash", None
        )
        if callable(legacy_resolver):
            try:
                legacy_card = await cast(
                    LegacyCardTokenRepositoryProtocol, self._link_repository
                ).find_publicly_resolvable_by_token_hash(token_hash)
            except (InvalidIdentifierError, RepositoryError, PyMongoError, ValueError) as error:
                raise EmergencyProfileServiceUnavailableError from error
            if legacy_card is None:
                raise EmergencyProfileNotFoundError
            return await self._lookup_legacy_card(legacy_card)

        try:
            link = await self._link_repository.find_by_token_hash(token_hash)
        except (InvalidIdentifierError, RepositoryError, PyMongoError, ValueError) as error:
            raise EmergencyProfileServiceUnavailableError from error

        if link is None:
            raise EmergencyProfileNotFoundError
        if link.purpose is not PublicLinkPurpose.CARD:
            raise EmergencyProfileNotFoundError
        if link.status is not PublicAccessLinkStatus.ACTIVE:
            raise EmergencyProfileNotFoundError

        try:
            profile = await self._profile_repository.find_by_id(link.profile_id)
        except (InvalidIdentifierError, RepositoryError, PyMongoError, ValueError) as error:
            raise EmergencyProfileServiceUnavailableError from error

        if profile is None or profile_readiness(profile).status != "ready":
            raise EmergencyProfileNotFoundError
        try:
            public_profile = to_public_profile(profile)
        except ValueError as error:
            raise EmergencyProfileNotFoundError from error

        assignment_id = None
        card_id = None
        if self._assignment_repository is not None:
            try:
                assignment = await self._assignment_repository.find_active_by_public_access_link_id(
                    link.id
                )
            except (InvalidIdentifierError, RepositoryError, PyMongoError, ValueError) as error:
                raise EmergencyProfileServiceUnavailableError from error
            if assignment is not None:
                assignment_id = str(assignment.id)
                card_id = str(assignment.card_id)

        return PublicProfileLookupResult(
            profile=public_profile,
            link_id=str(link.id),
            purpose=link.purpose,
            assignment_id=assignment_id,
            card_id=card_id,
        )

    async def _lookup_legacy_card(self, card: CardDocument) -> PublicProfileLookupResult:
        if card.owner_id is None:
            raise EmergencyProfileNotFoundError
        if not card.is_current or card.issued_at is None or card.encoding_verified_at is None:
            raise EmergencyProfileNotFoundError

        try:
            profile = await self._profile_repository.find_by_user_id(card.owner_id)
        except (InvalidIdentifierError, RepositoryError, PyMongoError, ValueError) as error:
            raise EmergencyProfileServiceUnavailableError from error

        if profile is None or profile_readiness(profile).status != "ready":
            raise EmergencyProfileNotFoundError
        try:
            public_profile = to_public_profile(profile)
        except ValueError as error:
            raise EmergencyProfileNotFoundError from error

        return PublicProfileLookupResult(
            profile=public_profile,
            link_id=str(card.id),
            purpose=PublicLinkPurpose.CARD,
            assignment_id=None,
            card_id=str(card.id),
        )

    def _valid_token_shape(self, raw_token: str) -> bool:
        return bool(
            raw_token
            and len(raw_token) <= self._token_max_length
            and _TOKEN_PATTERN.fullmatch(raw_token)
        )
