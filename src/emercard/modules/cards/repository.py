"""Typed, owner-aware MongoDB operations for physical cards."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, cast

from bson.errors import InvalidId
from bson.objectid import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from emercard.core.config import Settings
from emercard.core.types import utc_now
from emercard.db.repositories import InvalidIdentifierError
from emercard.modules.cards.errors import (
    CardError,
    CardIdentityConflictError,
    CardInvariantError,
    CardSerialConflictError,
    CardTokenHashConflictError,
)
from emercard.modules.cards.identity import normalize_serial, validate_token_hash
from emercard.modules.cards.models import CardDocument, CardStatus


class CardRepository:
    """Persist cards without owning authentication or lifecycle policy."""

    def __init__(self, database: Any, settings: Settings) -> None:
        self._database = database
        self._collection = database[settings.mongodb_cards_collection]

    async def create_unassigned_card(
        self,
        *,
        serial: str,
        token_hash: str,
        replaces_card_id: ObjectId | str | None = None,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument:
        timestamp = now or utc_now()
        canonical_serial = normalize_serial(serial)
        canonical_hash = validate_token_hash(token_hash)
        replacement_id = _optional_object_id(replaces_card_id)
        document = CardDocument(
            _id=ObjectId(),
            serial=canonical_serial,
            owner_id=None,
            token_hash=canonical_hash,
            status=CardStatus.UNASSIGNED,
            is_current=False,
            replaces_card_id=replacement_id,
            created_at=timestamp,
            updated_at=timestamp,
        )
        persisted = _persisted(document)
        try:
            await self._collection.insert_one(persisted, **_session_kwargs(session))
        except DuplicateKeyError as error:
            raise _identity_conflict(error) from error
        return document

    async def find_by_id(
        self, card_id: ObjectId | str, *, session: Any | None = None
    ) -> CardDocument | None:
        identifier = _object_id(card_id)
        document = await self._collection.find_one({"_id": identifier}, **_session_kwargs(session))
        return _card(document)

    async def find_by_serial(
        self, serial: str, *, session: Any | None = None
    ) -> CardDocument | None:
        try:
            canonical_serial = normalize_serial(serial)
        except CardInvariantError as error:
            raise InvalidIdentifierError("invalid card serial") from error
        document = await self._collection.find_one(
            {"serial": canonical_serial}, **_session_kwargs(session)
        )
        return _card(document)

    async def find_by_token_hash(
        self, token_hash: str, *, session: Any | None = None
    ) -> CardDocument | None:
        try:
            canonical_hash = validate_token_hash(token_hash)
        except CardInvariantError as error:
            raise InvalidIdentifierError("invalid card token hash") from error
        document = await self._collection.find_one(
            {"token_hash": canonical_hash}, **_session_kwargs(session)
        )
        return _card(document)

    async def list_for_user(
        self, user_id: ObjectId | str, *, session: Any | None = None
    ) -> list[CardDocument]:
        identifier = _object_id(user_id)
        cursor = self._collection.find({"owner_id": identifier}, **_session_kwargs(session))
        return _cards(await cursor.to_list(length=None))

    async def list_current_for_user(
        self, user_id: ObjectId | str, *, session: Any | None = None
    ) -> list[CardDocument]:
        identifier = _object_id(user_id)
        cursor = self._collection.find(
            {"owner_id": identifier, "is_current": True}, **_session_kwargs(session)
        )
        return _cards(await cursor.to_list(length=None))

    async def list_active_for_user(
        self, user_id: ObjectId | str, *, session: Any | None = None
    ) -> list[CardDocument]:
        identifier = _object_id(user_id)
        cursor = self._collection.find(
            {"owner_id": identifier, "status": CardStatus.ACTIVE},
            **_session_kwargs(session),
        )
        return _cards(await cursor.to_list(length=None))

    async def assign_to_user(
        self,
        *,
        card_id: ObjectId | str,
        user_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None:
        identifier = _object_id(card_id)
        owner_id = _object_id(user_id)
        timestamp = now or utc_now()
        document = await self._collection.find_one_and_update(
            {"_id": identifier, "status": CardStatus.UNASSIGNED, "owner_id": None},
            {
                "$set": {
                    "owner_id": owner_id,
                    "status": CardStatus.ASSIGNED,
                    "is_current": True,
                    "assigned_at": timestamp,
                    "updated_at": timestamp,
                }
            },
            return_document=ReturnDocument.AFTER,
            **_session_kwargs(session),
        )
        return _card(document)

    async def transition_status(
        self,
        *,
        card_id: ObjectId | str,
        from_statuses: set[CardStatus],
        to_status: CardStatus,
        owner_id: ObjectId | str | None = None,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None:
        operational_statuses = {
            CardStatus.ASSIGNED,
            CardStatus.ACTIVE,
            CardStatus.DISABLED,
        }
        terminal_statuses = {CardStatus.LOST, CardStatus.REPLACED}
        if not from_statuses or not from_statuses.issubset(operational_statuses):
            raise CardInvariantError("card transition requires an operational source status")
        if to_status not in operational_statuses | terminal_statuses:
            raise CardInvariantError("card transition has an invalid target status")
        identifier = _object_id(card_id)
        timestamp = now or utc_now()
        query: dict[str, Any] = {"_id": identifier, "status": {"$in": list(from_statuses)}}
        if owner_id is not None:
            query["owner_id"] = _object_id(owner_id)
        update_fields: dict[str, Any] = {
            "status": to_status,
            "is_current": to_status
            in {
                CardStatus.ASSIGNED,
                CardStatus.ACTIVE,
                CardStatus.DISABLED,
            },
            "updated_at": timestamp,
        }
        timestamp_field = {
            CardStatus.ASSIGNED: "assigned_at",
            CardStatus.ACTIVE: "activated_at",
            CardStatus.DISABLED: "disabled_at",
            CardStatus.LOST: "lost_at",
            CardStatus.REPLACED: "replaced_at",
        }.get(to_status)
        if timestamp_field is not None:
            update_fields[timestamp_field] = timestamp
        document = await self._collection.find_one_and_update(
            query,
            {"$set": update_fields},
            return_document=ReturnDocument.AFTER,
            **_session_kwargs(session),
        )
        return _card(document)

    async def mark_lost(
        self,
        *,
        card_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None:
        return await self.transition_status(
            card_id=card_id,
            from_statuses={CardStatus.ASSIGNED, CardStatus.ACTIVE, CardStatus.DISABLED},
            to_status=CardStatus.LOST,
            now=now,
            session=session,
        )

    async def mark_replaced(
        self,
        *,
        card_id: ObjectId | str,
        owner_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None:
        return await self.transition_status(
            card_id=card_id,
            from_statuses={CardStatus.ASSIGNED, CardStatus.ACTIVE, CardStatus.DISABLED},
            to_status=CardStatus.REPLACED,
            owner_id=owner_id,
            now=now,
            session=session,
        )

    async def link_replacement(
        self,
        *,
        card_id: ObjectId | str,
        replacement_card_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardDocument | None:
        identifier = _object_id(card_id)
        replacement_id = _object_id(replacement_card_id)
        if identifier == replacement_id:
            raise CardInvariantError("replacement references cannot point to the same card")
        timestamp = now or utc_now()
        document = await self._collection.find_one_and_update(
            {"_id": identifier, "status": CardStatus.REPLACED, "is_current": False},
            {"$set": {"replacement_card_id": replacement_id, "updated_at": timestamp}},
            return_document=ReturnDocument.AFTER,
            **_session_kwargs(session),
        )
        return _card(document)

    async def with_transaction(self, operation: Callable[[Any], Awaitable[Any]]) -> Any:
        """Run a repository operation in a MongoDB transaction."""

        client = self._database.client
        async with await client.start_session() as session, session.start_transaction():
            return await operation(session)


def _identity_conflict(error: DuplicateKeyError) -> CardError:
    details = cast(dict[str, Any], getattr(error, "details", None) or {})
    key_pattern = cast(dict[str, Any], details.get("keyPattern", {}))
    index_name = str(details.get("indexName", ""))
    fields = set(key_pattern)
    error_text = str(error)
    if "serial" in fields or "serial" in index_name or "serial" in error_text:
        return CardSerialConflictError("card serial already exists")
    if "token_hash" in fields or "token_hash" in index_name or "token_hash" in error_text:
        return CardTokenHashConflictError("card token hash already exists")
    return CardIdentityConflictError("card identity already exists")


def _persisted(document: CardDocument) -> dict[str, Any]:
    persisted = document.model_dump(by_alias=True, mode="python")
    for field in (
        "_id",
        "owner_id",
        "replaces_card_id",
        "replacement_card_id",
    ):
        value = getattr(document, field.removeprefix("_") if field == "_id" else field)
        persisted[field] = value
    return persisted


def _card(document: Any) -> CardDocument | None:
    return CardDocument.model_validate(document) if document is not None else None


def _cards(documents: list[Any]) -> list[CardDocument]:
    cards = [_card(document) for document in documents]
    return [card for card in cards if card is not None]


def _session_kwargs(session: Any | None) -> dict[str, Any]:
    return {"session": session} if session is not None else {}


def _object_id(value: ObjectId | str) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(value)
    except (InvalidId, TypeError) as error:
        raise InvalidIdentifierError("invalid card identifier") from error


def _optional_object_id(value: ObjectId | str | None) -> ObjectId | None:
    return None if value is None else _object_id(value)
