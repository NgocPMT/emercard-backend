"""Typed MongoDB operations for profile-backed public links."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bson.errors import InvalidId
from bson.objectid import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from emercard.core.config import Settings
from emercard.core.types import utc_now
from emercard.db.repositories import InvalidIdentifierError, RepositoryConflictError
from emercard.modules.cards.identity import validate_token_hash
from emercard.modules.public_links.models import (
    PublicAccessLinkDocument,
    PublicAccessLinkStatus,
)

_MAX_TOKEN_RETRIES = 3


class PublicAccessLinkRepository:
    """Persist public-profile link state without owning authentication policy."""

    def __init__(self, database: Any, settings: Settings) -> None:
        self._collection = database[settings.mongodb_public_access_links_collection]

    async def find_by_profile_id(
        self, profile_id: ObjectId | str, *, session: Any | None = None
    ) -> PublicAccessLinkDocument | None:
        identifier = _object_id(profile_id)
        document = await self._collection.find_one(
            {"profile_id": identifier}, **_session_kwargs(session)
        )
        return _link(document)

    async def find_by_token_hash(
        self, token_hash: str, *, session: Any | None = None
    ) -> PublicAccessLinkDocument | None:
        canonical_hash = _validate_token_hash(token_hash)
        document = await self._collection.find_one(
            {"token_hash": canonical_hash}, **_session_kwargs(session)
        )
        return _link(document)

    async def find_active_by_token_hash(
        self, token_hash: str, *, session: Any | None = None
    ) -> PublicAccessLinkDocument | None:
        canonical_hash = _validate_token_hash(token_hash)
        document = await self._collection.find_one(
            {"token_hash": canonical_hash, "status": PublicAccessLinkStatus.ACTIVE},
            **_session_kwargs(session),
        )
        return _link(document)

    async def create_for_profile(
        self,
        *,
        profile_id: ObjectId | str,
        token_hash: str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument:
        timestamp = now or utc_now()
        identifier = _object_id(profile_id)
        canonical_hash = _validate_token_hash(token_hash)
        document = PublicAccessLinkDocument.model_validate(
            {
                "_id": ObjectId(),
                "profile_id": identifier,
                "token_hash": canonical_hash,
                "status": PublicAccessLinkStatus.ACTIVE,
                "created_at": timestamp,
                "updated_at": timestamp,
                "disabled_at": None,
            }
        )
        try:
            await self._collection.insert_one(
                document.model_dump(by_alias=True, mode="python"), **_session_kwargs(session)
            )
        except DuplicateKeyError:
            existing = await self.find_by_profile_id(identifier, session=session)
            if existing is not None:
                return existing
            raise RepositoryConflictError("public profile link creation conflict") from None
        return document

    async def rotate_for_profile(
        self,
        *,
        profile_id: ObjectId | str,
        token_hash: str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument:
        timestamp = now or utc_now()
        identifier = _object_id(profile_id)
        canonical_hash = _validate_token_hash(token_hash)
        for _ in range(_MAX_TOKEN_RETRIES):
            try:
                document = await self._collection.find_one_and_update(
                    {"profile_id": identifier},
                    {
                        "$set": {
                            "token_hash": canonical_hash,
                            "status": PublicAccessLinkStatus.ACTIVE,
                            "updated_at": timestamp,
                            "disabled_at": None,
                        },
                        "$setOnInsert": {
                            "_id": ObjectId(),
                            "profile_id": identifier,
                            "created_at": timestamp,
                        },
                    },
                    upsert=True,
                    return_document=ReturnDocument.AFTER,
                    **_session_kwargs(session),
                )
            except DuplicateKeyError:
                continue
            if document is None:
                raise RepositoryConflictError(
                    "public profile link rotate did not return a document"
                )
            return PublicAccessLinkDocument.model_validate(document)
        raise RepositoryConflictError("public profile link token could not be made unique")

    async def disable_for_profile(
        self,
        *,
        profile_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument | None:
        timestamp = now or utc_now()
        identifier = _object_id(profile_id)
        document = await self._collection.find_one_and_update(
            {"profile_id": identifier},
            {
                "$set": {
                    "status": PublicAccessLinkStatus.DISABLED,
                    "updated_at": timestamp,
                    "disabled_at": timestamp,
                }
            },
            return_document=ReturnDocument.AFTER,
            **_session_kwargs(session),
        )
        return _link(document)


def _link(document: Any) -> PublicAccessLinkDocument | None:
    return PublicAccessLinkDocument.model_validate(document) if document is not None else None


def _object_id(value: ObjectId | str) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(value)
    except (InvalidId, TypeError) as error:
        raise InvalidIdentifierError("invalid public profile link identifier") from error


def _session_kwargs(session: Any | None) -> dict[str, Any]:
    return {"session": session} if session is not None else {}


def _validate_token_hash(token_hash: str) -> str:
    try:
        return validate_token_hash(token_hash)
    except ValueError as error:
        raise InvalidIdentifierError("invalid public token hash") from error
