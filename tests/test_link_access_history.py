from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from bson import ObjectId

from emercard.modules.card_link_assignments import (
    CardLinkAssignmentDocument,
    CardLinkAssignmentStatus,
)
from emercard.modules.cards import hash_public_token
from emercard.modules.link_access_history import LinkAccessHistoryRepository
from emercard.modules.profiles import ProfileDocument
from emercard.modules.public_links import (
    PublicAccessLinkDocument,
    PublicAccessLinkStatus,
    PublicLinkPurpose,
    PublicProfileDisabledError,
    PublicProfileLookupService,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
PROFILE_ID = ObjectId("507f1f77bcf86cd799439012")
CARD_ID = ObjectId("507f1f77bcf86cd799439014")
LINK_ID = ObjectId("507f1f77bcf86cd799439013")
TOKEN = "public-demo-token_123"


class Cursor:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records

    def sort(self, fields: list[tuple[str, int]]) -> Cursor:
        for field, direction in reversed(fields):
            self.records.sort(
                key=lambda record: (record[field], record["_id"]), reverse=direction < 0
            )
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self.records if length is None else self.records[:length]


class Collection:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def insert_one(self, record: dict[str, Any]) -> None:
        self.records.append(record)

    def find(self, query: dict[str, Any]) -> Cursor:
        def matches(record: dict[str, Any]) -> bool:
            if "$or" in query:
                base = {key: value for key, value in query.items() if key != "$or"}
                if not _matches_condition(record, base):
                    return False
                return any(_matches_condition(record, condition) for condition in query["$or"])
            return all(_matches_condition(record, {key: value}) for key, value in query.items())

        return Cursor([record for record in self.records if matches(record)])


def _matches_condition(record: dict[str, Any], condition: dict[str, Any]) -> bool:
    for field, expected in condition.items():
        actual = record[field]
        if isinstance(expected, dict):
            if "$lt" in expected and not actual < expected["$lt"]:
                return False
        elif actual != expected:
            return False
    return True


class Database:
    def __init__(self, collection: Collection) -> None:
        self.collection = collection

    def __getitem__(self, name: str) -> Collection:
        return self.collection


class Settings:
    mongodb_link_access_events_collection = "link_access_events"
    link_access_history_retention_seconds = 7_776_000


@pytest.mark.asyncio
async def test_repository_appends_minimized_events_and_pages() -> None:
    collection = Collection()
    repository = LinkAccessHistoryRepository(Database(collection), Settings())
    first = await repository.append(card_id=CARD_ID, public_access_link_id=LINK_ID, accessed_at=NOW)
    second = await repository.append(
        card_id=CARD_ID,
        public_access_link_id=LINK_ID,
        accessed_at=NOW + timedelta(minutes=1),
    )

    events, cursor = await repository.list_by_card_and_link(
        card_id=CARD_ID,
        public_access_link_id=LINK_ID,
        limit=1,
    )

    assert events == [second]
    assert cursor is not None
    assert set(collection.records[0]) == {
        "_id",
        "card_id",
        "public_access_link_id",
        "accessed_at",
        "expires_at",
    }
    assert collection.records[0]["expires_at"] == NOW + timedelta(
        seconds=Settings.link_access_history_retention_seconds
    )
    remaining, next_cursor = await repository.list_by_card_and_link(
        card_id=CARD_ID,
        public_access_link_id=LINK_ID,
        limit=1,
        cursor=cursor,
    )
    assert remaining == [first]
    assert next_cursor is None


class LinkRepository:
    async def find_by_token_hash(self, token_hash: str, *, session: object | None = None) -> Any:
        return self.link if token_hash == hash_public_token(TOKEN) else None

    def __init__(self, link: PublicAccessLinkDocument) -> None:
        self.link = link


class ProfileRepository:
    async def find_by_id(self, profile_id: ObjectId | str) -> ProfileDocument:
        return ProfileDocument.model_validate(
            {
                "_id": PROFILE_ID,
                "user_id": ObjectId("507f1f77bcf86cd799439011"),
                "display_name": "Alex Example",
                "birth_year": 1995,
                "gender": "male",
                "blood_type": "O+",
                "critical_allergies": ["Penicillin"],
                "important_conditions": [],
                "critical_medications": [],
                "emergency_note": None,
                "emergency_contacts": [],
                "public_access": {
                    "token": "legacy-secret",
                    "enabled": True,
                    "published_at": NOW,
                },
                "created_at": NOW,
                "updated_at": NOW,
            }
        )


class AssignmentRepository:
    async def find_active_by_public_access_link_id(
        self, public_access_link_id: ObjectId | str, *, session: object | None = None
    ) -> CardLinkAssignmentDocument:
        return CardLinkAssignmentDocument.model_validate(
            {
                "_id": ObjectId("507f1f77bcf86cd799439015"),
                "card_id": CARD_ID,
                "public_access_link_id": LINK_ID,
                "status": CardLinkAssignmentStatus.ACTIVE,
                "attached_at": NOW,
                "updated_at": NOW,
            }
        )


class HistoryRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def append(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_successful_card_link_lookup_records_one_server_timestamped_event() -> None:
    link = PublicAccessLinkDocument.model_validate(
        {
            "_id": LINK_ID,
            "profile_id": PROFILE_ID,
            "purpose": PublicLinkPurpose.CARD,
            "token_hash": hash_public_token(TOKEN),
            "status": PublicAccessLinkStatus.ACTIVE,
            "created_at": NOW,
            "updated_at": NOW,
            "activated_at": NOW,
        }
    )
    history = HistoryRepository()
    result = await PublicProfileLookupService(
        LinkRepository(link),
        ProfileRepository(),
        assignment_repository=AssignmentRepository(),
        access_history_repository=history,
    ).lookup(TOKEN)

    assert result.card_id == str(CARD_ID)
    assert len(history.calls) == 1
    assert history.calls[0]["card_id"] == CARD_ID
    assert history.calls[0]["public_access_link_id"] == LINK_ID
    assert history.calls[0]["accessed_at"].tzinfo is UTC


@pytest.mark.asyncio
async def test_invalid_cursor_is_rejected() -> None:
    repository = LinkAccessHistoryRepository(Database(Collection()), Settings())
    with pytest.raises(ValueError, match="cursor"):
        await repository.list_by_card_and_link(
            card_id=CARD_ID,
            public_access_link_id=LINK_ID,
            cursor="not-a-valid-cursor",
        )


@pytest.mark.asyncio
async def test_inactive_link_does_not_write_access_history() -> None:
    link = PublicAccessLinkDocument.model_validate(
        {
            "_id": LINK_ID,
            "profile_id": PROFILE_ID,
            "purpose": PublicLinkPurpose.CARD,
            "token_hash": hash_public_token(TOKEN),
            "status": PublicAccessLinkStatus.DISABLED,
            "created_at": NOW,
            "updated_at": NOW,
            "disabled_at": NOW,
        }
    )
    history = HistoryRepository()
    with pytest.raises(PublicProfileDisabledError):
        await PublicProfileLookupService(
            LinkRepository(link),
            ProfileRepository(),
            assignment_repository=AssignmentRepository(),
            access_history_repository=history,
        ).lookup(TOKEN)
    assert history.calls == []


@pytest.mark.asyncio
async def test_access_history_failure_does_not_block_public_lookup() -> None:
    link = PublicAccessLinkDocument.model_validate(
        {
            "_id": LINK_ID,
            "profile_id": PROFILE_ID,
            "purpose": PublicLinkPurpose.CARD,
            "token_hash": hash_public_token(TOKEN),
            "status": PublicAccessLinkStatus.ACTIVE,
            "created_at": NOW,
            "updated_at": NOW,
            "activated_at": NOW,
        }
    )

    class FailingHistory:
        async def append(self, **kwargs: Any) -> None:
            raise RuntimeError("database unavailable")

    result = await PublicProfileLookupService(
        LinkRepository(link),
        ProfileRepository(),
        assignment_repository=AssignmentRepository(),
        access_history_repository=FailingHistory(),
    ).lookup(TOKEN)

    assert result.card_id == str(CARD_ID)
