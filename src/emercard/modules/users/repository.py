"""Typed persistence operations for user documents."""

from datetime import datetime
from typing import Any

from bson.errors import InvalidId
from bson.objectid import ObjectId
from pymongo.errors import DuplicateKeyError

from emercard.core.config import Settings
from emercard.core.types import utc_now
from emercard.db.repositories import InvalidIdentifierError, RepositoryConflictError
from emercard.modules.users.models import UserDocument, UserRole, canonicalize_email


class UserRepository:
    """Persist users through the database selected by the application lifecycle."""

    def __init__(self, database: Any, settings: Settings) -> None:
        self._collection = database[settings.mongodb_users_collection]

    async def find_by_email(self, email: str) -> UserDocument | None:
        document = await self._collection.find_one({"email": canonicalize_email(email)})
        return UserDocument.model_validate(document) if document is not None else None

    async def find_by_id(self, user_id: ObjectId | str) -> UserDocument | None:
        identifier = _object_id(user_id)
        document = await self._collection.find_one({"_id": identifier})
        if document is None:
            # Read legacy local accounts created before ObjectId preservation was fixed.
            document = await self._collection.find_one({"_id": str(identifier)})
        return UserDocument.model_validate(document) if document is not None else None

    async def find_all_ids(self) -> list[str]:
        """Return user identifiers for safe maintenance/backfill operations."""

        cursor = self._collection.find({}, projection={"_id": 1})
        documents: list[Any] = await cursor.to_list(length=None)
        return [str(document["_id"]) for document in documents]

    async def create(
        self,
        *,
        email: str,
        password_hash: str,
        role: UserRole = "user",
        now: datetime | None = None,
    ) -> UserDocument:
        timestamp = now or utc_now()
        document = UserDocument(
            _id=ObjectId(),
            email=canonicalize_email(email),
            password_hash=password_hash,
            role=role,
            created_at=timestamp,
            updated_at=timestamp,
        )
        persisted = document.model_dump(by_alias=True)
        # ObjectIdValue serializes for API-shaped dumps; MongoDB must retain BSON ObjectId.
        persisted["_id"] = document.id
        try:
            await self._collection.insert_one(persisted)
        except DuplicateKeyError as error:
            raise RepositoryConflictError("user email already exists") from error
        return document


def _object_id(value: ObjectId | str) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(value)
    except (InvalidId, TypeError) as error:
        raise InvalidIdentifierError("invalid user identifier") from error
