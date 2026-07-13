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
    PublicLinkPurpose,
    PublicProfileLinkResult,
)


class PublicAccessLinkRepositoryProtocol(Protocol):
    async def find_by_id(
        self, link_id: ObjectId | str, *, session: Any | None = None
    ) -> PublicAccessLinkDocument | None: ...

    async def list_by_profile_id(
        self,
        profile_id: ObjectId | str,
        *,
        purpose: PublicLinkPurpose | None = None,
        session: Any | None = None,
    ) -> list[PublicAccessLinkDocument]: ...

    async def find_by_profile_id_and_purpose(
        self,
        profile_id: ObjectId | str,
        *,
        purpose: PublicLinkPurpose,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument | None: ...

    async def find_by_token_hash(
        self, token_hash: str, *, session: Any | None = None
    ) -> PublicAccessLinkDocument | None: ...

    async def create_link(
        self,
        *,
        profile_id: ObjectId | str,
        purpose: PublicLinkPurpose,
        token_hash: str,
        label: str | None = None,
        status: PublicAccessLinkStatus = PublicAccessLinkStatus.ACTIVE,
        created_by: ObjectId | str | None = None,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument: ...

    async def rotate_link(
        self,
        *,
        link_id: ObjectId | str,
        token_hash: str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument: ...

    async def activate_link(
        self,
        *,
        link_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument | None: ...

    async def disable_link(
        self,
        *,
        link_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument | None: ...

    async def revoke_link(
        self,
        *,
        link_id: ObjectId | str,
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
        self._issued_tokens: dict[tuple[str, PublicLinkPurpose], _IssuedToken] = {}

    async def create_preview_link(
        self, *, profile_id: ObjectId | str, now: datetime | None = None
    ) -> PublicProfileLinkResult:
        profile = await self._load_ready_profile(profile_id)
        persisted, token = await self._create_link(
            profile.id,
            purpose=PublicLinkPurpose.STANDALONE,
            now=now,
            label="Xem trước",
        )
        issued = self._cache(profile.id, PublicLinkPurpose.STANDALONE, token)
        return PublicProfileLinkResult(
            action="create_preview_link",
            status="created",
            profile_id=str(profile.id),
            public_url=issued.public_url,
            raw_token=issued.raw_token,
            link_id=str(persisted.id),
            purpose=persisted.purpose,
        )

    async def generate(
        self, *, profile_id: ObjectId | str, now: datetime | None = None
    ) -> PublicProfileLinkResult:
        profile = await self._load_ready_profile(profile_id)
        latest = await self._latest_link(profile.id, purpose=PublicLinkPurpose.STANDALONE)
        if latest is not None and latest.status is PublicAccessLinkStatus.ACTIVE:
            cached = self._issued_tokens.get((str(profile.id), PublicLinkPurpose.STANDALONE))
            return PublicProfileLinkResult(
                action="generate",
                status="existing",
                profile_id=str(profile.id),
                public_url=cached.public_url if cached is not None else None,
                raw_token=cached.raw_token if cached is not None else None,
                link_id=str(latest.id),
                purpose=latest.purpose,
            )
        if latest is not None and latest.status is PublicAccessLinkStatus.DISABLED:
            persisted, token = await self._rotate_link(latest.id, purpose=latest.purpose, now=now)
            issued = self._cache(profile.id, PublicLinkPurpose.STANDALONE, token)
            return PublicProfileLinkResult(
                action="generate",
                status="reactivated",
                profile_id=str(profile.id),
                public_url=issued.public_url,
                raw_token=issued.raw_token,
                link_id=str(persisted.id),
                purpose=persisted.purpose,
            )
        persisted, token = await self._create_link(
            profile.id,
            purpose=PublicLinkPurpose.STANDALONE,
            now=now,
        )
        issued = self._cache(profile.id, PublicLinkPurpose.STANDALONE, token)
        return PublicProfileLinkResult(
            action="generate",
            status="created",
            profile_id=str(profile.id),
            public_url=issued.public_url,
            raw_token=issued.raw_token,
            link_id=str(persisted.id),
            purpose=persisted.purpose,
        )

    async def regenerate(
        self, *, profile_id: ObjectId | str, now: datetime | None = None
    ) -> PublicProfileLinkResult:
        profile = await self._load_ready_profile(profile_id)
        existing = await self._latest_link(profile.id, purpose=PublicLinkPurpose.STANDALONE)
        if existing is None:
            persisted, token = await self._create_link(
                profile.id,
                purpose=PublicLinkPurpose.STANDALONE,
                now=now,
            )
        else:
            persisted, token = await self._rotate_link(
                existing.id, purpose=existing.purpose, now=now
            )
        issued = self._cache(profile.id, PublicLinkPurpose.STANDALONE, token)
        return PublicProfileLinkResult(
            action="regenerate",
            status="rotated",
            profile_id=str(profile.id),
            public_url=issued.public_url,
            raw_token=issued.raw_token,
            link_id=str(persisted.id),
            purpose=persisted.purpose,
        )

    async def disable(
        self, *, profile_id: ObjectId | str, now: datetime | None = None
    ) -> PublicProfileLinkResult:
        profile = await self._load_profile(profile_id)
        existing = await self._load_link(profile.id, purpose=PublicLinkPurpose.STANDALONE)
        if existing is None:
            raise PublicProfileNotFoundError
        disabled = await self._persist_disable(existing.id, now=now)
        if disabled is None:
            raise PublicProfileNotFoundError
        self._issued_tokens.pop((str(profile.id), PublicLinkPurpose.STANDALONE), None)
        return PublicProfileLinkResult(
            action="disable",
            status="disabled",
            profile_id=str(profile.id),
            disabled=True,
            link_id=str(disabled.id),
            purpose=disabled.purpose,
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

    async def _load_link(
        self, profile_id: ObjectId | str, *, purpose: PublicLinkPurpose
    ) -> PublicAccessLinkDocument | None:
        try:
            return await self._link_repository.find_by_profile_id_and_purpose(
                profile_id, purpose=purpose
            )
        except (RepositoryError, PyMongoError) as error:
            raise PublicProfileServiceUnavailableError from error
        except (InvalidIdentifierError, InvalidId, ValueError) as error:
            raise PublicProfileNotFoundError from error

    async def _latest_link(
        self, profile_id: ObjectId | str, *, purpose: PublicLinkPurpose
    ) -> PublicAccessLinkDocument | None:
        try:
            links = await self._link_repository.list_by_profile_id(profile_id, purpose=purpose)
        except (RepositoryError, PyMongoError) as error:
            raise PublicProfileServiceUnavailableError from error
        except (InvalidIdentifierError, InvalidId, ValueError) as error:
            raise PublicProfileNotFoundError from error
        return links[0] if links else None

    async def _create_link(
        self,
        profile_id: ObjectId | str,
        *,
        purpose: PublicLinkPurpose,
        now: datetime | None = None,
        label: str | None = None,
    ) -> tuple[PublicAccessLinkDocument, str]:
        for _ in range(3):
            raw_token = self._new_token()
            token_hash = hash_public_token(raw_token)
            try:
                persisted = await self._link_repository.create_link(
                    profile_id=profile_id,
                    purpose=purpose,
                    token_hash=token_hash,
                    label=label,
                    now=now or utc_now(),
                )
            except RepositoryConflictError:
                continue
            except (RepositoryError, PyMongoError) as error:
                raise PublicProfileServiceUnavailableError from error
            return persisted, raw_token
        raise PublicProfileServiceUnavailableError

    async def _rotate_link(
        self,
        link_id: ObjectId | str,
        *,
        purpose: PublicLinkPurpose,
        now: datetime | None = None,
    ) -> tuple[PublicAccessLinkDocument, str]:
        for _ in range(3):
            raw_token = self._new_token()
            token_hash = hash_public_token(raw_token)
            try:
                persisted = await self._link_repository.rotate_link(
                    link_id=link_id,
                    token_hash=token_hash,
                    now=now or utc_now(),
                )
            except RepositoryConflictError:
                continue
            except (RepositoryError, PyMongoError) as error:
                raise PublicProfileServiceUnavailableError from error
            if persisted.purpose != purpose:
                raise PublicProfileServiceUnavailableError
            return persisted, raw_token
        raise PublicProfileServiceUnavailableError

    async def _persist_disable(
        self, link_id: ObjectId | str, *, now: datetime | None = None
    ) -> PublicAccessLinkDocument | None:
        try:
            return await self._link_repository.disable_link(link_id=link_id, now=now or utc_now())
        except (RepositoryError, PyMongoError) as error:
            raise PublicProfileServiceUnavailableError from error

    def _new_token(self) -> str:
        import secrets

        return secrets.token_urlsafe(32)

    def _cache(
        self, profile_id: ObjectId, purpose: PublicLinkPurpose, raw_token: str
    ) -> _IssuedToken:
        issued = _IssuedToken(
            raw_token=raw_token, public_url=f"{self._public_profile_base_url}/{raw_token}"
        )
        self._issued_tokens[(str(profile_id), purpose)] = issued
        return issued
