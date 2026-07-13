"""Lifecycle service for profile-backed public links."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from bson.errors import InvalidId
from bson.objectid import ObjectId
from pymongo.errors import PyMongoError

from emercard.core.types import utc_now
from emercard.db.repositories import (
    InvalidIdentifierError,
    RepositoryConflictError,
    RepositoryError,
)
from emercard.modules.cards.identity import hash_public_token
from emercard.modules.profiles.models import ProfileDocument, profile_readiness
from emercard.modules.public_links.errors import (
    PublicProfileNotFoundError,
    PublicProfileNotReadyError,
    PublicProfileServiceUnavailableError,
)
from emercard.modules.public_links.models import (
    PublicAccessLinkDocument,
    PublicAccessLinkStatus,
    PublicProfileLinkResult,
)


class PublicAccessLinkRepositoryProtocol(Protocol):
    async def find_by_profile_id(
        self, profile_id: ObjectId | str, *, session: Any | None = None
    ) -> PublicAccessLinkDocument | None: ...

    async def find_by_token_hash(
        self, token_hash: str, *, session: Any | None = None
    ) -> PublicAccessLinkDocument | None: ...

    async def create_for_profile(
        self,
        *,
        profile_id: ObjectId | str,
        token_hash: str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument: ...

    async def rotate_for_profile(
        self,
        *,
        profile_id: ObjectId | str,
        token_hash: str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument: ...

    async def disable_for_profile(
        self,
        *,
        profile_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument | None: ...


class ProfileRepositoryProtocol(Protocol):
    async def find_by_id(self, profile_id: ObjectId | str) -> ProfileDocument | None: ...


@dataclass
class _IssuedToken:
    raw_token: str
    public_url: str


class PublicProfileLinkService:
    """Coordinate link lifecycle validation and persistence for operators."""

    def __init__(
        self,
        link_repository: PublicAccessLinkRepositoryProtocol,
        profile_repository: ProfileRepositoryProtocol,
        *,
        public_profile_base_url: str,
    ) -> None:
        self._link_repository = link_repository
        self._profile_repository = profile_repository
        self._public_profile_base_url = public_profile_base_url.rstrip("/")
        self._issued_tokens: dict[str, _IssuedToken] = {}

    async def generate(
        self, *, profile_id: ObjectId | str, now: datetime | None = None
    ) -> PublicProfileLinkResult:
        profile = await self._load_ready_profile(profile_id)
        existing = await self._load_link(profile.id)
        if existing is not None and existing.status is PublicAccessLinkStatus.ACTIVE:
            cached = self._issued_tokens.get(str(profile.id))
            return PublicProfileLinkResult(
                action="generate",
                status="existing",
                profile_id=str(profile.id),
                public_url=cached.public_url if cached is not None else None,
                raw_token=cached.raw_token if cached is not None else None,
            )

        if existing is not None and existing.status is PublicAccessLinkStatus.DISABLED:
            _persisted, token = await self._rotate_link(profile.id, now=now)
            issued = self._cache(profile.id, token)
            return PublicProfileLinkResult(
                action="generate",
                status="reactivated",
                profile_id=str(profile.id),
                public_url=issued.public_url,
                raw_token=issued.raw_token,
            )

        _persisted, token = await self._create_link(profile.id, now=now)
        if token is None:
            cached = self._issued_tokens.get(str(profile.id))
            return PublicProfileLinkResult(
                action="generate",
                status="existing",
                profile_id=str(profile.id),
                public_url=cached.public_url if cached is not None else None,
                raw_token=cached.raw_token if cached is not None else None,
            )
        issued = self._cache(profile.id, token)
        return PublicProfileLinkResult(
            action="generate",
            status="created",
            profile_id=str(profile.id),
            public_url=issued.public_url,
            raw_token=issued.raw_token,
        )

    async def regenerate(
        self, *, profile_id: ObjectId | str, now: datetime | None = None
    ) -> PublicProfileLinkResult:
        profile = await self._load_ready_profile(profile_id)
        persisted, token = await self._rotate_link(profile.id, now=now)
        if persisted.token_hash != hash_public_token(token):
            raise PublicProfileServiceUnavailableError
        issued = self._cache(profile.id, token)
        return PublicProfileLinkResult(
            action="regenerate",
            status="rotated",
            profile_id=str(profile.id),
            public_url=issued.public_url,
            raw_token=issued.raw_token,
        )

    async def disable(
        self, *, profile_id: ObjectId | str, now: datetime | None = None
    ) -> PublicProfileLinkResult:
        profile = await self._load_profile(profile_id)
        disabled = await self._persist_disable(profile.id, now=now)
        if disabled is None:
            raise PublicProfileNotFoundError
        self._issued_tokens.pop(str(profile.id), None)
        return PublicProfileLinkResult(
            action="disable",
            status="disabled",
            profile_id=str(profile.id),
            disabled=True,
        )

    async def _load_profile(self, profile_id: ObjectId | str) -> ProfileDocument:
        try:
            profile = await self._profile_repository.find_by_id(profile_id)
        except (RepositoryError, PyMongoError) as error:
            raise PublicProfileServiceUnavailableError from error
        except (InvalidIdentifierError, InvalidId, ValueError) as error:
            raise PublicProfileNotFoundError from error
        if profile is None:
            raise PublicProfileNotFoundError
        return profile

    async def _load_ready_profile(self, profile_id: ObjectId | str) -> ProfileDocument:
        profile = await self._load_profile(profile_id)
        if profile_readiness(profile).status != "ready":
            raise PublicProfileNotReadyError
        return profile

    async def _load_link(self, profile_id: ObjectId | str) -> PublicAccessLinkDocument | None:
        try:
            return await self._link_repository.find_by_profile_id(profile_id)
        except (RepositoryError, PyMongoError) as error:
            raise PublicProfileServiceUnavailableError from error
        except (InvalidIdentifierError, InvalidId, ValueError) as error:
            raise PublicProfileNotFoundError from error

    async def _create_link(
        self, profile_id: ObjectId | str, *, now: datetime | None = None
    ) -> tuple[PublicAccessLinkDocument, str | None]:
        for _ in range(3):
            raw_token = self._new_token()
            token_hash = hash_public_token(raw_token)
            try:
                persisted = await self._link_repository.create_for_profile(
                    profile_id=profile_id,
                    token_hash=token_hash,
                    now=now or utc_now(),
                )
            except RepositoryConflictError:
                continue
            except (RepositoryError, PyMongoError) as error:
                raise PublicProfileServiceUnavailableError from error
            if persisted.token_hash == token_hash:
                return persisted, raw_token
            return persisted, None
        raise PublicProfileServiceUnavailableError

    async def _rotate_link(
        self, profile_id: ObjectId | str, *, now: datetime | None = None
    ) -> tuple[PublicAccessLinkDocument, str]:
        for _ in range(3):
            raw_token = self._new_token()
            token_hash = hash_public_token(raw_token)
            try:
                persisted = await self._link_repository.rotate_for_profile(
                    profile_id=profile_id,
                    token_hash=token_hash,
                    now=now or utc_now(),
                )
            except RepositoryConflictError:
                continue
            except (RepositoryError, PyMongoError) as error:
                raise PublicProfileServiceUnavailableError from error
            if persisted.token_hash == token_hash:
                return persisted, raw_token
        raise PublicProfileServiceUnavailableError

    async def _persist_disable(
        self, profile_id: ObjectId | str, *, now: datetime | None = None
    ) -> PublicAccessLinkDocument | None:
        try:
            return await self._link_repository.disable_for_profile(
                profile_id=profile_id, now=now or utc_now()
            )
        except (RepositoryError, PyMongoError) as error:
            raise PublicProfileServiceUnavailableError from error

    def _new_token(self) -> str:
        import secrets

        return secrets.token_urlsafe(32)

    def _cache(self, profile_id: ObjectId, raw_token: str) -> _IssuedToken:
        issued = _IssuedToken(
            raw_token=raw_token, public_url=f"{self._public_profile_base_url}/{raw_token}"
        )
        self._issued_tokens[str(profile_id)] = issued
        return issued
