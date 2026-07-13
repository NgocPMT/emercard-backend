"""Typed MongoDB operations for profile-backed public links."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bson.errors import InvalidId
from bson.objectid import ObjectId
from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from emercard.core.config import Settings
from emercard.core.types import utc_now
from emercard.db.repositories import InvalidIdentifierError, RepositoryConflictError
from emercard.modules.cards.identity import validate_token_hash
from emercard.modules.public_links.models import (
    PublicAccessLinkDocument,
    PublicAccessLinkStatus,
    PublicLinkPurpose,
)

_MAX_TOKEN_RETRIES = 3


class PublicAccessLinkRepository:
    """Persist public-profile link state without owning authentication policy."""

    def __init__(self, database: Any, settings: Settings) -> None:
        self._collection = database[settings.mongodb_public_access_links_collection]

    async def find_by_id(
        self, link_id: ObjectId | str, *, session: Any | None = None
    ) -> PublicAccessLinkDocument | None:
        identifier = _object_id(link_id)
        document = await self._collection.find_one(
            _identifier_query("_id", identifier), **_session_kwargs(session)
        )
        return _link(document)

    async def list_by_profile_id(
        self,
        profile_id: ObjectId | str,
        *,
        purpose: PublicLinkPurpose | None = None,
        session: Any | None = None,
    ) -> list[PublicAccessLinkDocument]:
        identifier = _object_id(profile_id)
        query: dict[str, Any] = _identifier_query("profile_id", identifier)
        if purpose is not None:
            query = {"$and": [query, {"purpose": purpose.value}]}
        cursor = self._collection.find(query, **_session_kwargs(session)).sort(
            [("created_at", DESCENDING), ("_id", DESCENDING)]
        )
        return _links(await cursor.to_list(length=None))

    async def find_by_profile_id_and_purpose(
        self,
        profile_id: ObjectId | str,
        *,
        purpose: PublicLinkPurpose,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument | None:
        identifier = _object_id(profile_id)
        cursor = (
            self._collection.find(
                {
                    "$and": [
                        _identifier_query("profile_id", identifier),
                        {"purpose": purpose.value},
                        {"status": PublicAccessLinkStatus.ACTIVE},
                    ]
                },
                **_session_kwargs(session),
            )
            .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
            .limit(1)
        )
        documents = await cursor.to_list(length=1)
        return _link(documents[0]) if documents else None

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
    ) -> PublicAccessLinkDocument:
        timestamp = now or utc_now()
        identifier = _object_id(profile_id)
        canonical_hash = _validate_token_hash(token_hash)
        document = PublicAccessLinkDocument.model_validate(
            {
                "_id": ObjectId(),
                "profile_id": identifier,
                "purpose": purpose,
                "label": label,
                "token_hash": canonical_hash,
                "status": status,
                "created_by": _optional_object_id(created_by),
                "created_at": timestamp,
                "updated_at": timestamp,
                "activated_at": timestamp if status is PublicAccessLinkStatus.ACTIVE else None,
                "disabled_at": timestamp if status is PublicAccessLinkStatus.DISABLED else None,
                "revoked_at": timestamp if status is PublicAccessLinkStatus.REVOKED else None,
                "expires_at": timestamp if status is PublicAccessLinkStatus.EXPIRED else None,
                "expired_at": timestamp if status is PublicAccessLinkStatus.EXPIRED else None,
            }
        )
        try:
            await self._collection.insert_one(
                document.model_dump(by_alias=True, mode="python"), **_session_kwargs(session)
            )
        except DuplicateKeyError as error:
            raise RepositoryConflictError("public profile link creation conflict") from error
        return document

    async def rotate_link(
        self,
        *,
        link_id: ObjectId | str,
        token_hash: str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument:
        timestamp = now or utc_now()
        identifier = _object_id(link_id)
        canonical_hash = _validate_token_hash(token_hash)
        for _ in range(_MAX_TOKEN_RETRIES):
            try:
                document = await self._collection.find_one_and_update(
                    _identifier_query("_id", identifier),
                    {
                        "$set": {
                            "token_hash": canonical_hash,
                            "status": PublicAccessLinkStatus.ACTIVE,
                            "updated_at": timestamp,
                            "activated_at": timestamp,
                            "disabled_at": None,
                            "revoked_at": None,
                            "expires_at": None,
                            "expired_at": None,
                        }
                    },
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

    async def activate_link(
        self,
        *,
        link_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument | None:
        timestamp = now or utc_now()
        identifier = _object_id(link_id)
        document = await self._collection.find_one_and_update(
            _identifier_query("_id", identifier),
            {
                "$set": {
                    "status": PublicAccessLinkStatus.ACTIVE,
                    "updated_at": timestamp,
                    "activated_at": timestamp,
                    "disabled_at": None,
                    "revoked_at": None,
                    "expires_at": None,
                    "expired_at": None,
                }
            },
            return_document=ReturnDocument.AFTER,
            **_session_kwargs(session),
        )
        return _link(document)

    async def disable_link(
        self,
        *,
        link_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument | None:
        timestamp = now or utc_now()
        identifier = _object_id(link_id)
        document = await self._collection.find_one_and_update(
            _identifier_query("_id", identifier),
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

    async def revoke_link(
        self,
        *,
        link_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument | None:
        timestamp = now or utc_now()
        identifier = _object_id(link_id)
        document = await self._collection.find_one_and_update(
            _identifier_query("_id", identifier),
            {
                "$set": {
                    "status": PublicAccessLinkStatus.REVOKED,
                    "updated_at": timestamp,
                    "revoked_at": timestamp,
                }
            },
            return_document=ReturnDocument.AFTER,
            **_session_kwargs(session),
        )
        return _link(document)

    async def expire_link(
        self,
        *,
        link_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument | None:
        timestamp = now or utc_now()
        identifier = _object_id(link_id)
        document = await self._collection.find_one_and_update(
            _identifier_query("_id", identifier),
            {
                "$set": {
                    "status": PublicAccessLinkStatus.EXPIRED,
                    "updated_at": timestamp,
                    "expires_at": timestamp,
                    "expired_at": timestamp,
                }
            },
            return_document=ReturnDocument.AFTER,
            **_session_kwargs(session),
        )
        return _link(document)

    # Legacy compatibility wrappers for the existing profile-preview flow.
    async def find_by_profile_id(
        self, profile_id: ObjectId | str, *, session: Any | None = None
    ) -> PublicAccessLinkDocument | None:
        return await self.find_by_profile_id_and_purpose(
            profile_id, purpose=PublicLinkPurpose.STANDALONE, session=session
        )

    async def create_for_profile(
        self,
        *,
        profile_id: ObjectId | str,
        token_hash: str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument:
        return await self.create_link(
            profile_id=profile_id,
            purpose=PublicLinkPurpose.STANDALONE,
            token_hash=token_hash,
            now=now,
            session=session,
        )

    async def rotate_for_profile(
        self,
        *,
        profile_id: ObjectId | str,
        token_hash: str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument:
        link = await self.find_by_profile_id_and_purpose(
            profile_id, purpose=PublicLinkPurpose.STANDALONE, session=session
        )
        if link is None:
            return await self.create_link(
                profile_id=profile_id,
                purpose=PublicLinkPurpose.STANDALONE,
                token_hash=token_hash,
                now=now,
                session=session,
            )
        return await self.rotate_link(
            link_id=link.id, token_hash=token_hash, now=now, session=session
        )

    async def disable_for_profile(
        self,
        *,
        profile_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> PublicAccessLinkDocument | None:
        link = await self.find_by_profile_id_and_purpose(
            profile_id, purpose=PublicLinkPurpose.STANDALONE, session=session
        )
        if link is None:
            return None
        return await self.disable_link(link_id=link.id, now=now, session=session)


def _link(document: Any) -> PublicAccessLinkDocument | None:
    return PublicAccessLinkDocument.model_validate(document) if document is not None else None


def _links(documents: list[Any]) -> list[PublicAccessLinkDocument]:
    return [PublicAccessLinkDocument.model_validate(document) for document in documents]


def _identifier_query(field: str, identifier: ObjectId | str) -> dict[str, Any]:
    return {"$or": [{field: identifier}, {field: str(identifier)}]}


def _object_id(value: ObjectId | str) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(value)
    except (InvalidId, TypeError) as error:
        raise InvalidIdentifierError("invalid public profile link identifier") from error


def _optional_object_id(value: ObjectId | str | None) -> ObjectId | None:
    if value is None:
        return None
    return _object_id(value)


def _session_kwargs(session: Any | None) -> dict[str, Any]:
    return {"session": session} if session is not None else {}


def _validate_token_hash(token_hash: str) -> str:
    try:
        return validate_token_hash(token_hash)
    except ValueError as error:
        raise InvalidIdentifierError("invalid public token hash") from error
