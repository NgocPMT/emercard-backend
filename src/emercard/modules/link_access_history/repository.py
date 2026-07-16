"""MongoDB persistence for minimized public-link access events."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timedelta
from typing import Any

from bson.errors import InvalidId
from bson.objectid import ObjectId
from pymongo import DESCENDING

from emercard.core.config import Settings
from emercard.core.types import utc_now
from emercard.db.repositories import InvalidIdentifierError
from emercard.modules.link_access_history.models import LinkAccessEventDocument


class LinkAccessHistoryRepository:
    """Persist and page successful card-backed public-link accesses."""

    def __init__(self, database: Any, settings: Settings) -> None:
        self._collection = database[settings.mongodb_link_access_events_collection]
        self._retention = timedelta(seconds=settings.link_access_history_retention_seconds)

    async def append(
        self,
        *,
        card_id: ObjectId | str,
        public_access_link_id: ObjectId | str,
        accessed_at: datetime | None = None,
    ) -> LinkAccessEventDocument:
        timestamp = accessed_at or utc_now()
        document = LinkAccessEventDocument.model_validate(
            {
                "_id": ObjectId(),
                "card_id": _object_id(card_id),
                "public_access_link_id": _object_id(public_access_link_id),
                "accessed_at": timestamp,
                "expires_at": timestamp + self._retention,
            }
        )
        await self._collection.insert_one(_persisted(document))
        return document

    async def list_by_card_and_link(
        self,
        *,
        card_id: ObjectId | str,
        public_access_link_id: ObjectId | str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[LinkAccessEventDocument], str | None]:
        if limit < 1 or limit > 100:
            raise ValueError("access history limit must be between 1 and 100")
        card_identifier = _object_id(card_id)
        link_identifier = _object_id(public_access_link_id)
        query: dict[str, Any] = {
            "card_id": card_identifier,
            "public_access_link_id": link_identifier,
        }
        if cursor is not None:
            accessed_at, event_id = _decode_cursor(cursor)
            query["$or"] = [
                {"accessed_at": {"$lt": accessed_at}},
                {"accessed_at": accessed_at, "_id": {"$lt": event_id}},
            ]
        records = await (
            self._collection.find(query)
            .sort([("accessed_at", DESCENDING), ("_id", DESCENDING)])
            .to_list(length=limit + 1)
        )
        events = [_event(record) for record in records]
        events = [item for item in events if item is not None]
        next_cursor = None
        if len(events) > limit:
            page = events[:limit]
            last = page[-1]
            next_cursor = _encode_cursor(last.accessed_at, last.id)
            events = page
        return events, next_cursor


def _encode_cursor(accessed_at: datetime, event_id: ObjectId) -> str:
    payload = json.dumps(
        {"accessed_at": accessed_at.isoformat(), "id": str(event_id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, ObjectId]:
    if not value or len(value) > 512:
        raise ValueError("invalid access history cursor")
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(value + padding).decode("utf-8"))
        accessed_at = datetime.fromisoformat(str(payload["accessed_at"]))
        event_id = _object_id(str(payload["id"]))
    except (
        binascii.Error,
        UnicodeError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise ValueError("invalid access history cursor") from error
    if accessed_at.tzinfo is None or accessed_at.utcoffset() is None:
        raise ValueError("invalid access history cursor")
    return accessed_at, event_id


def _event(document: Any) -> LinkAccessEventDocument | None:
    return LinkAccessEventDocument.model_validate(document) if document is not None else None


def _object_id(value: ObjectId | str) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(value)
    except (InvalidId, TypeError) as error:
        raise InvalidIdentifierError("invalid link access history identifier") from error


def _persisted(document: LinkAccessEventDocument) -> dict[str, Any]:
    persisted = document.model_dump(mode="python", by_alias=True)
    persisted["_id"] = document.id
    persisted["card_id"] = document.card_id
    persisted["public_access_link_id"] = document.public_access_link_id
    return persisted
