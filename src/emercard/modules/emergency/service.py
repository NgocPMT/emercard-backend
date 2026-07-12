"""Application service for anonymous card-backed emergency lookup."""

from __future__ import annotations

import re
from typing import Protocol

from pymongo.errors import PyMongoError

from emercard.db.repositories import RepositoryError
from emercard.modules.cards.identity import hash_public_token
from emercard.modules.cards.models import CardDocument
from emercard.modules.emergency.errors import (
    EmergencyProfileNotFoundError,
    EmergencyProfileServiceUnavailableError,
)
from emercard.modules.profiles.models import ProfileDocument, PublicProfileOutput, to_public_profile

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class PublicCardRepositoryProtocol(Protocol):
    async def find_publicly_resolvable_by_token_hash(
        self, token_hash: str
    ) -> CardDocument | None: ...


class PublicProfileRepositoryProtocol(Protocol):
    async def find_by_user_id(self, user_id: str) -> ProfileDocument | None: ...


class EmergencyLookupService:
    """Resolve a bearer token without disclosing card or account state."""

    def __init__(
        self,
        card_repository: PublicCardRepositoryProtocol,
        profile_repository: PublicProfileRepositoryProtocol,
        *,
        token_max_length: int = 128,
    ) -> None:
        self._card_repository = card_repository
        self._profile_repository = profile_repository
        self._token_max_length = token_max_length

    async def lookup(self, raw_token: str) -> PublicProfileOutput:
        """Return the allowlisted profile or a privacy-preserving public error."""

        if not self._valid_token_shape(raw_token):
            raise EmergencyProfileNotFoundError

        try:
            card = await self._card_repository.find_publicly_resolvable_by_token_hash(
                hash_public_token(raw_token)
            )
        except (RepositoryError, PyMongoError) as error:
            raise EmergencyProfileServiceUnavailableError from error
        except ValueError as error:
            raise EmergencyProfileNotFoundError from error

        if card is None or card.owner_id is None:
            raise EmergencyProfileNotFoundError

        try:
            profile = await self._profile_repository.find_by_user_id(str(card.owner_id))
        except (RepositoryError, PyMongoError) as error:
            raise EmergencyProfileServiceUnavailableError from error
        except ValueError as error:
            raise EmergencyProfileNotFoundError from error

        if profile is None:
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
