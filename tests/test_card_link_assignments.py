from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from emercard.core.config import Settings
from emercard.db.repositories import RepositoryConflictError
from emercard.modules.card_link_assignments import (
    CardLinkAssignmentDocument,
    CardLinkAssignmentRepository,
    CardLinkAssignmentStatus,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
CARD_ID = ObjectId("507f1f77bcf86cd799439011")
LINK_ID = ObjectId("507f1f77bcf86cd799439012")
ADMIN_ID = ObjectId("507f1f77bcf86cd799439013")


def assignment_document(
    *,
    status: CardLinkAssignmentStatus = CardLinkAssignmentStatus.ACTIVE,
    detach_reason: str | None = None,
) -> CardLinkAssignmentDocument:
    return CardLinkAssignmentDocument(
        _id=ObjectId(),
        card_id=CARD_ID,
        public_access_link_id=LINK_ID,
        status=status,
        attached_at=NOW,
        updated_at=NOW,
        attached_by_admin_id=ADMIN_ID,
        disabled_at=NOW if status is CardLinkAssignmentStatus.DISABLED else None,
        disabled_by_admin_id=ADMIN_ID if status is CardLinkAssignmentStatus.DISABLED else None,
        detached_at=NOW if status is CardLinkAssignmentStatus.DETACHED else None,
        detached_by_admin_id=ADMIN_ID if status is CardLinkAssignmentStatus.DETACHED else None,
        detach_reason=detach_reason if status is CardLinkAssignmentStatus.DETACHED else None,
    )


def test_assignment_document_validates_terminal_fields() -> None:
    active = assignment_document()
    disabled = assignment_document(status=CardLinkAssignmentStatus.DISABLED)
    detached = assignment_document(
        status=CardLinkAssignmentStatus.DETACHED, detach_reason="replaced"
    )

    assert active.status is CardLinkAssignmentStatus.ACTIVE
    assert disabled.disabled_at is not None
    assert detached.detach_reason == "replaced"

    with pytest.raises(ValueError, match="detached assignments must include a detach reason"):
        CardLinkAssignmentDocument(
            _id=ObjectId(),
            card_id=CARD_ID,
            public_access_link_id=LINK_ID,
            status=CardLinkAssignmentStatus.DETACHED,
            attached_at=NOW,
            updated_at=NOW,
            detached_at=NOW,
        )


@pytest.mark.asyncio
async def test_repository_attach_link_persists_only_assignment_fields() -> None:
    database = MagicMock()
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=None)
    collection.insert_one = AsyncMock()
    database.__getitem__.return_value = collection
    repository = CardLinkAssignmentRepository(database, Settings(environment="test"))

    assignment = await repository.attach_link(
        card_id=CARD_ID,
        public_access_link_id=LINK_ID,
        attached_by_admin_id=ADMIN_ID,
        now=NOW,
    )

    persisted = collection.insert_one.await_args.args[0]
    assert assignment.status is CardLinkAssignmentStatus.ACTIVE
    assert persisted["card_id"] == CARD_ID
    assert persisted["public_access_link_id"] == LINK_ID
    assert persisted["status"] == CardLinkAssignmentStatus.ACTIVE
    assert "public_token" not in persisted
    assert "token_hash" not in persisted


@pytest.mark.asyncio
async def test_repository_attach_link_translates_duplicate_keys() -> None:
    database = MagicMock()
    collection = MagicMock()
    collection.find_one = AsyncMock(side_effect=[None, None])
    collection.insert_one = AsyncMock(side_effect=DuplicateKeyError("dup"))
    database.__getitem__.return_value = collection
    repository = CardLinkAssignmentRepository(database, Settings(environment="test"))

    with pytest.raises(RepositoryConflictError):
        await repository.attach_link(card_id=CARD_ID, public_access_link_id=LINK_ID, now=NOW)


@pytest.mark.asyncio
async def test_repository_lists_and_transitions_assignment_state() -> None:
    database = MagicMock()
    collection = MagicMock()
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[assignment_document(), assignment_document()])
    collection.find.return_value = cursor
    collection.find_one = AsyncMock(return_value=assignment_document())
    collection.find_one_and_update = AsyncMock(
        return_value=assignment_document(status=CardLinkAssignmentStatus.DISABLED)
    )
    database.__getitem__.return_value = collection
    repository = CardLinkAssignmentRepository(database, Settings(environment="test"))

    by_card = await repository.list_by_card_id(CARD_ID)
    history = await repository.list_history_by_card_id(CARD_ID)
    active = await repository.find_active_by_card_id(CARD_ID)
    disabled = await repository.disable_assignment(assignment_id=ObjectId(), now=NOW)
    deactivated = await repository.deactivate_assignment(assignment_id=ObjectId(), now=NOW)

    assert len(by_card) == 2
    assert len(history) == 2
    assert active is not None
    assert disabled is not None
    assert deactivated is not None
    collection.find_one_and_update.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("find_one_side_effect", "message"),
    [
        ([assignment_document(), None], "card already has an active link assignment"),
        ([None, assignment_document()], "public access link is already assigned"),
    ],
)
async def test_repository_attach_link_rejects_existing_active_assignment(
    find_one_side_effect: list[CardLinkAssignmentDocument | None],
    message: str,
) -> None:
    database = MagicMock()
    collection = MagicMock()
    collection.find_one = AsyncMock(side_effect=find_one_side_effect)
    collection.insert_one = AsyncMock()
    database.__getitem__.return_value = collection
    repository = CardLinkAssignmentRepository(database, Settings(environment="test"))

    with pytest.raises(RepositoryConflictError, match=message):
        await repository.attach_link(card_id=CARD_ID, public_access_link_id=LINK_ID, now=NOW)

    collection.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_repository_with_transaction_runs_operation_in_a_session() -> None:
    class FakeTransaction:
        async def __aenter__(self) -> FakeTransaction:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    class FakeSession:
        def __init__(self) -> None:
            self.started = False

        async def start_transaction(self) -> FakeTransaction:
            self.started = True
            return FakeTransaction()

    class FakeSessionContext:
        def __init__(self, session: FakeSession) -> None:
            self._session = session

        async def __aenter__(self) -> FakeSession:
            return self._session

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    class FakeClient:
        def __init__(self, session: FakeSession) -> None:
            self._session = session

        def start_session(self) -> FakeSessionContext:
            return FakeSessionContext(self._session)

    database = MagicMock()
    collection = MagicMock()
    database.__getitem__.return_value = collection
    fake_session = FakeSession()
    database.client = FakeClient(fake_session)
    repository = CardLinkAssignmentRepository(database, Settings(environment="test"))

    async def operation(session: object) -> str:
        assert session is fake_session
        return "ok"

    assert await repository.with_transaction(operation) == "ok"
    assert fake_session.started is True
