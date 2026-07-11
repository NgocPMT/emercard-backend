"""Persistence helpers for admin idempotency and append-only custody events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bson.objectid import ObjectId
from pymongo.errors import DuplicateKeyError

from emercard.core.config import Settings
from emercard.db.repositories import RepositoryConflictError


class IdempotencyRepository:
    def __init__(self, database: Any, settings: Settings) -> None:
        self._collection = database[settings.mongodb_idempotency_collection]

    async def find_card_id(
        self, operation_key: str, *, session: Any | None = None
    ) -> ObjectId | None:
        document = await self._collection.find_one(
            {"operation_key": operation_key}, **_session_kwargs(session)
        )
        if document is None:
            return None
        return document["card_id"]

    async def save_card_id(
        self,
        *,
        operation_key: str,
        card_id: ObjectId,
        now: datetime,
        session: Any | None = None,
    ) -> None:
        try:
            await self._collection.insert_one(
                {
                    "_id": ObjectId(),
                    "operation_key": operation_key,
                    "card_id": card_id,
                    "created_at": now,
                },
                **_session_kwargs(session),
            )
        except DuplicateKeyError as error:
            raise RepositoryConflictError("idempotency key already exists") from error


class CustodyEventRepository:
    def __init__(self, database: Any, settings: Settings) -> None:
        self._collection = database[settings.mongodb_custody_events_collection]

    async def append(
        self,
        *,
        card_id: ObjectId,
        event_type: str,
        previous_owner_id: ObjectId | None,
        new_owner_id: ObjectId | None,
        performed_by_admin_id: ObjectId,
        reason: str | None,
        now: datetime,
        session: Any | None = None,
    ) -> ObjectId:
        event_id = ObjectId()
        await self._collection.insert_one(
            {
                "_id": event_id,
                "card_id": card_id,
                "event_type": event_type,
                "previous_owner_id": previous_owner_id,
                "new_owner_id": new_owner_id,
                "performed_by_admin_id": performed_by_admin_id,
                "reason": reason,
                "created_at": now,
            },
            **_session_kwargs(session),
        )
        return event_id


def _session_kwargs(session: Any | None) -> dict[str, Any]:
    return {"session": session} if session is not None else {}
