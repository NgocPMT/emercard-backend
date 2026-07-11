"""Typed, ownership-scoped persistence operations for medical profiles."""

import secrets
from datetime import datetime
from typing import Any

from bson.errors import InvalidId
from bson.objectid import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from emercard.core.config import Settings
from emercard.core.types import utc_now
from emercard.db.repositories import InvalidIdentifierError, RepositoryConflictError
from emercard.modules.profiles.models import (
    EmergencyContactDocument,
    ProfileDocument,
    ProfileUpsertInput,
)

_MAX_TOKEN_RETRIES = 3


class ProfileRepository:
    """Persist profiles using user ownership and atomic MongoDB updates."""

    def __init__(self, database: Any, settings: Settings) -> None:
        self._collection = database[settings.mongodb_profiles_collection]
        self._token_bytes = settings.public_link_token_bytes

    async def find_by_user_id(self, user_id: ObjectId | str) -> ProfileDocument | None:
        identifier = _object_id(user_id)
        document = await self._collection.find_one({"user_id": identifier})
        return _profile(document)

    async def ensure_for_user(
        self,
        *,
        user_id: ObjectId | str,
        now: datetime | None = None,
    ) -> ProfileDocument:
        """Create the owner's empty profile without changing an existing profile."""

        identifier = _object_id(user_id)
        timestamp = now or utc_now()
        update: dict[str, Any] = {
            "$setOnInsert": {
                "_id": ObjectId(),
                "user_id": identifier,
                "critical_allergies": [],
                "important_conditions": [],
                "critical_medications": [],
                "emergency_contacts": [],
                "public_access": {
                    "token": None,
                    "enabled": False,
                    "published_at": None,
                    "regenerated_at": None,
                },
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        }
        try:
            document = await self._collection.find_one_and_update(
                {"user_id": identifier},
                update,
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            document = await self._collection.find_one({"user_id": identifier})
        if document is None:
            raise RepositoryConflictError("profile ensure did not return a document")
        return ProfileDocument.model_validate(document)

    async def upsert_for_user(
        self,
        *,
        user_id: ObjectId | str,
        profile: ProfileUpsertInput,
        now: datetime | None = None,
    ) -> ProfileDocument:
        """Atomically create or update the single profile owned by a user."""

        identifier = _object_id(user_id)
        timestamp = now or utc_now()
        profile_fields = _profile_fields(profile)
        update = {
            "$set": {**profile_fields, "updated_at": timestamp},
            "$setOnInsert": {
                "_id": ObjectId(),
                "user_id": identifier,
                "created_at": timestamp,
                "public_access": {
                    "token": None,
                    "enabled": False,
                    "published_at": None,
                    "regenerated_at": None,
                },
            },
        }
        try:
            document = await self._collection.find_one_and_update(
                {"user_id": identifier},
                update,
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as error:
            # A concurrent first upsert may win the unique user_id index. Retry
            # as a non-upsert update rather than creating a second profile.
            try:
                document = await self._collection.find_one_and_update(
                    {"user_id": identifier},
                    {"$set": {**profile_fields, "updated_at": timestamp}},
                    return_document=ReturnDocument.AFTER,
                )
            except DuplicateKeyError as retry_error:
                raise RepositoryConflictError("profile ownership conflict") from retry_error
            if document is None:
                raise RepositoryConflictError("profile ownership conflict") from error
        if document is None:
            raise RepositoryConflictError("profile upsert did not return a document")
        return ProfileDocument.model_validate(document)

    async def replace_for_user(
        self,
        *,
        user_id: ObjectId | str,
        profile: ProfileUpsertInput,
        now: datetime | None = None,
    ) -> ProfileDocument | None:
        """Atomically replace editable fields without creating a missing profile."""

        identifier = _object_id(user_id)
        timestamp = now or utc_now()
        document = await self._collection.find_one_and_update(
            {"user_id": identifier},
            {"$set": {**_profile_fields(profile), "updated_at": timestamp}},
            return_document=ReturnDocument.AFTER,
        )
        return _profile(document)

    async def publish(
        self,
        *,
        user_id: ObjectId | str,
        now: datetime | None = None,
    ) -> ProfileDocument | None:
        """Generate and enable a new public token atomically."""

        identifier = _object_id(user_id)
        timestamp = now or utc_now()
        for _ in range(_MAX_TOKEN_RETRIES):
            token = secrets.token_urlsafe(self._token_bytes)
            try:
                document = await self._collection.find_one_and_update(
                    {"user_id": identifier},
                    {
                        "$set": {
                            "public_access.token": token,
                            "public_access.enabled": True,
                            "public_access.published_at": timestamp,
                            "public_access.regenerated_at": timestamp,
                            "updated_at": timestamp,
                        }
                    },
                    return_document=ReturnDocument.AFTER,
                )
            except DuplicateKeyError:
                continue
            return _profile(document)
        raise RepositoryConflictError("could not generate a unique public token")

    async def enable(
        self, *, user_id: ObjectId | str, now: datetime | None = None
    ) -> ProfileDocument | None:
        identifier = _object_id(user_id)
        timestamp = now or utc_now()
        document = await self._collection.find_one_and_update(
            {"user_id": identifier, "public_access.token": {"$type": "string"}},
            {
                "$set": {
                    "public_access.enabled": True,
                    "public_access.published_at": timestamp,
                    "updated_at": timestamp,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return _profile(document)

    async def disable(
        self, *, user_id: ObjectId | str, now: datetime | None = None
    ) -> ProfileDocument | None:
        identifier = _object_id(user_id)
        timestamp = now or utc_now()
        document = await self._collection.find_one_and_update(
            {"user_id": identifier},
            {
                "$set": {
                    "public_access.enabled": False,
                    "updated_at": timestamp,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return _profile(document)

    async def regenerate_token(
        self,
        *,
        user_id: ObjectId | str,
        now: datetime | None = None,
    ) -> ProfileDocument | None:
        """Replace the token in one update; the previous token stops resolving immediately."""

        identifier = _object_id(user_id)
        timestamp = now or utc_now()
        for _ in range(_MAX_TOKEN_RETRIES):
            token = secrets.token_urlsafe(self._token_bytes)
            try:
                document = await self._collection.find_one_and_update(
                    {"user_id": identifier},
                    {
                        "$set": {
                            "public_access.token": token,
                            "public_access.regenerated_at": timestamp,
                            "updated_at": timestamp,
                        }
                    },
                    return_document=ReturnDocument.AFTER,
                )
            except DuplicateKeyError:
                continue
            return _profile(document)
        raise RepositoryConflictError("could not generate a unique public token")

    async def find_enabled_by_token(self, token: str) -> ProfileDocument | None:
        if not token:
            return None
        document = await self._collection.find_one(
            {"public_access.token": token, "public_access.enabled": True}
        )
        return _profile(document)


def _profile_fields(profile: ProfileUpsertInput) -> dict[str, Any]:
    profile_fields = profile.model_dump(mode="python")
    profile_fields["emergency_contacts"] = [
        EmergencyContactDocument.model_validate(contact.model_dump()).model_dump(mode="python")
        for contact in profile.emergency_contacts
    ]
    return profile_fields


def _profile(document: Any) -> ProfileDocument | None:
    return ProfileDocument.model_validate(document) if document is not None else None


def _object_id(value: ObjectId | str) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(value)
    except (InvalidId, TypeError) as error:
        raise InvalidIdentifierError("invalid profile owner identifier") from error
