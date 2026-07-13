"""MongoDB persistence for card-to-public-link assignments."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from bson.errors import InvalidId
from bson.objectid import ObjectId
from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from emercard.core.config import Settings
from emercard.core.types import utc_now
from emercard.db.repositories import InvalidIdentifierError, RepositoryConflictError
from emercard.modules.card_link_assignments.models import (
    CardLinkAssignmentDocument,
    CardLinkAssignmentStatus,
)


class CardLinkAssignmentRepository:
    def __init__(self, database: Any, settings: Settings) -> None:
        self._database = database
        self._collection = database[settings.mongodb_card_link_assignments_collection]

    async def find_by_id(
        self, assignment_id: ObjectId | str, *, session: Any | None = None
    ) -> CardLinkAssignmentDocument | None:
        document = await self._collection.find_one(
            {"_id": _object_id(assignment_id)}, **_session_kwargs(session)
        )
        return _assignment(document)

    async def list_by_card_id(
        self, card_id: ObjectId | str, *, session: Any | None = None
    ) -> list[CardLinkAssignmentDocument]:
        cursor = self._collection.find(
            {"card_id": _object_id(card_id)}, **_session_kwargs(session)
        ).sort([("attached_at", DESCENDING), ("updated_at", DESCENDING), ("_id", DESCENDING)])
        return _assignments(await cursor.to_list(length=None))

    async def list_history_by_card_id(
        self, card_id: ObjectId | str, *, session: Any | None = None
    ) -> list[CardLinkAssignmentDocument]:
        return await self.list_by_card_id(card_id, session=session)

    async def list_by_public_access_link_id(
        self, public_access_link_id: ObjectId | str, *, session: Any | None = None
    ) -> list[CardLinkAssignmentDocument]:
        cursor = self._collection.find(
            {"public_access_link_id": _object_id(public_access_link_id)},
            **_session_kwargs(session),
        ).sort([("attached_at", DESCENDING), ("updated_at", DESCENDING), ("_id", DESCENDING)])
        return _assignments(await cursor.to_list(length=None))

    async def list_history_by_public_access_link_id(
        self, public_access_link_id: ObjectId | str, *, session: Any | None = None
    ) -> list[CardLinkAssignmentDocument]:
        return await self.list_by_public_access_link_id(public_access_link_id, session=session)

    async def find_active_by_card_id(
        self, card_id: ObjectId | str, *, session: Any | None = None
    ) -> CardLinkAssignmentDocument | None:
        document = await self._collection.find_one(
            {"card_id": _object_id(card_id), "status": CardLinkAssignmentStatus.ACTIVE},
            **_session_kwargs(session),
        )
        return _assignment(document)

    async def find_active_by_public_access_link_id(
        self, public_access_link_id: ObjectId | str, *, session: Any | None = None
    ) -> CardLinkAssignmentDocument | None:
        document = await self._collection.find_one(
            {
                "public_access_link_id": _object_id(public_access_link_id),
                "status": CardLinkAssignmentStatus.ACTIVE,
            },
            **_session_kwargs(session),
        )
        return _assignment(document)

    async def attach_link(
        self,
        *,
        card_id: ObjectId | str,
        public_access_link_id: ObjectId | str,
        attached_by_admin_id: ObjectId | str | None = None,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardLinkAssignmentDocument:
        timestamp = now or utc_now()
        card_identifier = _object_id(card_id)
        link_identifier = _object_id(public_access_link_id)
        if await self.find_active_by_card_id(card_identifier, session=session) is not None:
            raise RepositoryConflictError("card already has an active link assignment")
        if (
            await self.find_active_by_public_access_link_id(
                link_identifier, session=session
            )
            is not None
        ):
            raise RepositoryConflictError("public access link is already assigned")
        document = CardLinkAssignmentDocument(
            _id=ObjectId(),
            card_id=card_identifier,
            public_access_link_id=link_identifier,
            status=CardLinkAssignmentStatus.ACTIVE,
            attached_at=timestamp,
            updated_at=timestamp,
            attached_by_admin_id=_optional_object_id(attached_by_admin_id),
        )
        persisted = _persisted(document)
        try:
            await self._collection.insert_one(persisted, **_session_kwargs(session))
        except DuplicateKeyError as error:
            raise RepositoryConflictError("card link assignment already exists") from error
        return document

    async def disable_assignment(
        self,
        *,
        assignment_id: ObjectId | str,
        disabled_by_admin_id: ObjectId | str | None = None,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardLinkAssignmentDocument | None:
        timestamp = now or utc_now()
        document = await self._collection.find_one_and_update(
            {"_id": _object_id(assignment_id), "status": CardLinkAssignmentStatus.ACTIVE},
            {
                "$set": {
                    "status": CardLinkAssignmentStatus.DISABLED,
                    "updated_at": timestamp,
                    "disabled_at": timestamp,
                    "disabled_by_admin_id": _optional_object_id(disabled_by_admin_id),
                }
            },
            return_document=ReturnDocument.AFTER,
            **_session_kwargs(session),
        )
        return _assignment(document)

    async def activate_assignment(
        self,
        *,
        assignment_id: ObjectId | str,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardLinkAssignmentDocument | None:
        timestamp = now or utc_now()
        document = await self._collection.find_one_and_update(
            {"_id": _object_id(assignment_id), "status": CardLinkAssignmentStatus.DISABLED},
            {
                "$set": {
                    "status": CardLinkAssignmentStatus.ACTIVE,
                    "updated_at": timestamp,
                    "disabled_at": None,
                    "disabled_by_admin_id": None,
                }
            },
            return_document=ReturnDocument.AFTER,
            **_session_kwargs(session),
        )
        return _assignment(document)

    async def deactivate_assignment(
        self,
        *,
        assignment_id: ObjectId | str,
        disabled_by_admin_id: ObjectId | str | None = None,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardLinkAssignmentDocument | None:
        return await self.disable_assignment(
            assignment_id=assignment_id,
            disabled_by_admin_id=disabled_by_admin_id,
            now=now,
            session=session,
        )

    async def detach_assignment(
        self,
        *,
        assignment_id: ObjectId | str,
        detached_by_admin_id: ObjectId | str | None = None,
        detach_reason: str | None = None,
        now: datetime | None = None,
        session: Any | None = None,
    ) -> CardLinkAssignmentDocument | None:
        timestamp = now or utc_now()
        reason = detach_reason.strip() if detach_reason is not None else None
        if reason == "":
            reason = None
        document = await self._collection.find_one_and_update(
            {
                "_id": _object_id(assignment_id),
                "status": {
                    "$in": [CardLinkAssignmentStatus.ACTIVE, CardLinkAssignmentStatus.DISABLED]
                },
            },
            {
                "$set": {
                    "status": CardLinkAssignmentStatus.DETACHED,
                    "updated_at": timestamp,
                    "detached_at": timestamp,
                    "detached_by_admin_id": _optional_object_id(detached_by_admin_id),
                    "detach_reason": reason,
                }
            },
            return_document=ReturnDocument.AFTER,
            **_session_kwargs(session),
        )
        return _assignment(document)

    async def with_transaction(self, operation: Callable[[Any], Awaitable[Any]]) -> Any:
        client = self._database.client
        async with client.start_session() as session, session.start_transaction():
            return await operation(session)


def _assignment(document: Any) -> CardLinkAssignmentDocument | None:
    return CardLinkAssignmentDocument.model_validate(document) if document is not None else None


def _assignments(documents: list[Any]) -> list[CardLinkAssignmentDocument]:
    return [CardLinkAssignmentDocument.model_validate(document) for document in documents]


def _object_id(value: ObjectId | str) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(value)
    except (InvalidId, TypeError) as error:
        raise InvalidIdentifierError("invalid card link assignment identifier") from error


def _optional_object_id(value: ObjectId | str | None) -> ObjectId | None:
    if value is None:
        return None
    return _object_id(value)


def _persisted(document: CardLinkAssignmentDocument) -> dict[str, Any]:
    return document.model_dump(mode="python", by_alias=True)


def _session_kwargs(session: Any | None) -> dict[str, Any]:
    return {"session": session} if session is not None else {}
